from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .backtest_intraday import _fetch_backtest_bars, _max_drawdown, _slice_until
from .intraday import _passes_entry_filter, _rank_scores, _size_position, _symbol_score
from .models import IntradayBar
from .universe import load_symbols


def _commission(quantity: int, execution: dict[str, Any]) -> float:
    return max(
        float(execution.get("minimum_commission", 0.0)),
        int(quantity) * float(execution.get("commission_per_share", 0.0)),
    )


def _slip_price(price: float, side: str, execution: dict[str, Any]) -> float:
    bps = float(execution.get("slippage_bps", 0.0)) / 10000
    if side == "buy":
        return price * (1 + bps)
    return price * (1 - bps)


def _format_money(value: float) -> str:
    return f"{value:,.2f}"


def run(config_path: Path) -> str:
    with config_path.open("r", encoding="utf-8") as file:
        recovery_config = json.load(file)

    base_path = Path(recovery_config["base_config"])
    if not base_path.is_absolute():
        base_path = config_path.parent / base_path
    with base_path.open("r", encoding="utf-8") as file:
        config = json.load(file)

    account = config["account"]
    strategy = config["strategy"]
    execution = recovery_config["execution"]
    overlay = recovery_config["scalp_overlay"]
    initial = recovery_config["initial"]
    recovery_symbol = str(initial["recovery_symbol"]).upper()
    recovery_qty = int(initial["recovery_quantity"])
    recovery_average = float(initial["recovery_average_price"])
    cash = float(initial.get("cash_usd", 0.0))
    range_days = int(recovery_config.get("range_days", 60))

    symbols = load_symbols(strategy, base_path.parent)
    if recovery_symbol not in symbols:
        symbols.append(recovery_symbol)
    bars, errors = _fetch_backtest_bars(symbols, strategy, range_days)
    timestamps = sorted(set.intersection(*(set(bar.timestamp for bar in symbol_bars) for symbol_bars in bars.values())))
    timestamps = timestamps[90:]
    if len(timestamps) < 20:
        raise RuntimeError(f"Not enough synchronized intraday bars: {len(timestamps)}")

    by_symbol_time = {
        symbol: {bar.timestamp: bar for bar in symbol_bars}
        for symbol, symbol_bars in bars.items()
    }
    first_price = by_symbol_time[recovery_symbol][timestamps[0]].close
    last_timestamp = timestamps[-1]
    book_cost = cash + (recovery_qty * recovery_average)
    starting_equity = cash + (recovery_qty * first_price)
    buy_hold_start = starting_equity
    recovery_ladder = [
        {
            "target_price": float(item["target_price"]),
            "quantity": int(item["quantity"]),
            "filled": False,
        }
        for item in recovery_config["recovery_sell_ladder"]
    ]

    scalp_position: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    equity_curve: list[float] = [starting_equity]
    trades_today = 0
    trade_day = None
    last_trade_at: datetime | None = None

    for index, timestamp in enumerate(timestamps[:-1]):
        next_timestamp = timestamps[index + 1]
        day = timestamp.date().isoformat()
        if day != trade_day:
            trade_day = day
            trades_today = 0

        sliced = {symbol: _slice_until(symbol_bars, timestamp) for symbol, symbol_bars in bars.items()}
        if any(len(symbol_bars) < 90 for symbol_bars in sliced.values()):
            continue
        scores = {symbol: _symbol_score(symbol_bars, strategy) for symbol, symbol_bars in sliced.items()}
        next_bar = by_symbol_time[recovery_symbol][next_timestamp]

        # Broker-side recovery limit sells are assumed to be resting before the next bar.
        for rung in recovery_ladder:
            if recovery_qty <= 0:
                break
            if rung["filled"]:
                continue
            target = float(rung["target_price"])
            if next_bar.high >= target:
                qty = min(int(rung["quantity"]), recovery_qty)
                fill_price = target
                fee = _commission(qty, execution)
                cash += (qty * fill_price) - fee
                recovery_qty -= qty
                rung["filled"] = True
                trades.append(
                    {
                        "timestamp": next_timestamp.isoformat(),
                        "kind": "recovery_sell",
                        "symbol": recovery_symbol,
                        "quantity": qty,
                        "price": round(fill_price, 2),
                        "fee": round(fee, 2),
                        "pnl": round((fill_price - recovery_average) * qty - fee, 2),
                        "reason": f"ladder {target:.2f}",
                    }
                )

        # Existing scalp overlay exits use the next bar. Stops are checked before targets.
        if scalp_position:
            bar = by_symbol_time[scalp_position["symbol"]][next_timestamp]
            exit_price = None
            exit_reason = None
            stop_floor = max(float(scalp_position["stop_price"]), float(scalp_position["trailing_stop_price"]))
            if bar.low <= stop_floor:
                exit_price = _slip_price(stop_floor, "sell", execution)
                exit_reason = "stop_or_trail"
            elif bar.high >= float(scalp_position["take_profit_price"]):
                exit_price = float(scalp_position["take_profit_price"])
                exit_reason = "take_profit"

            if exit_price is not None and exit_reason is not None:
                qty = int(scalp_position["quantity"])
                fee = _commission(qty, execution)
                cash += (qty * exit_price) - fee
                pnl = (exit_price - float(scalp_position["entry_price"])) * qty - fee
                trades.append(
                    {
                        "timestamp": next_timestamp.isoformat(),
                        "kind": "scalp_sell",
                        "symbol": scalp_position["symbol"],
                        "quantity": qty,
                        "price": round(exit_price, 2),
                        "fee": round(fee, 2),
                        "pnl": round(pnl, 2),
                        "reason": exit_reason,
                    }
                )
                scalp_position = None
                trades_today += 1
                last_trade_at = next_timestamp
            else:
                peak = max(float(scalp_position["peak_price"]), bar.high)
                scalp_position["peak_price"] = peak
                scalp_position["trailing_stop_price"] = max(
                    float(scalp_position["trailing_stop_price"]),
                    peak * (1 - float(strategy["trailing_stop_pct"])),
                )

        # New scalp entries are decided on the closed bar and filled at next open.
        if bool(overlay.get("enabled", True)) and scalp_position is None:
            in_cooldown = (
                last_trade_at is not None
                and (timestamp - last_trade_at).total_seconds() < int(strategy["cooldown_minutes"]) * 60
            )
            if trades_today < int(strategy["max_trades_per_day"]) and not in_cooldown:
                ranked = _rank_scores(scores)
                best_symbol, best = ranked[0]
                second_symbol, second = ranked[1] if len(ranked) > 1 else ("-", {"score": -99})
                recovery_open = recovery_qty > 0
                if recovery_open and best_symbol == recovery_symbol and not bool(overlay.get("allow_long_soxl_while_recovery_open", False)):
                    pass
                elif recovery_open and best_symbol != recovery_symbol and not bool(overlay.get("allow_inverse_etf_while_recovery_open", True)):
                    pass
                elif _passes_entry_filter(best, second, strategy):
                    entry_bar = by_symbol_time[best_symbol][next_timestamp]
                    entry_price = _slip_price(entry_bar.open, "buy", execution)
                    qty = _size_position(cash, entry_price, float(best["atr"]), account, strategy)
                    if qty > 0:
                        fee = _commission(qty, execution)
                        total_cost = qty * entry_price + fee
                        if total_cost <= cash:
                            cash -= total_cost
                            scalp_position = {
                                "symbol": best_symbol,
                                "quantity": qty,
                                "entry_price": entry_price,
                                "stop_price": entry_price * (1 - float(strategy["stop_loss_pct"])),
                                "take_profit_price": entry_price * (1 + float(strategy["take_profit_pct"])),
                                "trailing_stop_price": entry_price * (1 - float(strategy["trailing_stop_pct"])),
                                "peak_price": entry_price,
                                "opened_at": next_timestamp.isoformat(),
                            }
                            trades.append(
                                {
                                    "timestamp": next_timestamp.isoformat(),
                                    "kind": "scalp_buy",
                                    "symbol": best_symbol,
                                    "quantity": qty,
                                    "price": round(entry_price, 2),
                                    "fee": round(fee, 2),
                                    "pnl": "",
                                    "reason": f"score {best['score']} runner_up {second_symbol}:{second['score']}",
                                }
                            )

        current_recovery_price = by_symbol_time[recovery_symbol][next_timestamp].close
        equity = cash + (recovery_qty * current_recovery_price)
        if scalp_position:
            scalp_price = by_symbol_time[scalp_position["symbol"]][next_timestamp].close
            equity += int(scalp_position["quantity"]) * scalp_price
        equity_curve.append(equity)

    last_price = by_symbol_time[recovery_symbol][last_timestamp].close
    ending_equity = cash + (recovery_qty * last_price)
    if scalp_position:
        scalp_price = by_symbol_time[scalp_position["symbol"]][last_timestamp].close
        ending_equity += int(scalp_position["quantity"]) * scalp_price
    buy_hold_equity = float(initial.get("cash_usd", 0.0)) + int(initial["recovery_quantity"]) * last_price
    marked_pnl_vs_book = ending_equity - book_cost
    return_pct = (ending_equity / starting_equity - 1) if starting_equity else 0.0
    buy_hold_return = (buy_hold_equity / buy_hold_start - 1) if buy_hold_start else 0.0
    max_dd = _max_drawdown(equity_curve)
    closed_pnls = [float(trade["pnl"]) for trade in trades if isinstance(trade.get("pnl"), (int, float))]
    wins = [pnl for pnl in closed_pnls if pnl > 0]
    losses = [pnl for pnl in closed_pnls if pnl <= 0]

    report_path = Path(recovery_config.get("report_path", "reports/backtest_recovery_realistic.md"))
    if not report_path.is_absolute():
        report_path = config_path.parent / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Realistic Recovery Backtest",
        "",
        f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"- Period: recent {range_days} days, {strategy['interval']} bars",
        f"- Execution model: closed-bar decision, next-bar fill, stops before targets",
        f"- Symbols tested: {', '.join(sorted(bars))}",
        f"- Initial recovery position: {initial['recovery_quantity']} {recovery_symbol} @ {recovery_average:.4f}",
        f"- Initial marked equity: {_format_money(starting_equity)} USD",
        f"- Ending equity: {_format_money(ending_equity)} USD",
        f"- Return on marked equity: {return_pct:.2%}",
        f"- Buy-and-hold return on marked equity: {buy_hold_return:.2%}",
        f"- Marked PnL vs original book cost: {_format_money(marked_pnl_vs_book)} USD",
        f"- Max drawdown: {max_dd:.2%}",
        f"- Ending cash: {_format_money(cash)} USD",
        f"- Remaining recovery shares: {recovery_qty}",
        f"- Recovery ending mark: {last_price:.2f}",
        f"- Trades/events: {len(trades)}",
        f"- Closed wins / losses: {len(wins)} / {len(losses)}",
        f"- Closed win rate: {(len(wins) / len(closed_pnls)):.2%}" if closed_pnls else "- Closed win rate: -",
        "",
        "## Recovery Ladder",
        "",
    ]
    for rung in recovery_ladder:
        lines.append(
            f"- {rung['target_price']:.2f} x {rung['quantity']} shares: "
            f"{'filled' if rung['filled'] else 'not filled'}"
        )
    lines.extend(["", "## Recent Events", ""])
    for trade in trades[-40:]:
        lines.append(
            f"- {trade['timestamp']} {trade['kind']} {trade['symbol']} {trade['quantity']} "
            f"@ {trade['price']}, fee={trade['fee']}, pnl={trade['pnl']}, {trade['reason']}"
        )
    if errors:
        lines.extend(["", "## Data Errors", ""])
        for symbol, message in sorted(errors.items()):
            lines.append(f"- {symbol}: {message}")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return (
        f"Recovery backtest done: return={return_pct:.2%}, buy_hold={buy_hold_return:.2%}, "
        f"dd={max_dd:.2%}, events={len(trades)}, report={report_path}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest a realistic SOXL recovery + scalp overlay")
    parser.add_argument("--config", default="config.recovery_backtest.60d.json")
    args = parser.parse_args()
    print(run(Path(args.config)))


if __name__ == "__main__":
    main()

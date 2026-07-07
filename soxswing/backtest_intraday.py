from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .data import fetch_intraday_bars
from .intraday import _passes_entry_filter, _size_position, _symbol_score
from .models import IntradayBar
from .universe import load_symbols


def _max_drawdown(equity_curve: list[float]) -> float:
    peak = equity_curve[0]
    worst = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        drawdown = (value / peak) - 1
        worst = min(worst, drawdown)
    return worst


def _slice_until(bars: list[IntradayBar], timestamp: datetime) -> list[IntradayBar]:
    return [bar for bar in bars if bar.timestamp <= timestamp]


def _fetch_backtest_bars(symbols: list[str], strategy: dict[str, Any], range_days: int) -> tuple[dict[str, list[IntradayBar]], dict[str, str]]:
    bars: dict[str, list[IntradayBar]] = {}
    errors: dict[str, str] = {}
    for symbol in symbols:
        try:
            bars[symbol] = fetch_intraday_bars(symbol, strategy["interval"], range_days)
        except Exception as exc:
            errors[symbol] = str(exc)
    if not bars:
        raise RuntimeError(f"No backtest data loaded. Errors: {errors}")
    return bars, errors


def run(config_path: Path) -> str:
    with config_path.open("r", encoding="utf-8") as file:
        backtest_config = json.load(file)
    base_path = Path(backtest_config["base_config"])
    with base_path.open("r", encoding="utf-8") as file:
        config = json.load(file)

    account = config["account"]
    strategy = config["strategy"]
    symbols = load_symbols(strategy, base_path.parent)
    range_days = int(backtest_config.get("range_days", 30))
    bars, errors = _fetch_backtest_bars(symbols, strategy, range_days)

    timestamps = sorted(set.intersection(*(set(bar.timestamp for bar in symbol_bars) for symbol_bars in bars.values())))
    timestamps = timestamps[90:]
    cash = float(account["starting_cash"])
    starting_cash = cash
    position: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    equity_curve: list[float] = [cash]
    trades_today = 0
    trade_day = None

    for timestamp in timestamps:
        day = timestamp.date().isoformat()
        if trade_day != day:
            trade_day = day
            trades_today = 0

        sliced = {symbol: _slice_until(symbol_bars, timestamp) for symbol, symbol_bars in bars.items()}
        if any(len(symbol_bars) < 90 for symbol_bars in sliced.values()):
            continue

        scores = {symbol: _symbol_score(symbol_bars, strategy) for symbol, symbol_bars in sliced.items()}

        if position:
            price = float(scores[position["symbol"]]["price"])
            position["peak_price"] = max(float(position["peak_price"]), price)
            position["trailing_stop_price"] = max(
                float(position["trailing_stop_price"]),
                position["peak_price"] * (1 - float(strategy["trailing_stop_pct"])),
            )
            exit_reason = None
            if price <= float(position["stop_price"]):
                exit_reason = "stop"
            elif price <= float(position["trailing_stop_price"]):
                exit_reason = "trail"
            elif price >= float(position["take_profit_price"]):
                exit_reason = "take_profit"

            if exit_reason:
                pnl = (price - float(position["entry_price"])) * int(position["quantity"])
                cash += price * int(position["quantity"])
                trades.append(
                    {
                        "opened_at": position["opened_at"],
                        "closed_at": timestamp.isoformat(),
                        "symbol": position["symbol"],
                        "quantity": position["quantity"],
                        "entry": position["entry_price"],
                        "exit": round(price, 2),
                        "pnl": round(pnl, 2),
                        "reason": exit_reason,
                    }
                )
                position = None
                trades_today += 1
        else:
            if trades_today < int(strategy["max_trades_per_day"]):
                ranked = sorted(scores.items(), key=lambda item: item[1]["score"], reverse=True)
                best_symbol, best = ranked[0]
                second_symbol, second = ranked[1] if len(ranked) > 1 else ("-", {"score": -99})
                if _passes_entry_filter(best, second, strategy):
                    qty = _size_position(cash, best["price"], best["atr"], account, strategy)
                    if qty > 0:
                        price = float(best["price"])
                        cash -= qty * price
                        position = {
                            "symbol": best_symbol,
                            "quantity": qty,
                            "entry_price": round(price, 2),
                            "stop_price": round(price * (1 - float(strategy["stop_loss_pct"])), 2),
                            "take_profit_price": round(price * (1 + float(strategy["take_profit_pct"])), 2),
                            "trailing_stop_price": round(price * (1 - float(strategy["trailing_stop_pct"])), 2),
                            "peak_price": round(price, 2),
                            "opened_at": timestamp.isoformat(),
                            "runner_up": f"{second_symbol}:{second['score']}",
                        }
                        trades_today += 1

        equity = cash
        if position:
            equity += float(scores[position["symbol"]]["price"]) * int(position["quantity"])
        equity_curve.append(equity)

    if position:
        last_score = _symbol_score(bars[position["symbol"]], strategy)
        price = float(last_score["price"])
        pnl = (price - float(position["entry_price"])) * int(position["quantity"])
        cash += price * int(position["quantity"])
        trades.append(
            {
                "opened_at": position["opened_at"],
                "closed_at": timestamps[-1].isoformat() if timestamps else datetime.now().isoformat(),
                "symbol": position["symbol"],
                "quantity": position["quantity"],
                "entry": position["entry_price"],
                "exit": round(price, 2),
                "pnl": round(pnl, 2),
                "reason": "end_of_test",
            }
        )

    wins = [trade for trade in trades if float(trade["pnl"]) > 0]
    losses = [trade for trade in trades if float(trade["pnl"]) <= 0]
    total_pnl = cash - starting_cash
    return_pct = (cash / starting_cash - 1) if starting_cash else 0.0
    max_dd = _max_drawdown(equity_curve)

    report_path = Path(backtest_config.get("report_path", "reports/backtest_intraday.md"))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Multi-Symbol Intraday Backtest",
        "",
        f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"- Symbols tested: {', '.join(sorted(bars))}",
        f"- Period: recent {range_days} days, {strategy['interval']} bars",
        f"- Starting cash: {starting_cash:,.2f} USD",
        f"- Ending cash: {cash:,.2f} USD",
        f"- Total PnL: {total_pnl:,.2f} USD",
        f"- Return: {return_pct:.2%}",
        f"- Max drawdown: {max_dd:.2%}",
        f"- Trades: {len(trades)}",
        f"- Wins / losses: {len(wins)} / {len(losses)}",
        f"- Win rate: {(len(wins) / len(trades)):.2%}" if trades else "- Win rate: -",
        "",
        "## Recent Trades",
        "",
    ]
    for trade in trades[-20:]:
        lines.append(
            f"- {trade['closed_at']} {trade['symbol']} {trade['quantity']} shares "
            f"{trade['entry']} -> {trade['exit']}, pnl={trade['pnl']}, {trade['reason']}"
        )
    if errors:
        lines.extend(["", "## Data Errors", ""])
        for symbol, message in sorted(errors.items()):
            lines.append(f"- {symbol}: {message}")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return f"Backtest done: return={return_pct:.2%}, trades={len(trades)}, report={report_path}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the multi-symbol intraday strategy")
    parser.add_argument("--config", default="config.backtest.example.json")
    args = parser.parse_args()
    print(run(Path(args.config)))


if __name__ == "__main__":
    main()

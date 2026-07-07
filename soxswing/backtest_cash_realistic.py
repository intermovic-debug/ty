from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .backtest_intraday import _fetch_backtest_bars, _max_drawdown, _slice_until
from .intraday import _passes_entry_filter, _rank_scores, _size_position, _symbol_score
from .universe import load_symbols


def _merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dict(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _commission(quantity: int, execution: dict[str, Any]) -> float:
    return max(
        float(execution.get("minimum_commission", 0.0)),
        int(quantity) * float(execution.get("commission_per_share", 0.0)),
    )


def _slip(price: float, side: str, execution: dict[str, Any]) -> float:
    bps = float(execution.get("slippage_bps", 0.0)) / 10000
    if side == "buy":
        return price * (1 + bps)
    return price * (1 - bps)


def _parse_hhmm(value: Any) -> tuple[int, int] | None:
    if value in (None, ""):
        return None
    hour_text, minute_text = str(value).split(":", 1)
    return int(hour_text), int(minute_text)


def _minutes_of_day(timestamp: datetime) -> int:
    return timestamp.hour * 60 + timestamp.minute


def _within_trade_window(timestamp: datetime, strategy: dict[str, Any]) -> bool:
    start = _parse_hhmm(strategy.get("trade_start_utc"))
    end = _parse_hhmm(strategy.get("trade_end_utc"))
    current = _minutes_of_day(timestamp)
    if start is not None:
        if current < start[0] * 60 + start[1]:
            return False
    if end is not None:
        if current > end[0] * 60 + end[1]:
            return False
    return True


def _simulate_variant(
    name: str,
    config: dict[str, Any],
    execution: dict[str, Any],
    base_path: Path,
    range_days: int,
    preloaded_bars: dict[str, list[Any]] | None = None,
    preloaded_errors: dict[str, str] | None = None,
) -> dict[str, Any]:
    account = config["account"]
    strategy = config["strategy"]
    symbols = load_symbols(strategy, base_path.parent)
    if preloaded_bars is None:
        bars, errors = _fetch_backtest_bars(symbols, strategy, range_days)
    else:
        bars = {symbol: preloaded_bars[symbol] for symbol in symbols if symbol in preloaded_bars}
        missing = sorted(set(symbols) - set(bars))
        errors = dict(preloaded_errors or {})
        if missing:
            errors["missing"] = ", ".join(missing)
        if not bars:
            raise RuntimeError(f"{name}: no preloaded bars for {symbols}")
    timestamps = sorted(set.intersection(*(set(bar.timestamp for bar in symbol_bars) for symbol_bars in bars.values())))
    timestamps = timestamps[90:]
    if len(timestamps) < 20:
        raise RuntimeError(f"{name}: not enough synchronized bars: {len(timestamps)}")

    by_symbol_time = {
        symbol: {bar.timestamp: bar for bar in symbol_bars}
        for symbol, symbol_bars in bars.items()
    }
    index_by_symbol_time = {
        symbol: {bar.timestamp: index for index, bar in enumerate(symbol_bars)}
        for symbol, symbol_bars in bars.items()
    }
    cash = float(account["starting_cash"])
    starting_cash = cash
    position: dict[str, Any] | None = None
    equity_curve: list[float] = [cash]
    trades: list[dict[str, Any]] = []
    fees = 0.0
    trades_today = 0
    trade_day = None
    last_trade_at: datetime | None = None

    for index, timestamp in enumerate(timestamps[:-1]):
        next_timestamp = timestamps[index + 1]
        day = timestamp.date().isoformat()
        if day != trade_day:
            trade_day = day
            trades_today = 0

        sliced = {
            symbol: symbol_bars[max(0, index_by_symbol_time[symbol][timestamp] - 95) : index_by_symbol_time[symbol][timestamp] + 1]
            for symbol, symbol_bars in bars.items()
        }
        if any(len(symbol_bars) < 90 for symbol_bars in sliced.values()):
            continue
        scores = {symbol: _symbol_score(symbol_bars, strategy) for symbol, symbol_bars in sliced.items()}

        if position:
            bar = by_symbol_time[position["symbol"]][next_timestamp]
            peak = max(float(position["peak_price"]), bar.high)
            trailing = max(
                float(position["trailing_stop_price"]),
                peak * (1 - float(strategy["trailing_stop_pct"])),
            )
            stop_floor = max(float(position["stop_price"]), trailing)
            exit_price = None
            exit_reason = None
            if bar.low <= stop_floor:
                exit_price = _slip(stop_floor, "sell", execution)
                exit_reason = "stop_or_trail"
            elif bar.high >= float(position["take_profit_price"]):
                exit_price = _slip(float(position["take_profit_price"]), "sell", execution)
                exit_reason = "take_profit"

            if exit_price is not None and exit_reason is not None:
                qty = int(position["quantity"])
                fee = _commission(qty, execution)
                fees += fee
                proceeds = qty * exit_price - fee
                cash += proceeds
                pnl = (exit_price - float(position["entry_price"])) * qty - fee
                trades.append(
                    {
                        "closed_at": next_timestamp.isoformat(),
                        "symbol": position["symbol"],
                        "quantity": qty,
                        "entry": round(float(position["entry_price"]), 2),
                        "exit": round(exit_price, 2),
                        "pnl": round(pnl, 2),
                        "reason": exit_reason,
                    }
                )
                position = None
                trades_today += 1
                last_trade_at = next_timestamp
            else:
                position["peak_price"] = peak
                position["trailing_stop_price"] = trailing

        if position is None and _within_trade_window(timestamp, strategy):
            in_cooldown = (
                last_trade_at is not None
                and (timestamp - last_trade_at).total_seconds() < int(strategy["cooldown_minutes"]) * 60
            )
            if trades_today < int(strategy["max_trades_per_day"]) and not in_cooldown:
                ranked = _rank_scores(scores)
                best_symbol, best = ranked[0]
                second_symbol, second = ranked[1] if len(ranked) > 1 else ("-", {"score": -99})
                if _passes_entry_filter(best, second, strategy):
                    entry_bar = by_symbol_time[best_symbol][next_timestamp]
                    entry_price = _slip(entry_bar.open, "buy", execution)
                    qty = _size_position(cash, entry_price, float(best["atr"]), account, strategy)
                    while qty > 0:
                        fee = _commission(qty, execution)
                        if qty * entry_price + fee <= cash:
                            break
                        qty -= 1
                    if qty > 0:
                        fee = _commission(qty, execution)
                        fees += fee
                        cash -= qty * entry_price + fee
                        position = {
                            "symbol": best_symbol,
                            "quantity": qty,
                            "entry_price": entry_price,
                            "stop_price": entry_price * (1 - float(strategy["stop_loss_pct"])),
                            "take_profit_price": entry_price * (1 + float(strategy["take_profit_pct"])),
                            "trailing_stop_price": entry_price * (1 - float(strategy["trailing_stop_pct"])),
                            "peak_price": entry_price,
                            "opened_at": next_timestamp.isoformat(),
                        }

        equity = cash
        if position:
            close = by_symbol_time[position["symbol"]][next_timestamp].close
            equity += int(position["quantity"]) * close
        equity_curve.append(equity)

    if position:
        last = by_symbol_time[position["symbol"]][timestamps[-1]].close
        qty = int(position["quantity"])
        fee = _commission(qty, execution)
        fees += fee
        exit_price = _slip(last, "sell", execution)
        cash += qty * exit_price - fee
        pnl = (exit_price - float(position["entry_price"])) * qty - fee
        trades.append(
            {
                "closed_at": timestamps[-1].isoformat(),
                "symbol": position["symbol"],
                "quantity": qty,
                "entry": round(float(position["entry_price"]), 2),
                "exit": round(exit_price, 2),
                "pnl": round(pnl, 2),
                "reason": "end_of_test",
            }
        )

    wins = [trade for trade in trades if float(trade["pnl"]) > 0]
    losses = [trade for trade in trades if float(trade["pnl"]) <= 0]
    total_pnl = cash - starting_cash
    return_pct = (cash / starting_cash - 1) if starting_cash else 0.0
    max_dd = _max_drawdown(equity_curve)
    avg_pnl = (sum(float(trade["pnl"]) for trade in trades) / len(trades)) if trades else 0.0
    return {
        "name": name,
        "symbols": ", ".join(symbols),
        "return_pct": return_pct,
        "max_drawdown": max_dd,
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(trades)) if trades else 0.0,
        "total_pnl": total_pnl,
        "ending_cash": cash,
        "fees": fees,
        "avg_pnl": avg_pnl,
        "score": return_pct + max_dd,
        "recent_trades": trades[-8:],
        "errors": errors,
    }


def run(config_path: Path) -> str:
    with config_path.open("r", encoding="utf-8") as file:
        sweep = json.load(file)
    base_path = Path(sweep["base_config"])
    if not base_path.is_absolute():
        base_path = config_path.parent / base_path
    with base_path.open("r", encoding="utf-8") as file:
        base_config = json.load(file)

    range_days = int(sweep.get("range_days", 60))
    execution = sweep.get("execution", {})
    results: list[dict[str, Any]] = []
    failures: list[str] = []
    for variant in sweep.get("variants", []):
        name = str(variant["name"])
        patch: dict[str, Any] = {
            "account": variant.get("account", {}),
            "strategy": variant.get("strategy", {}),
        }
        if "active_groups" in variant:
            patch["strategy"]["active_groups"] = variant["active_groups"]
        config = _merge_dict(base_config, patch)
        try:
            results.append(_simulate_variant(name, config, execution, base_path, range_days))
        except Exception as exc:
            failures.append(f"{name}: {exc}")

    results.sort(key=lambda item: item["score"], reverse=True)
    report_path = Path(sweep.get("report_path", "reports/backtest_cash_realistic_sweep.md"))
    if not report_path.is_absolute():
        report_path = config_path.parent / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Cash-Start Realistic Intraday Sweep",
        "",
        f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"- Period: recent {range_days} days, closed-bar decision and next-bar fills",
        f"- Execution: commission/share={execution.get('commission_per_share')}, "
        f"minimum={execution.get('minimum_commission')}, slippage_bps={execution.get('slippage_bps')}",
        "",
        "| Rank | Variant | Return | Max DD | Score | Trades | Win Rate | PnL | Fees | Symbols |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for index, result in enumerate(results, 1):
        lines.append(
            "| {rank} | {name} | {ret:.2%} | {dd:.2%} | {score:.2%} | {trades} | {win:.2%} | {pnl:.2f} | {fees:.2f} | {symbols} |".format(
                rank=index,
                name=result["name"],
                ret=result["return_pct"],
                dd=result["max_drawdown"],
                score=result["score"],
                trades=result["trades"],
                win=result["win_rate"],
                pnl=result["total_pnl"],
                fees=result["fees"],
                symbols=result["symbols"],
            )
        )
    if results:
        best = results[0]
        lines.extend(["", "## Best Variant Recent Trades", ""])
        for trade in best["recent_trades"]:
            lines.append(
                f"- {trade['closed_at']} {trade['symbol']} {trade['quantity']} "
                f"{trade['entry']} -> {trade['exit']}, pnl={trade['pnl']}, {trade['reason']}"
            )
    if failures:
        lines.extend(["", "## Failures", ""])
        lines.extend(f"- {failure}" for failure in failures)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    best_text = results[0]["name"] if results else "-"
    return f"Cash realistic sweep done: variants={len(results)}, best={best_text}, report={report_path}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare realistic cash-start intraday variants")
    parser.add_argument("--config", default="config.cash_realistic_sweep.60d.json")
    args = parser.parse_args()
    print(run(Path(args.config)))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .config import load_config
from .data import fetch_daily_bars
from .models import Bar, Position
from .risk import build_entry_order
from .strategy import choose_signal


def _max_drawdown(equity_curve: list[float]) -> float:
    peak = equity_curve[0]
    worst = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        worst = min(worst, (value / peak) - 1)
    return worst


def _bars_until(bars: list[Bar], current_date: date) -> list[Bar]:
    return [bar for bar in bars if bar.date <= current_date]


def _latest_bar_by_date(bars: list[Bar]) -> dict[date, Bar]:
    return {bar.date: bar for bar in bars}


def run(config_path: Path) -> str:
    with config_path.open("r", encoding="utf-8") as file:
        backtest_config = json.load(file)

    base_path = Path(backtest_config["base_config"])
    if not base_path.is_absolute():
        base_path = config_path.parent / base_path
    config = load_config(base_path)
    range_days = int(backtest_config.get("range_days", 365))
    warmup_days = int(backtest_config.get("warmup_days", 120))

    bars = {
        symbol: fetch_daily_bars(symbol, days=range_days + warmup_days)
        for symbol in config.strategy.symbols
    }
    by_date = {symbol: _latest_bar_by_date(symbol_bars) for symbol, symbol_bars in bars.items()}
    dates = sorted(set.intersection(*(set(items) for items in by_date.values())))
    if len(dates) < warmup_days + 20:
        raise RuntimeError(f"Not enough daily bars for backtest: {len(dates)}")
    dates = dates[-range_days:]

    cash = float(config.account.starting_cash)
    starting_cash = cash
    position: Position | None = None
    trades: list[dict[str, Any]] = []
    equity_curve: list[float] = [cash]

    for current_date in dates:
        sliced = {symbol: _bars_until(symbol_bars, current_date) for symbol, symbol_bars in bars.items()}
        if any(len(symbol_bars) <= max(config.strategy.slow_ma, config.strategy.rsi_period, config.strategy.atr_period) for symbol_bars in sliced.values()):
            continue

        if position:
            bar = by_date[position.symbol][current_date]
            exit_price = None
            exit_reason = None
            if bar.low <= position.stop_price:
                exit_price = position.stop_price
                exit_reason = "stop"
            elif bar.high >= position.take_profit_price:
                exit_price = position.take_profit_price
                exit_reason = "take_profit"

            if exit_price is not None and exit_reason is not None:
                pnl = (exit_price - position.entry_price) * position.quantity
                cash += exit_price * position.quantity
                trades.append(
                    {
                        "opened_at": position.opened_at,
                        "closed_at": current_date.isoformat(),
                        "symbol": position.symbol,
                        "quantity": position.quantity,
                        "entry": round(position.entry_price, 2),
                        "exit": round(exit_price, 2),
                        "pnl": round(pnl, 2),
                        "reason": exit_reason,
                    }
                )
                position = None

        if position is None:
            signal = choose_signal(sliced, config.strategy, None)
            order = build_entry_order(signal, cash, config.account, config.strategy)
            if order is not None:
                cost = order.quantity * order.price
                cash -= cost
                position = Position(
                    symbol=order.symbol,
                    quantity=order.quantity,
                    entry_price=order.price,
                    stop_price=float(order.stop_price or 0),
                    take_profit_price=float(order.take_profit_price or 0),
                    opened_at=current_date.isoformat(),
                )

        equity = cash
        if position:
            equity += by_date[position.symbol][current_date].close * position.quantity
        equity_curve.append(equity)

    if position:
        last_bar = by_date[position.symbol][dates[-1]]
        pnl = (last_bar.close - position.entry_price) * position.quantity
        cash += last_bar.close * position.quantity
        trades.append(
            {
                "opened_at": position.opened_at,
                "closed_at": dates[-1].isoformat(),
                "symbol": position.symbol,
                "quantity": position.quantity,
                "entry": round(position.entry_price, 2),
                "exit": round(last_bar.close, 2),
                "pnl": round(pnl, 2),
                "reason": "end_of_test",
            }
        )
        position = None

    wins = [trade for trade in trades if float(trade["pnl"]) > 0]
    losses = [trade for trade in trades if float(trade["pnl"]) <= 0]
    total_pnl = cash - starting_cash
    return_pct = (cash / starting_cash - 1) if starting_cash else 0.0
    max_dd = _max_drawdown(equity_curve)

    report_path = Path(backtest_config.get("report_path", "reports/backtest_daily.md"))
    if not report_path.is_absolute():
        report_path = config_path.parent / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Daily Swing Backtest",
        "",
        f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"- Base config: {base_path}",
        f"- Symbols tested: {', '.join(config.strategy.symbols)}",
        f"- Period: recent {range_days} trading days",
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
    for trade in trades[-30:]:
        lines.append(
            f"- {trade['closed_at']} {trade['symbol']} {trade['quantity']} shares "
            f"{trade['entry']} -> {trade['exit']}, pnl={trade['pnl']}, {trade['reason']}"
        )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return f"Daily backtest done: return={return_pct:.2%}, trades={len(trades)}, report={report_path}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the daily SOXL/SOXS swing strategy")
    parser.add_argument("--config", default="config.backtest.daily.365d.json")
    args = parser.parse_args()
    print(run(Path(args.config)))


if __name__ == "__main__":
    main()

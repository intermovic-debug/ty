from __future__ import annotations

import argparse
import copy
import itertools
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .backtest_intraday import _fetch_backtest_bars, _max_drawdown, _slice_until
from .intraday import _passes_entry_filter, _size_position, _symbol_score
from .universe import load_symbols


@dataclass(frozen=True)
class TrialMetrics:
    params: dict[str, Any]
    train_return_pct: float
    train_max_drawdown: float
    train_trades: int
    train_win_rate: float
    test_return_pct: float
    test_max_drawdown: float
    test_trades: int
    test_win_rate: float
    score: float


def _set_nested(config: dict[str, Any], dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    current: dict[str, Any] = config
    for part in parts[:-1]:
        current = current[part]
    current[parts[-1]] = value


def _parameter_sets(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = list(grid)
    values = [grid[key] for key in keys]
    return [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*values)]


def _score_snapshots(
    bars_by_symbol: dict[str, list[Any]],
    strategy: dict[str, Any],
) -> list[tuple[datetime, dict[str, dict[str, Any]]]]:
    timestamps = sorted(set.intersection(*(set(bar.timestamp for bar in bars) for bars in bars_by_symbol.values())))
    snapshots: list[tuple[datetime, dict[str, dict[str, Any]]]] = []
    for timestamp in timestamps[90:]:
        sliced = {symbol: _slice_until(bars, timestamp) for symbol, bars in bars_by_symbol.items()}
        if any(len(bars) < 90 for bars in sliced.values()):
            continue
        scores = {symbol: _symbol_score(bars, strategy) for symbol, bars in sliced.items()}
        snapshots.append((timestamp, scores))
    return snapshots


def _simulate(
    snapshots: list[tuple[datetime, dict[str, dict[str, Any]]]],
    account: dict[str, Any],
    strategy: dict[str, Any],
) -> dict[str, Any]:
    cash = float(account["starting_cash"])
    starting_cash = cash
    position: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    equity_curve: list[float] = [cash]
    trades_today = 0
    trade_day = None

    for timestamp, scores in snapshots:
        day = timestamp.date().isoformat()
        if trade_day != day:
            trade_day = day
            trades_today = 0

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
        elif trades_today < int(strategy["max_trades_per_day"]):
            ranked = sorted(scores.items(), key=lambda item: item[1]["score"], reverse=True)
            best_symbol, best = ranked[0]
            _second_symbol, second = ranked[1] if len(ranked) > 1 else ("-", {"score": -99})
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
                    }
                    trades_today += 1

        equity = cash
        if position:
            equity += float(scores[position["symbol"]]["price"]) * int(position["quantity"])
        equity_curve.append(equity)

    if position and snapshots:
        _timestamp, scores = snapshots[-1]
        price = float(scores[position["symbol"]]["price"])
        pnl = (price - float(position["entry_price"])) * int(position["quantity"])
        cash += price * int(position["quantity"])
        trades.append(
            {
                "closed_at": snapshots[-1][0].isoformat(),
                "symbol": position["symbol"],
                "quantity": position["quantity"],
                "entry": position["entry_price"],
                "exit": round(price, 2),
                "pnl": round(pnl, 2),
                "reason": "end_of_test",
            }
        )

    wins = [trade for trade in trades if float(trade["pnl"]) > 0]
    return_pct = (cash / starting_cash - 1) if starting_cash else 0.0
    max_drawdown = _max_drawdown(equity_curve)
    return {
        "return_pct": return_pct,
        "max_drawdown": max_drawdown,
        "trades": len(trades),
        "win_rate": (len(wins) / len(trades)) if trades else 0.0,
        "ending_cash": cash,
    }


def _trial_score(
    train: dict[str, Any],
    test: dict[str, Any],
    max_drawdown_floor: float,
    min_test_trades: int,
) -> float:
    score = float(test["return_pct"]) - abs(float(test["max_drawdown"])) * 1.25
    score += float(train["return_pct"]) * 0.20
    if float(test["max_drawdown"]) < max_drawdown_floor:
        score -= abs(float(test["max_drawdown"]) - max_drawdown_floor) * 4
    if int(test["trades"]) < min_test_trades:
        score -= (min_test_trades - int(test["trades"])) * 0.01
    return score


def _format_pct(value: float) -> str:
    return f"{value:.2%}"


def _write_report(
    path: Path,
    base_config: dict[str, Any],
    range_days: int,
    split_pct: float,
    results: list[TrialMetrics],
    chosen: TrialMetrics,
    errors: dict[str, str],
) -> None:
    lines = [
        "# Intraday Strategy Optimization",
        "",
        f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"- Period: recent {range_days} days",
        f"- Train/test split: {split_pct:.0%} / {1 - split_pct:.0%}",
        f"- Trials: {len(results)}",
        f"- Base active groups: {', '.join(base_config['strategy'].get('active_groups', []))}",
        "",
        "## Recommended Candidate",
        "",
        f"- Score: {chosen.score:.4f}",
        f"- Train return / DD / trades: {_format_pct(chosen.train_return_pct)} / {_format_pct(chosen.train_max_drawdown)} / {chosen.train_trades}",
        f"- Test return / DD / trades: {_format_pct(chosen.test_return_pct)} / {_format_pct(chosen.test_max_drawdown)} / {chosen.test_trades}",
        f"- Test win rate: {_format_pct(chosen.test_win_rate)}",
        "",
        "Parameters:",
        "",
    ]
    for key, value in chosen.params.items():
        lines.append(f"- `{key}`: `{value}`")

    lines.extend(
        [
            "",
            "## Top 15 Trials",
            "",
            "| Rank | Score | Test Ret | Test DD | Test Trades | Train Ret | Params |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for rank, trial in enumerate(results[:15], start=1):
        params = ", ".join(f"{key}={value}" for key, value in trial.params.items())
        lines.append(
            f"| {rank} | {trial.score:.4f} | {_format_pct(trial.test_return_pct)} | "
            f"{_format_pct(trial.test_max_drawdown)} | {trial.test_trades} | "
            f"{_format_pct(trial.train_return_pct)} | {params} |"
        )

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This is a parameter search, not a profit guarantee.",
            "- The candidate config is intentionally separate from the live/default config.",
            "- Prefer candidates that survive the test split with reasonable drawdown and enough trades.",
        ]
    )
    if errors:
        lines.extend(["", "## Data Errors", ""])
        for symbol, message in sorted(errors.items()):
            lines.append(f"- {symbol}: {message}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def run(config_path: Path) -> str:
    with config_path.open("r", encoding="utf-8") as file:
        optimize_config = json.load(file)

    base_path = Path(optimize_config["base_config"])
    with base_path.open("r", encoding="utf-8") as file:
        base_config = json.load(file)

    account = base_config["account"]
    strategy = base_config["strategy"]
    symbols = load_symbols(strategy, base_path.parent)
    range_days = int(optimize_config.get("range_days", 30))
    split_pct = float(optimize_config.get("split_pct", 0.7))
    bars, errors = _fetch_backtest_bars(symbols, strategy, range_days)
    snapshots = _score_snapshots(bars, strategy)
    if len(snapshots) < 20:
        raise RuntimeError(f"Not enough snapshots to optimize: {len(snapshots)}")

    split_index = max(1, min(len(snapshots) - 1, int(len(snapshots) * split_pct)))
    train_snapshots = snapshots[:split_index]
    test_snapshots = snapshots[split_index:]
    max_drawdown_floor = -float(optimize_config.get("max_acceptable_drawdown_pct", 0.03))
    min_test_trades = int(optimize_config.get("min_test_trades", 4))

    results: list[TrialMetrics] = []
    for params in _parameter_sets(optimize_config["grid"]):
        trial_config = copy.deepcopy(base_config)
        for key, value in params.items():
            _set_nested(trial_config, key, value)
        trial_account = trial_config["account"]
        trial_strategy = trial_config["strategy"]
        train = _simulate(train_snapshots, trial_account, trial_strategy)
        test = _simulate(test_snapshots, trial_account, trial_strategy)
        score = _trial_score(train, test, max_drawdown_floor, min_test_trades)
        results.append(
            TrialMetrics(
                params=params,
                train_return_pct=float(train["return_pct"]),
                train_max_drawdown=float(train["max_drawdown"]),
                train_trades=int(train["trades"]),
                train_win_rate=float(train["win_rate"]),
                test_return_pct=float(test["return_pct"]),
                test_max_drawdown=float(test["max_drawdown"]),
                test_trades=int(test["trades"]),
                test_win_rate=float(test["win_rate"]),
                score=score,
            )
        )

    results.sort(key=lambda result: result.score, reverse=True)
    chosen = results[0]
    report_path = Path(optimize_config.get("report_path", "reports/optimize_intraday.md"))
    _write_report(report_path, base_config, range_days, split_pct, results, chosen, errors)

    candidate_config = copy.deepcopy(base_config)
    for key, value in chosen.params.items():
        _set_nested(candidate_config, key, value)
    candidate_path = Path(optimize_config.get("candidate_config_path", "config.intraday.optimized_candidate.json"))
    candidate_path.write_text(json.dumps(candidate_config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return (
        "Optimization done: "
        f"test_return={chosen.test_return_pct:.2%}, "
        f"test_dd={chosen.test_max_drawdown:.2%}, "
        f"test_trades={chosen.test_trades}, "
        f"report={report_path}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Optimize intraday strategy parameters with a train/test split")
    parser.add_argument("--config", default="config.optimize.example.json")
    args = parser.parse_args()
    print(run(Path(args.config)))


if __name__ == "__main__":
    main()

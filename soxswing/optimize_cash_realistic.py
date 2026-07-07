from __future__ import annotations

import argparse
import copy
import itertools
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .backtest_cash_realistic import _fetch_backtest_bars, _merge_dict, _simulate_variant
from .universe import load_symbols


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _candidate_grid() -> list[dict[str, Any]]:
    variants: list[dict[str, Any]] = []
    for (
        max_position_pct,
        risk_per_trade_pct,
        entry_score,
        min_score_gap,
        min_momentum_pct,
        max_entry_rsi,
        stop_loss_pct,
        take_profit_pct,
        trailing_stop_pct,
        cooldown_minutes,
        max_trades_per_day,
        time_window,
    ) in itertools.product(
        [0.24, 0.30],
        [0.003],
        [4, 5],
        [1, 2],
        [0.002, 0.004],
        [70, 76],
        [0.005, 0.007],
        [0.020, 0.032],
        [0.006, 0.010],
        [60, 120],
        [1, 2],
        ["all_day", "avoid_edges"],
    ):
        strategy = {
            "entry_score": entry_score,
            "min_score_gap": min_score_gap,
            "min_momentum_pct": min_momentum_pct,
            "min_entry_rsi": 42 if entry_score == 4 else 45,
            "max_entry_rsi": max_entry_rsi,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "trailing_stop_pct": trailing_stop_pct,
            "cooldown_minutes": cooldown_minutes,
            "max_trades_per_day": max_trades_per_day,
            "active_groups": ["Semiconductors 3x"],
        }
        if time_window == "avoid_edges":
            strategy["trade_start_utc"] = "14:00"
            strategy["trade_end_utc"] = "19:30"
        variants.append(
            {
                "name": (
                    f"semis_p{max_position_pct:.2f}_r{risk_per_trade_pct:.3f}_"
                    f"s{entry_score}_gap{min_score_gap}_m{min_momentum_pct:.3f}_"
                    f"rsi{max_entry_rsi}_st{stop_loss_pct:.3f}_tp{take_profit_pct:.3f}_"
                    f"tr{trailing_stop_pct:.3f}_cd{cooldown_minutes}_d{max_trades_per_day}_{time_window}"
                ),
                "account": {
                    "max_position_pct": max_position_pct,
                    "risk_per_trade_pct": risk_per_trade_pct,
                },
                "strategy": strategy,
            }
        )
    return variants


def _optimizer_score(result: dict[str, Any], trade_penalty: float) -> float:
    return float(result["return_pct"]) + float(result["max_drawdown"]) - int(result["trades"]) * trade_penalty


def run(
    base_config_path: Path,
    range_days: int,
    report_path: Path,
    output_config_path: Path,
    minimum_commission: float,
    commission_per_share: float,
    slippage_bps: float,
    trade_penalty: float,
) -> str:
    base_config = _load_json(base_config_path)
    base_path = base_config_path
    prefetch_config = copy.deepcopy(base_config)
    prefetch_config["strategy"]["active_groups"] = ["Semiconductors 3x"]
    symbols = load_symbols(prefetch_config["strategy"], base_config_path.parent)
    bars, errors = _fetch_backtest_bars(symbols, prefetch_config["strategy"], range_days)

    execution = {
        "commission_per_share": commission_per_share,
        "minimum_commission": minimum_commission,
        "slippage_bps": slippage_bps,
    }
    results: list[dict[str, Any]] = []
    failures: list[str] = []
    for variant in _candidate_grid():
        config = _merge_dict(
            base_config,
            {
                "account": variant["account"],
                "strategy": variant["strategy"],
            },
        )
        try:
            result = _simulate_variant(
                variant["name"],
                config,
                execution,
                base_path,
                range_days,
                preloaded_bars=bars,
                preloaded_errors=errors,
            )
            result["optimizer_score"] = _optimizer_score(result, trade_penalty)
            result["config_patch"] = variant
            results.append(result)
        except Exception as exc:
            failures.append(f"{variant['name']}: {exc}")

    results.sort(key=lambda item: item["optimizer_score"], reverse=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Cash-Start Realistic Optimizer",
        "",
        f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"- Base config: {base_config_path}",
        f"- Symbols: {', '.join(symbols)}",
        f"- Period: recent {range_days} days",
        f"- Execution: commission/share={commission_per_share}, minimum={minimum_commission}, slippage_bps={slippage_bps}",
        f"- Optimizer score: return + max_drawdown - trades * {trade_penalty}",
        f"- Candidates: {len(results)} succeeded, {len(failures)} failed",
        "",
        "| Rank | Variant | Return | Max DD | Opt Score | Trades | Win Rate | PnL | Fees |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for index, result in enumerate(results[:25], 1):
        lines.append(
            "| {rank} | {name} | {ret:.2%} | {dd:.2%} | {score:.2%} | {trades} | {win:.2%} | {pnl:.2f} | {fees:.2f} |".format(
                rank=index,
                name=result["name"],
                ret=result["return_pct"],
                dd=result["max_drawdown"],
                score=result["optimizer_score"],
                trades=result["trades"],
                win=result["win_rate"],
                pnl=result["total_pnl"],
                fees=result["fees"],
            )
        )

    if results:
        best = results[0]
        best_config = _merge_dict(
            base_config,
            {
                "account": best["config_patch"]["account"],
                "strategy": best["config_patch"]["strategy"],
            },
        )
        output_config_path.write_text(
            json.dumps(best_config, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        lines.extend(
            [
                "",
                "## Best Config Patch",
                "",
                "```json",
                json.dumps(best["config_patch"], indent=2, ensure_ascii=False),
                "```",
                "",
                f"- Wrote optimized config: {output_config_path}",
            ]
        )
    if failures:
        lines.extend(["", "## Failures", ""])
        lines.extend(f"- {failure}" for failure in failures[:20])

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    best_name = results[0]["name"] if results else "-"
    return f"Cash optimizer done: candidates={len(results)}, best={best_name}, report={report_path}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Grid optimize realistic cash-start SOXL/SOXS strategy.")
    parser.add_argument("--base-config", default="config.intraday.optimized_forward_paper.json")
    parser.add_argument("--range-days", type=int, default=60)
    parser.add_argument("--report", default="reports/cash_realistic_optimizer_60d.md")
    parser.add_argument("--output-config", default="config.intraday.cash_optimized_semis.json")
    parser.add_argument("--minimum-commission", type=float, default=0.35)
    parser.add_argument("--commission-per-share", type=float, default=0.0035)
    parser.add_argument("--slippage-bps", type=float, default=5)
    parser.add_argument("--trade-penalty", type=float, default=0.00005)
    args = parser.parse_args()
    print(
        run(
            Path(args.base_config),
            args.range_days,
            Path(args.report),
            Path(args.output_config),
            args.minimum_commission,
            args.commission_per_share,
            args.slippage_bps,
            args.trade_penalty,
        )
    )


if __name__ == "__main__":
    main()

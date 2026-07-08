from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parent
REPO = WORKSPACE if (WORKSPACE / "soxswing").exists() else Path(r"C:\Users\Administrator\Documents\Codex\ty")
REPORT_PATH = WORKSPACE / "ibkr_runtime" / "reports" / "cash_realistic_optimizer_ibkr_1y.md"
OUTPUT_CONFIG_PATH = WORKSPACE / "ibkr_runtime" / "config.intraday.cash_optimized_semis_ibkr_1y.json"

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(WORKSPACE))

from run_ibkr_csv_cash_backtest import _load_historical_bars, _pct, HISTORICAL_DIR  # noqa: E402
from soxswing.backtest_cash_realistic import _merge_dict, _simulate_variant  # noqa: E402
from soxswing.optimize_cash_realistic import _candidate_grid, _optimizer_score  # noqa: E402
from soxswing.universe import load_symbols  # noqa: E402


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def run() -> str:
    base_config_path = REPO / "config.intraday.optimized_forward_paper.json"
    base_config = _load_json(base_config_path)
    preloaded_bars = _load_historical_bars(HISTORICAL_DIR)
    available_symbols = sorted(preloaded_bars)
    prefetch_config = _merge_dict(
        base_config,
        {"strategy": {"active_groups": ["Semiconductors 3x"]}},
    )
    symbols = load_symbols(prefetch_config["strategy"], REPO)
    missing = sorted(set(symbols) - set(available_symbols))
    if missing:
        raise RuntimeError(f"Missing CSV data for {', '.join(missing)}")

    execution = {
        "commission_per_share": 0.0035,
        "minimum_commission": 0.35,
        "slippage_bps": 5,
    }
    trade_penalty = 0.00005
    results: list[dict[str, Any]] = []
    failures: list[str] = []
    variants = _candidate_grid()
    for variant in variants:
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
                base_config_path,
                range_days=365,
                preloaded_bars=preloaded_bars,
                preloaded_errors={},
            )
            result["optimizer_score"] = _optimizer_score(result, trade_penalty)
            result["config_patch"] = variant
            results.append(result)
        except Exception as exc:
            failures.append(f"{variant['name']}: {exc}")

    results.sort(key=lambda item: item["optimizer_score"], reverse=True)
    by_return = sorted(results, key=lambda item: item["return_pct"], reverse=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# IBKR 1Y Cash-Start Realistic Optimizer",
        "",
        f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"- Source CSV: {HISTORICAL_DIR}",
        f"- Symbols: {', '.join(symbols)}",
        f"- Candidates: {len(results)} succeeded, {len(failures)} failed",
        f"- Execution: commission/share={execution['commission_per_share']}, "
        f"minimum={execution['minimum_commission']}, slippage_bps={execution['slippage_bps']}",
        f"- Optimizer score: return + max_drawdown - trades * {trade_penalty}",
        "",
        "## Top By Risk-Adjusted Score",
        "",
        "| Rank | Variant | Return | Max DD | Opt Score | Trades | Win Rate | PnL | Fees |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for index, result in enumerate(results[:25], 1):
        lines.append(
            "| {rank} | {name} | {ret} | {dd} | {score} | {trades} | {win} | {pnl:.2f} | {fees:.2f} |".format(
                rank=index,
                name=result["name"],
                ret=_pct(float(result["return_pct"])),
                dd=_pct(float(result["max_drawdown"])),
                score=_pct(float(result["optimizer_score"])),
                trades=result["trades"],
                win=_pct(float(result["win_rate"])),
                pnl=float(result["total_pnl"]),
                fees=float(result["fees"]),
            )
        )

    lines.extend(
        [
            "",
            "## Top By Raw Return",
            "",
            "| Rank | Variant | Return | Max DD | Opt Score | Trades | Win Rate | PnL | Fees |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for index, result in enumerate(by_return[:10], 1):
        lines.append(
            "| {rank} | {name} | {ret} | {dd} | {score} | {trades} | {win} | {pnl:.2f} | {fees:.2f} |".format(
                rank=index,
                name=result["name"],
                ret=_pct(float(result["return_pct"])),
                dd=_pct(float(result["max_drawdown"])),
                score=_pct(float(result["optimizer_score"])),
                trades=result["trades"],
                win=_pct(float(result["win_rate"])),
                pnl=float(result["total_pnl"]),
                fees=float(result["fees"]),
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
        OUTPUT_CONFIG_PATH.write_text(
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
                f"- Wrote optimized config: {OUTPUT_CONFIG_PATH}",
            ]
        )
    if failures:
        lines.extend(["", "## Failures", ""])
        lines.extend(f"- {failure}" for failure in failures[:20])

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    if not results:
        return f"No successful optimizer candidates. Report={REPORT_PATH}"
    best = results[0]
    return (
        "IBKR CSV 1Y optimizer done: "
        f"candidates={len(results)}, best={best['name']}, "
        f"return={_pct(float(best['return_pct']))}, max_dd={_pct(float(best['max_drawdown']))}, "
        f"trades={best['trades']}, report={REPORT_PATH}"
    )


if __name__ == "__main__":
    print(run())

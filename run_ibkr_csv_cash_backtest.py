from __future__ import annotations

import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parent
REPO = WORKSPACE if (WORKSPACE / "soxswing").exists() else Path(r"C:\Users\Administrator\Documents\Codex\ty")
HISTORICAL_DIR = WORKSPACE / "ibkr_runtime" / "historical"
REPORT_PATH = WORKSPACE / "ibkr_runtime" / "reports" / "backtest_cash_realistic_ibkr_1y.md"

sys.path.insert(0, str(REPO))

from soxswing.backtest_cash_realistic import _merge_dict, _simulate_variant  # noqa: E402
from soxswing.models import IntradayBar  # noqa: E402
from soxswing.universe import load_symbols  # noqa: E402


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def _load_ibkr_csv(path: Path) -> list[IntradayBar]:
    bars: list[IntradayBar] = []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            timestamp = datetime.fromisoformat(row["timestamp"]).astimezone(UTC)
            bars.append(
                IntradayBar(
                    timestamp=timestamp,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(float(row["volume"] or 0)),
                )
            )
    return sorted(bars, key=lambda bar: bar.timestamp)


def _load_historical_bars(historical_dir: Path) -> dict[str, list[IntradayBar]]:
    bars: dict[str, list[IntradayBar]] = {}
    for path in sorted(historical_dir.glob("*_1y_15mins.csv")):
        symbol = path.name.split("_", 1)[0].upper()
        bars[symbol] = _load_ibkr_csv(path)
    if not bars:
        raise RuntimeError(f"No IBKR CSV files found in {historical_dir}")
    return bars


def _pct(value: float) -> str:
    return f"{value:.2%}"


def run() -> str:
    sweep_path = REPO / "config.cash_realistic_sweep.ibkr_tiered_60d.json"
    sweep = _load_json(sweep_path)
    base_path = REPO / str(sweep["base_config"])
    base_config = _load_json(base_path)
    execution = sweep.get("execution", {})
    preloaded_bars = _load_historical_bars(HISTORICAL_DIR)
    available_symbols = sorted(preloaded_bars)

    timestamps = sorted(
        set.intersection(*(set(bar.timestamp for bar in symbol_bars) for symbol_bars in preloaded_bars.values()))
    )
    if not timestamps:
        raise RuntimeError("No synchronized IBKR timestamps found.")

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
        symbols = load_symbols(config["strategy"], REPO)
        missing = sorted(set(symbols) - set(available_symbols))
        if missing:
            failures.append(f"{name}: missing CSV data for {', '.join(missing)}")
            continue
        try:
            results.append(
                _simulate_variant(
                    name,
                    config,
                    execution,
                    base_path,
                    range_days=365,
                    preloaded_bars=preloaded_bars,
                    preloaded_errors={},
                )
            )
        except Exception as exc:
            failures.append(f"{name}: {exc}")

    results.sort(key=lambda item: item["score"], reverse=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# IBKR 1Y Cash-Start Realistic Intraday Backtest",
        "",
        f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"- Source CSV: {HISTORICAL_DIR}",
        f"- Synchronized bars: {len(timestamps):,}",
        f"- First bar UTC: {timestamps[0].isoformat()}",
        f"- Last bar UTC: {timestamps[-1].isoformat()}",
        f"- Available symbols: {', '.join(available_symbols)}",
        f"- Execution: commission/share={execution.get('commission_per_share')}, "
        f"minimum={execution.get('minimum_commission')}, slippage_bps={execution.get('slippage_bps')}",
        "",
        "| Rank | Variant | Return | Max DD | Score | Trades | Win Rate | PnL | Fees | Symbols |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for index, result in enumerate(results, 1):
        lines.append(
            "| {rank} | {name} | {ret} | {dd} | {score} | {trades} | {win} | {pnl:.2f} | {fees:.2f} | {symbols} |".format(
                rank=index,
                name=result["name"],
                ret=_pct(float(result["return_pct"])),
                dd=_pct(float(result["max_drawdown"])),
                score=_pct(float(result["score"])),
                trades=result["trades"],
                win=_pct(float(result["win_rate"])),
                pnl=float(result["total_pnl"]),
                fees=float(result["fees"]),
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
        lines.extend(["", "## Skipped / Failures", ""])
        lines.extend(f"- {failure}" for failure in failures)

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    if not results:
        return f"No successful variants. Report={REPORT_PATH}"
    best = results[0]
    return (
        "IBKR CSV 1Y cash realistic backtest done: "
        f"best={best['name']}, return={_pct(float(best['return_pct']))}, "
        f"max_dd={_pct(float(best['max_drawdown']))}, trades={best['trades']}, report={REPORT_PATH}"
    )


if __name__ == "__main__":
    print(run())

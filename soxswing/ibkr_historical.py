from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .ibkr import IbkrBroker, IbkrError


def _load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _bar_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone().isoformat()
    return str(value)


def fetch_symbol(
    config: dict[str, Any],
    symbol: str,
    duration: str,
    bar_size: str,
    use_rth: bool,
    output_dir: Path,
) -> Path:
    broker = IbkrBroker(config)
    ib = broker.connect()
    try:
        from ib_insync import Stock

        ib.RequestTimeout = float(config.get("historical_data", {}).get("request_timeout_seconds", 90))
        contract = Stock(symbol.upper(), "SMART", "USD")
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise IbkrError(f"IBKR could not qualify stock contract for {symbol}.")

        bars = ib.reqHistoricalData(
            qualified[0],
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=use_rth,
            formatDate=1,
            keepUpToDate=False,
        )
        if not bars:
            raise IbkrError(f"No historical bars returned for {symbol}.")

        output_dir.mkdir(parents=True, exist_ok=True)
        safe_duration = duration.lower().replace(" ", "")
        safe_bar = bar_size.lower().replace(" ", "")
        path = output_dir / f"{symbol.upper()}_{safe_duration}_{safe_bar}.csv"
        with path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=[
                    "timestamp",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "average",
                    "bar_count",
                ],
            )
            writer.writeheader()
            for bar in bars:
                writer.writerow(
                    {
                        "timestamp": _bar_timestamp(bar.date),
                        "open": float(bar.open),
                        "high": float(bar.high),
                        "low": float(bar.low),
                        "close": float(bar.close),
                        "volume": int(bar.volume),
                        "average": float(getattr(bar, "average", 0.0) or 0.0),
                        "bar_count": int(getattr(bar, "barCount", 0) or 0),
                    }
                )
        return path
    finally:
        broker.disconnect()


def run(config_path: Path, symbols: list[str], duration: str, bar_size: str, output_dir: Path, use_rth: bool) -> str:
    config = _load_config(config_path)
    paths: list[Path] = []
    failures: list[str] = []
    for index, symbol in enumerate(symbols):
        try:
            paths.append(fetch_symbol(config, symbol, duration, bar_size, use_rth, output_dir))
        except Exception as exc:
            failures.append(f"{symbol}: {exc}")
        if index < len(symbols) - 1:
            time.sleep(2)

    report = output_dir / "ibkr_historical_fetch_report.md"
    output_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# IBKR Historical Data Fetch",
        "",
        f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"- Config: {config_path}",
        f"- Duration: {duration}",
        f"- Bar size: {bar_size}",
        f"- RTH only: {use_rth}",
        "",
        "## Files",
        "",
    ]
    lines.extend(f"- {path}" for path in paths)
    if failures:
        lines.extend(["", "## Failures", ""])
        lines.extend(f"- {failure}" for failure in failures)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return f"IBKR historical fetch done: files={len(paths)}, failures={len(failures)}, report={report}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch IBKR historical bars into CSV cache.")
    parser.add_argument("--config", default="config.ibkr.cash_start.example.json")
    parser.add_argument("--symbols", nargs="+", default=["SOXL", "SOXS"])
    parser.add_argument("--duration", default="1 Y")
    parser.add_argument("--bar-size", default="15 mins")
    parser.add_argument("--out", default="data/ibkr_historical")
    parser.add_argument("--include-extended-hours", action="store_true")
    args = parser.parse_args()
    print(
        run(
            Path(args.config),
            args.symbols,
            args.duration,
            args.bar_size,
            Path(args.out),
            use_rth=not args.include_extended_hours,
        )
    )


if __name__ == "__main__":
    main()

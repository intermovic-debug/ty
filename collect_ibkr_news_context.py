from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from soxswing.ibkr import IbkrBroker, IbkrError
from soxswing.market_context import build_context_from_headlines, write_market_context
from soxswing.universe import load_universe_entries


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def _clean_headline(value: Any) -> str:
    headline = str(value or "").strip()
    return re.sub(r"^(?:\{[^}]+\}\s*)+", "", headline).strip()


def _paper_accounts_only(broker: IbkrBroker) -> None:
    accounts = [str(account) for account in broker.connect().managedAccounts()]
    if not accounts or any(not account.upper().startswith("DU") for account in accounts):
        raise IbkrError(f"Paper-only collector expected DU accounts, got: {accounts or ['unknown']}")


def _benchmark_change_pct(ib: Any, symbol: str) -> float:
    from ib_insync import Stock

    contract = Stock(symbol, "SMART", "USD")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        return 0.0
    bars = ib.reqHistoricalData(
        qualified[0],
        endDateTime="",
        durationStr="5 D",
        barSizeSetting="1 day",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=2,
        keepUpToDate=False,
    )
    if len(bars) < 2 or float(bars[-2].close) <= 0:
        return 0.0
    return (float(bars[-1].close) / float(bars[-2].close) - 1.0) * 100.0


def _collect_headlines(
    broker: IbkrBroker,
    symbols: list[str],
    provider_codes: list[str],
    lookback_hours: float,
    max_per_symbol: int,
) -> tuple[list[str], list[dict[str, str]], dict[str, int]]:
    from ib_insync import Stock
    from ib_insync.util import formatIBDatetime

    ib = broker.connect()
    end = datetime.now(tz=UTC)
    start = end - timedelta(hours=lookback_hours)
    end_text = formatIBDatetime(end)
    start_text = formatIBDatetime(start)
    provider_text = "+".join(provider_codes)
    headlines: list[str] = []
    sources: list[dict[str, str]] = []
    counts: dict[str, int] = {}
    seen: set[str] = set()
    seen_providers: set[str] = set()

    for symbol in symbols:
        contract = Stock(symbol, "SMART", "USD")
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            counts[symbol] = 0
            continue
        rows = ib.reqHistoricalNews(
            qualified[0].conId,
            provider_text,
            start_text,
            end_text,
            int(max_per_symbol),
        )
        added = 0
        for row in rows:
            headline = _clean_headline(getattr(row, "headline", ""))
            key = headline.casefold()
            if not headline or key in seen:
                continue
            seen.add(key)
            headlines.append(headline)
            provider = str(getattr(row, "providerCode", "IBKR"))
            if provider not in seen_providers:
                sources.append({"name": f"IBKR {provider}", "url": ""})
                seen_providers.add(provider)
            added += 1
        counts[symbol] = added
    return headlines, sources, counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect subscribed IBKR headlines and build a risk-only daily market context."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.ibkr.regime_paper.example.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/market_context_latest.json"),
    )
    parser.add_argument("--lookback-hours", type=float, default=24.0)
    parser.add_argument("--valid-hours", type=float, default=18.0)
    parser.add_argument("--minimum-headlines", type=int, default=5)
    parser.add_argument("--max-per-symbol", type=int, default=50)
    parser.add_argument(
        "--provider-codes",
        default="BRFG,BRFUPDN,DJNL",
        help="Comma-separated IBKR news provider codes; subscriptions are required.",
    )
    args = parser.parse_args()

    config = _load_json(args.config)
    strategy = config.get("strategy", {})
    entries = load_universe_entries(strategy, args.config.resolve().parent)
    symbols = sorted({entry.symbol for entry in entries} | set(strategy.get("benchmarks", [])))
    broker = IbkrBroker(config)
    try:
        _paper_accounts_only(broker)
        ib = broker.connect()
        available = {str(provider.code): str(provider.name) for provider in ib.reqNewsProviders()}
        requested = [code.strip() for code in args.provider_codes.split(",") if code.strip()]
        selected = [code for code in requested if code in available]
        if not selected:
            raise IbkrError(
                "None of the requested IBKR news providers are available. "
                f"Requested={requested}, available={sorted(available)}"
            )
        headlines, sources, counts = _collect_headlines(
            broker,
            symbols,
            selected,
            args.lookback_hours,
            args.max_per_symbol,
        )
        benchmark_change = _benchmark_change_pct(ib, "SPY")
        context = build_context_from_headlines(
            headlines,
            generated_at=datetime.now(tz=UTC),
            valid_hours=args.valid_hours,
            benchmark_change_pct=benchmark_change,
            sources=sources,
            minimum_headlines=args.minimum_headlines,
        )
        write_market_context(args.output, context)
        print(
            json.dumps(
                {
                    "output": str(args.output.resolve()),
                    "providers": {code: available[code] for code in selected},
                    "headline_count": len(headlines),
                    "headlines_by_symbol": counts,
                    "spy_change_pct": round(benchmark_change, 4),
                    "risk_level": context.risk_level,
                    "regime_hint": context.regime_hint,
                    "valid_until": context.valid_until.isoformat(),
                },
                indent=2,
            )
        )
    finally:
        broker.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

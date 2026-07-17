from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from soxswing.market_context import RISK_MULTIPLIERS, build_context_from_headlines, write_market_context


RISK_ORDER = {name: index for index, name in enumerate(RISK_MULTIPLIERS)}


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as file:
        raw = json.load(file)
    if not isinstance(raw, dict):
        raise ValueError("The headline input must be a JSON object.")
    return raw


def _extract_headlines(raw: dict[str, Any]) -> tuple[list[str], list[dict[str, str]]]:
    headlines: list[str] = []
    sources: list[dict[str, str]] = []
    seen_sources: set[tuple[str, str]] = set()
    for item in raw.get("headlines", []):
        if isinstance(item, str):
            text = item.strip()
            source_name = "manual"
            url = ""
        elif isinstance(item, dict):
            text = str(item.get("text", item.get("headline", ""))).strip()
            source_name = str(item.get("source", "manual")).strip()
            url = str(item.get("url", "")).strip()
        else:
            continue
        if not text:
            continue
        headlines.append(text)
        source_key = (source_name, url)
        if source_key not in seen_sources:
            sources.append({"name": source_name, "url": url})
            seen_sources.add(source_key)
    return headlines, sources


def _risk_floor(calculated: str, requested: str | None) -> str:
    if not requested:
        return calculated
    requested = requested.strip().lower()
    if requested not in RISK_ORDER:
        raise ValueError(f"Unsupported risk_floor: {requested}")
    return requested if RISK_ORDER[requested] > RISK_ORDER[calculated] else calculated


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a fresh risk-only market context file from timestamped headlines."
    )
    parser.add_argument("--input", type=Path, required=True, help="Headline JSON input file")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/market_context_latest.json"),
        help="Generated context JSON path",
    )
    parser.add_argument("--valid-hours", type=float, default=18.0)
    parser.add_argument("--minimum-headlines", type=int, default=3)
    args = parser.parse_args()

    raw = _load_json(args.input)
    headlines, sources = _extract_headlines(raw)
    context = build_context_from_headlines(
        headlines,
        generated_at=datetime.now(tz=UTC),
        valid_hours=args.valid_hours,
        benchmark_change_pct=float(raw.get("benchmark_change_pct", 0.0)),
        sources=sources,
        minimum_headlines=args.minimum_headlines,
    )
    risk_level = _risk_floor(context.risk_level, raw.get("risk_floor"))
    context = replace(
        context,
        risk_level=risk_level,
        blocked_symbols=frozenset(
            str(symbol).strip().upper() for symbol in raw.get("blocked_symbols", []) if str(symbol).strip()
        ),
        blocked_groups=frozenset(
            str(group).strip().lower() for group in raw.get("blocked_groups", []) if str(group).strip()
        ),
    )
    write_market_context(args.output, context)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "generated_at": context.generated_at.isoformat(),
                "valid_until": context.valid_until.isoformat(),
                "headline_count": len(headlines),
                "risk_level": context.risk_level,
                "regime_hint": context.regime_hint,
                "risk_multiplier": context.risk_multiplier,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class UniverseEntry:
    symbol: str
    group: str
    correlation_group: str
    direction: str
    leverage: float
    benchmark: str
    allowed_regimes: tuple[str, ...]
    max_position_pct: float | None = None


def _entry_from_raw(raw: Any, group_name: str) -> UniverseEntry | None:
    if isinstance(raw, str):
        symbol = raw.strip().upper()
        values: dict[str, Any] = {}
    elif isinstance(raw, dict):
        symbol = str(raw.get("symbol", "")).strip().upper()
        values = raw
    else:
        return None
    if not symbol:
        return None

    max_position = values.get("max_position_pct")
    raw_regimes = values.get("allowed_regimes", ["risk_on", "mixed", "risk_off"])
    allowed_regimes = tuple(
        str(item).strip().lower()
        for item in raw_regimes
        if str(item).strip().lower() in {"risk_on", "mixed", "risk_off"}
    )
    return UniverseEntry(
        symbol=symbol,
        group=group_name or "Configured",
        correlation_group=str(values.get("correlation_group", group_name or symbol)).strip().lower(),
        direction=str(values.get("direction", "long")).strip().lower(),
        leverage=float(values.get("leverage", 1.0)),
        benchmark=str(values.get("benchmark", symbol)).strip().upper(),
        allowed_regimes=allowed_regimes or ("mixed",),
        max_position_pct=float(max_position) if max_position is not None else None,
    )


def load_universe_entries(
    strategy: dict[str, Any],
    base_path: Path | None = None,
) -> list[UniverseEntry]:
    entries = [
        entry
        for symbol in strategy.get("symbols", [])
        if (entry := _entry_from_raw(symbol, "Configured")) is not None
    ]
    universe_path = strategy.get("universe_path")
    if universe_path:
        path = Path(str(universe_path))
        if not path.is_absolute() and base_path is not None:
            path = base_path / path
        if path.exists():
            with path.open("r", encoding="utf-8") as file:
                universe = json.load(file)
            active_groups = {
                str(name).strip().lower()
                for name in strategy.get("active_groups", [])
                if str(name).strip()
            }
            entries = []
            for group in universe.get("groups", []):
                display_name = str(group.get("name", "")).strip()
                if active_groups and display_name.lower() not in active_groups:
                    continue
                for raw in group.get("symbols", []):
                    entry = _entry_from_raw(raw, display_name)
                    if entry is not None:
                        entries.append(entry)

    by_symbol = {entry.symbol: entry for entry in entries}
    return [by_symbol[symbol] for symbol in sorted(by_symbol)]


def load_symbols(strategy: dict[str, Any], base_path: Path | None = None) -> list[str]:
    return [entry.symbol for entry in load_universe_entries(strategy, base_path)]

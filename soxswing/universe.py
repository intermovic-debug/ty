from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_symbols(strategy: dict[str, Any], base_path: Path | None = None) -> list[str]:
    symbols = list(strategy.get("symbols", []))
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
            symbols = []
            for group in universe.get("groups", []):
                group_name = str(group.get("name", "")).strip().lower()
                if active_groups and group_name not in active_groups:
                    continue
                symbols.extend(group.get("symbols", []))

    return sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})

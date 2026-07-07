from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from .config import AccountConfig
from .models import Position


def load_state(path: Path, account: AccountConfig) -> dict[str, Any]:
    if not path.exists():
        return {
            "cash": account.starting_cash,
            "position": None,
            "last_entry_date": None,
        }
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_state(path: Path, cash: float, position: Position | None, last_entry_date: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cash": round(cash, 2),
        "position": asdict(position) if position else None,
        "last_entry_date": last_entry_date,
        "updated_at": date.today().isoformat(),
    }
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)


def position_from_state(raw: dict[str, Any]) -> Position | None:
    position = raw.get("position")
    if not position:
        return None
    return Position(
        symbol=position["symbol"],
        quantity=int(position["quantity"]),
        entry_price=float(position["entry_price"]),
        stop_price=float(position["stop_price"]),
        take_profit_price=float(position["take_profit_price"]),
        opened_at=position["opened_at"],
    )

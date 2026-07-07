from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from .models import Order, Signal


def append_trade_log(path: Path, signal: Signal, order: Order | None, status: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "timestamp",
                "action",
                "symbol",
                "score",
                "price",
                "quantity",
                "status",
                "reason",
            ],
        )
        if is_new:
            writer.writeheader()
        writer.writerow(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "action": signal.action,
                "symbol": signal.symbol or "",
                "score": signal.score,
                "price": signal.price or "",
                "quantity": order.quantity if order else "",
                "status": status,
                "reason": signal.reason,
            }
        )

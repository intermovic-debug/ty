from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from .config import StrategyConfig
from .models import Order, Position


class BrokerError(RuntimeError):
    pass


class PaperBroker:
    def __init__(self, cash: float, position: Position | None, strategy: StrategyConfig):
        self.cash = cash
        self.position = position
        self.strategy = strategy

    def submit_order(self, order: Order) -> dict[str, object]:
        if order.side == "buy":
            if self.position:
                raise BrokerError("Cannot buy while a position is already open")
            notional = order.quantity * order.price
            if notional > self.cash:
                raise BrokerError("Insufficient paper cash")
            if order.stop_price is None or order.take_profit_price is None:
                raise BrokerError("Entry order must include stop and take-profit prices")
            self.cash -= notional
            self.position = Position(
                symbol=order.symbol,
                quantity=order.quantity,
                entry_price=order.price,
                stop_price=order.stop_price,
                take_profit_price=order.take_profit_price,
                opened_at=datetime.now().isoformat(timespec="seconds"),
            )
        elif order.side == "sell":
            if not self.position:
                raise BrokerError("Cannot sell without an open position")
            self.cash += order.quantity * order.price
            self.position = None
        else:
            raise BrokerError(f"Unsupported side: {order.side}")

        return {
            "status": "filled",
            "order": asdict(order),
            "cash": round(self.cash, 2),
            "position": asdict(self.position) if self.position else None,
        }


class RealBrokerUnavailable:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise BrokerError("Real broker integration is not implemented. Keep broker=paper.")

from __future__ import annotations

from .config import AccountConfig, StrategyConfig
from .models import Order, Signal


def build_entry_order(
    signal: Signal,
    cash: float,
    account: AccountConfig,
    strategy: StrategyConfig,
) -> Order | None:
    if signal.action != "enter" or not signal.symbol or not signal.price or not signal.atr:
        return None

    max_notional = cash * account.max_position_pct
    risk_budget = cash * account.risk_per_trade_pct
    stop_distance = max(signal.atr * strategy.stop_atr_multiple, signal.price * 0.02)
    take_profit_distance = max(signal.atr * strategy.take_profit_atr_multiple, signal.price * 0.03)
    quantity_by_notional = int(max_notional // signal.price)
    quantity_by_risk = int(risk_budget // stop_distance)
    quantity = max(0, min(quantity_by_notional, quantity_by_risk))

    if quantity < 1:
        return None

    return Order(
        symbol=signal.symbol,
        side="buy",
        quantity=quantity,
        price=signal.price,
        reason=signal.reason,
        stop_price=round(signal.price - stop_distance, 2),
        take_profit_price=round(signal.price + take_profit_distance, 2),
    )


def build_exit_order(signal: Signal, quantity: int) -> Order | None:
    if signal.action != "exit" or not signal.symbol or not signal.price:
        return None
    return Order(
        symbol=signal.symbol,
        side="sell",
        quantity=quantity,
        price=signal.price,
        reason=signal.reason,
    )

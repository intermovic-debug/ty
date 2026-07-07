from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from datetime import datetime


@dataclass(frozen=True)
class Bar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class Indicators:
    close: float
    fast_ma: float
    slow_ma: float
    rsi: float
    atr: float
    momentum: float


@dataclass(frozen=True)
class Signal:
    symbol: str | None
    action: str
    score: int
    reason: str
    price: float | None = None
    atr: float | None = None


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: int
    entry_price: float
    stop_price: float
    take_profit_price: float
    opened_at: str


@dataclass(frozen=True)
class Order:
    symbol: str
    side: str
    quantity: int
    price: float
    reason: str
    stop_price: float | None = None
    take_profit_price: float | None = None


@dataclass(frozen=True)
class IntradayBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class IntradayPosition:
    symbol: str
    quantity: int
    entry_price: float
    stop_price: float
    take_profit_price: float
    trailing_stop_price: float
    peak_price: float
    opened_at: str

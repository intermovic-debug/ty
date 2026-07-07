from __future__ import annotations

from .models import Bar, Indicators


def simple_ma(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"Need at least {period} values")
    return sum(values[-period:]) / period


def rsi(values: list[float], period: int) -> float:
    if len(values) <= period:
        raise ValueError(f"Need more than {period} values")

    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(values[-period - 1 : -1], values[-period:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))

    average_gain = sum(gains) / period
    average_loss = sum(losses) / period
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def atr(bars: list[Bar], period: int) -> float:
    if len(bars) <= period:
        raise ValueError(f"Need more than {period} bars")

    true_ranges: list[float] = []
    window = bars[-period - 1 :]
    for previous, current in zip(window[:-1], window[1:]):
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return sum(true_ranges) / period


def build_indicators(
    bars: list[Bar],
    fast_ma_period: int,
    slow_ma_period: int,
    rsi_period: int,
    atr_period: int,
    momentum_days: int,
) -> Indicators:
    closes = [bar.close for bar in bars]
    latest = bars[-1]
    previous = bars[-1 - momentum_days]
    return Indicators(
        close=latest.close,
        fast_ma=simple_ma(closes, fast_ma_period),
        slow_ma=simple_ma(closes, slow_ma_period),
        rsi=rsi(closes, rsi_period),
        atr=atr(bars, atr_period),
        momentum=(latest.close / previous.close) - 1,
    )

from __future__ import annotations

from .config import StrategyConfig
from .indicators import build_indicators
from .models import Bar, Indicators, Position, Signal


def score_symbol(indicators: Indicators) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    if indicators.fast_ma > indicators.slow_ma:
        score += 2
        reasons.append("fast MA above slow MA")
    else:
        reasons.append("fast MA below slow MA")

    if 45 <= indicators.rsi <= 72:
        score += 1
        reasons.append(f"RSI in tradable range ({indicators.rsi:.1f})")
    elif indicators.rsi > 78:
        score -= 1
        reasons.append(f"RSI overheated ({indicators.rsi:.1f})")
    else:
        reasons.append(f"RSI weak ({indicators.rsi:.1f})")

    if indicators.momentum > 0:
        score += 1
        reasons.append(f"positive momentum ({indicators.momentum:.2%})")
    else:
        reasons.append(f"negative momentum ({indicators.momentum:.2%})")

    return score, reasons


def choose_signal(
    symbol_bars: dict[str, list[Bar]],
    config: StrategyConfig,
    position: Position | None,
) -> Signal:
    if position:
        current = symbol_bars[position.symbol][-1].close
        if current <= position.stop_price:
            return Signal(position.symbol, "exit", 99, "stop loss reached", current)
        if current >= position.take_profit_price:
            return Signal(position.symbol, "exit", 99, "take profit reached", current)
        return Signal(position.symbol, "hold", 0, "position remains inside stop/profit band", current)

    candidates: list[tuple[str, int, list[str], Indicators]] = []
    for symbol in config.symbols:
        indicators = build_indicators(
            symbol_bars[symbol],
            config.fast_ma,
            config.slow_ma,
            config.rsi_period,
            config.atr_period,
            config.momentum_days,
        )
        score, reasons = score_symbol(indicators)
        candidates.append((symbol, score, reasons, indicators))

    candidates.sort(key=lambda item: item[1], reverse=True)
    symbol, score, reasons, indicators = candidates[0]
    other_symbol, other_score, _, _ = candidates[1]

    if score < config.min_signal_score:
        return Signal(None, "flat", score, f"best score too low: {symbol}={score}")
    if score == other_score:
        return Signal(None, "flat", score, f"tie between {symbol} and {other_symbol}")

    return Signal(
        symbol=symbol,
        action="enter",
        score=score,
        reason="; ".join(reasons),
        price=indicators.close,
        atr=indicators.atr,
    )

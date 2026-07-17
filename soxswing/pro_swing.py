from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any

from .market_context import MarketContext
from .models import IntradayBar
from .universe import UniverseEntry


@dataclass(frozen=True)
class RegimeAssessment:
    label: str
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class KneeSignal:
    symbol: str
    eligible: bool
    score: int
    price: float
    stop_price: float
    atr: float
    rsi: float
    volume_ratio: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ShoulderDecision:
    should_exit: bool
    reason: str
    price: float
    peak_price: float
    active_stop: float
    trailing_active: bool


@dataclass(frozen=True)
class PositionSizeDecision:
    allowed: bool
    quantity: int
    notional: float
    risk_budget: float
    reason: str


def _ema(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"Need at least {period} values")
    multiplier = 2.0 / (period + 1.0)
    value = sum(values[:period]) / period
    for current in values[period:]:
        value = current * multiplier + value * (1.0 - multiplier)
    return value


def _rsi(values: list[float], period: int) -> float:
    if len(values) <= period:
        raise ValueError(f"Need more than {period} values")
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(values[-period - 1 : -1], values[-period:]):
        change = current - previous
        gains.append(max(0.0, change))
        losses.append(max(0.0, -change))
    average_gain = sum(gains) / period
    average_loss = sum(losses) / period
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def _atr(bars: list[IntradayBar], period: int) -> float:
    if len(bars) <= period:
        raise ValueError(f"Need more than {period} bars")
    ranges: list[float] = []
    for previous, current in zip(bars[-period - 1 : -1], bars[-period:]):
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return sum(ranges) / period


def assess_regime(
    bars_by_symbol: dict[str, list[IntradayBar]],
    benchmarks: list[str],
    config: dict[str, Any],
) -> RegimeAssessment:
    fast_period = int(config.get("regime_fast_ema", 20))
    slow_period = int(config.get("regime_slow_ema", 50))
    momentum_bars = int(config.get("regime_momentum_bars", 5))
    reasons: list[str] = []
    votes: list[float] = []

    for symbol in benchmarks:
        bars = bars_by_symbol.get(symbol, [])
        if len(bars) < max(slow_period, momentum_bars + 1):
            reasons.append(f"{symbol}: insufficient bars")
            continue
        closes = [bar.close for bar in bars]
        fast = _ema(closes, fast_period)
        slow = _ema(closes, slow_period)
        momentum = closes[-1] / closes[-1 - momentum_bars] - 1.0
        components = [fast > slow, closes[-1] > slow, momentum > 0]
        vote = sum(1.0 for item in components if item) / len(components)
        votes.append(vote)
        reasons.append(
            f"{symbol}: vote={vote:.2f} close={closes[-1]:.2f} "
            f"fast={fast:.2f} slow={slow:.2f} momentum={momentum:.2%}"
        )

    if not votes:
        return RegimeAssessment("unknown", 0.0, tuple(reasons or ["No benchmark data"]))
    score = sum(votes) / len(votes)
    if score >= float(config.get("risk_on_threshold", 0.67)):
        label = "risk_on"
    elif score <= float(config.get("risk_off_threshold", 0.33)):
        label = "risk_off"
    else:
        label = "mixed"
    return RegimeAssessment(label, round(score, 4), tuple(reasons))


def assess_knee_entry(
    entry: UniverseEntry,
    bars: list[IntradayBar],
    config: dict[str, Any],
    preference_bonus: int = 0,
) -> KneeSignal:
    fast_period = int(config.get("entry_fast_ema", 20))
    slow_period = int(config.get("entry_slow_ema", 50))
    rsi_period = int(config.get("rsi_period", 14))
    atr_period = int(config.get("atr_period", 14))
    pullback_lookback = int(config.get("pullback_lookback", 8))
    required = max(slow_period, rsi_period + 1, atr_period + 1, pullback_lookback + 2)
    if len(bars) < required:
        return KneeSignal(entry.symbol, False, 0, 0.0, 0.0, 0.0, 0.0, 0.0, ("insufficient bars",))

    closes = [bar.close for bar in bars]
    latest = bars[-1]
    previous = bars[-2]
    fast = _ema(closes, fast_period)
    slow = _ema(closes, slow_period)
    atr_value = _atr(bars, atr_period)
    rsi_value = _rsi(closes, rsi_period)
    pullback_window = bars[-pullback_lookback - 1 : -1]
    pullback_low = min(bar.low for bar in pullback_window)
    prior_volumes = [bar.volume for bar in bars[-21:-1] if bar.volume > 0]
    median_volume = statistics.median(prior_volumes) if prior_volumes else 0.0
    volume_ratio = latest.volume / median_volume if median_volume > 0 else 1.0

    checks: list[tuple[bool, str]] = []
    checks.append((fast > slow, f"trend fast={fast:.2f} slow={slow:.2f}"))
    checks.append((latest.close > slow, f"close {latest.close:.2f} above slow EMA"))

    tolerance_atr = float(config.get("pullback_tolerance_atr", 0.50))
    max_break_atr = float(config.get("max_pullback_break_atr", 0.75))
    touched = pullback_low <= fast + tolerance_atr * atr_value
    held_structure = pullback_low >= slow - max_break_atr * atr_value
    checks.append((touched and held_structure, f"pullback low={pullback_low:.2f} held trend structure"))

    confirmation = latest.close > previous.high and latest.close > latest.open and latest.close > fast
    checks.append((confirmation, f"confirmation close={latest.close:.2f} prior_high={previous.high:.2f}"))

    min_rsi = float(config.get("min_entry_rsi", 40.0))
    max_rsi = float(config.get("max_entry_rsi", 65.0))
    checks.append((min_rsi <= rsi_value <= max_rsi, f"RSI={rsi_value:.1f}"))

    atr_pct = atr_value / latest.close if latest.close > 0 else math.inf
    max_atr_pct = float(config.get("max_atr_pct", 0.05))
    checks.append((atr_pct <= max_atr_pct, f"ATR/price={atr_pct:.2%}"))

    extension_atr = (latest.close - fast) / atr_value if atr_value > 0 else math.inf
    checks.append(
        (
            extension_atr <= float(config.get("max_entry_extension_atr", 1.25)),
            f"entry extension={extension_atr:.2f} ATR",
        )
    )
    checks.append(
        (
            volume_ratio >= float(config.get("min_volume_ratio", 0.80)),
            f"volume ratio={volume_ratio:.2f}",
        )
    )

    score = sum(1 for passed, _reason in checks if passed) + int(preference_bonus)
    failed = [reason for passed, reason in checks if not passed]
    reasons = tuple(("PASS " if passed else "FAIL ") + reason for passed, reason in checks)
    eligible = not failed and score >= int(config.get("min_entry_checks", len(checks)))

    structural_stop = pullback_low - atr_value * float(config.get("stop_below_pullback_atr", 0.15))
    volatility_stop = latest.close - atr_value * float(config.get("initial_stop_atr", 1.50))
    stop_price = min(structural_stop, volatility_stop)
    return KneeSignal(
        symbol=entry.symbol,
        eligible=eligible,
        score=score,
        price=round(latest.close, 4),
        stop_price=round(max(0.01, stop_price), 4),
        atr=round(atr_value, 4),
        rsi=round(rsi_value, 2),
        volume_ratio=round(volume_ratio, 3),
        reasons=reasons,
    )


def assess_shoulder_exit(
    bars: list[IntradayBar],
    entry_price: float,
    initial_stop: float,
    prior_peak: float,
    bars_held: int,
    config: dict[str, Any],
) -> ShoulderDecision:
    atr_period = int(config.get("atr_period", 14))
    fast_period = int(config.get("exit_fast_ema", 10))
    if len(bars) < max(atr_period + 1, fast_period, 4):
        raise ValueError("Not enough bars for shoulder exit")

    latest = bars[-1]
    closes = [bar.close for bar in bars]
    atr_value = _atr(bars, atr_period)
    fast = _ema(closes, fast_period)
    peak = max(float(prior_peak), latest.high)
    initial_risk = max(0.01, float(entry_price) - float(initial_stop))
    activation_price = float(entry_price) + initial_risk * float(config.get("trail_activation_r", 1.0))
    trailing_active = peak >= activation_price
    chandelier = peak - atr_value * float(config.get("trailing_atr", 2.25))
    active_stop = max(float(initial_stop), chandelier) if trailing_active else float(initial_stop)

    prior_swing_low = min(bars[-2].low, bars[-3].low)
    structure_break = latest.close < prior_swing_low and latest.close < fast
    if latest.low <= float(initial_stop):
        reason = "initial risk stop"
        should_exit = True
    elif trailing_active and latest.low <= active_stop:
        reason = "ATR trailing stop"
        should_exit = True
    elif trailing_active and structure_break:
        reason = "confirmed shoulder structure break"
        should_exit = True
    elif (
        bars_held >= int(config.get("max_hold_bars", 52))
        and latest.close < float(entry_price) + initial_risk * float(config.get("time_stop_min_r", 0.25))
    ):
        reason = "time stop without sufficient progress"
        should_exit = True
    else:
        reason = "trend intact"
        should_exit = False

    return ShoulderDecision(
        should_exit=should_exit,
        reason=reason,
        price=round(latest.close, 4),
        peak_price=round(peak, 4),
        active_stop=round(active_stop, 4),
        trailing_active=trailing_active,
    )


def size_position(
    equity: float,
    available_cash: float,
    current_gross_notional: float,
    daily_realized_pnl: float,
    entry: UniverseEntry,
    signal: KneeSignal,
    account_config: dict[str, Any],
    context: MarketContext,
) -> PositionSizeDecision:
    if not signal.eligible:
        return PositionSizeDecision(False, 0, 0.0, 0.0, "technical entry is not eligible")
    context_allowed, context_reason = context.policy_for(entry)
    if not context_allowed:
        return PositionSizeDecision(False, 0, 0.0, 0.0, context_reason)
    if equity <= 0 or available_cash <= 0:
        return PositionSizeDecision(False, 0, 0.0, 0.0, "no available equity or cash")
    max_daily_loss = equity * float(account_config.get("max_daily_loss_pct", 0.005))
    if daily_realized_pnl <= -max_daily_loss:
        return PositionSizeDecision(False, 0, 0.0, 0.0, "daily loss circuit breaker is active")

    multiplier = context.risk_multiplier
    if multiplier <= 0:
        return PositionSizeDecision(False, 0, 0.0, 0.0, "market context blocks new risk")

    risk_per_share = signal.price - signal.stop_price
    if risk_per_share <= 0:
        return PositionSizeDecision(False, 0, 0.0, 0.0, "invalid stop distance")

    base_position_pct = float(account_config.get("max_position_pct", 0.10))
    if entry.max_position_pct is not None:
        base_position_pct = min(base_position_pct, entry.max_position_pct)
    leverage_penalty = max(1.0, entry.leverage)
    position_cap = equity * base_position_pct * multiplier / leverage_penalty
    gross_cap = equity * float(account_config.get("max_gross_exposure_pct", 0.30)) * multiplier
    gross_remaining = max(0.0, gross_cap - current_gross_notional)
    notional_cap = min(available_cash, position_cap, gross_remaining)
    risk_budget = equity * float(account_config.get("risk_per_trade_pct", 0.0025)) * multiplier

    by_notional = int(notional_cap // signal.price)
    by_risk = int(risk_budget // risk_per_share)
    quantity = max(0, min(by_notional, by_risk))
    max_shares = int(account_config.get("max_shares_per_trade", 0))
    if max_shares > 0:
        quantity = min(quantity, max_shares)
    if quantity <= 0:
        return PositionSizeDecision(False, 0, 0.0, risk_budget, "risk limits size the trade to zero")
    return PositionSizeDecision(
        allowed=True,
        quantity=quantity,
        notional=round(quantity * signal.price, 2),
        risk_budget=round(risk_budget, 2),
        reason="sized by stop risk, context, leverage, and gross exposure",
    )


def rank_knee_candidates(
    entries: list[UniverseEntry],
    bars_by_symbol: dict[str, list[IntradayBar]],
    regime: RegimeAssessment,
    context: MarketContext,
    config: dict[str, Any],
) -> tuple[list[tuple[UniverseEntry, KneeSignal]], dict[str, str]]:
    ranked: list[tuple[UniverseEntry, KneeSignal]] = []
    blocked: dict[str, str] = {}
    for entry in entries:
        if regime.label == "unknown":
            blocked[entry.symbol] = "technical regime is unknown"
            continue
        if regime.label not in entry.allowed_regimes:
            blocked[entry.symbol] = f"{entry.group} is disabled in {regime.label} regime"
            continue
        allowed, reason = context.policy_for(entry)
        if not allowed:
            blocked[entry.symbol] = reason
            continue
        bars = bars_by_symbol.get(entry.symbol)
        if not bars:
            blocked[entry.symbol] = "missing market bars"
            continue
        signal = assess_knee_entry(entry, bars, config, context.preference_bonus(entry))
        ranked.append((entry, signal))
    ranked.sort(key=lambda item: (item[1].eligible, item[1].score), reverse=True)
    return ranked, blocked

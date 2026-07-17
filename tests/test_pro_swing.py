from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from soxswing.market_context import context_from_dict
from soxswing.models import IntradayBar
from soxswing.pro_swing import (
    KneeSignal,
    assess_knee_entry,
    assess_regime,
    assess_shoulder_exit,
    size_position,
)
from soxswing.universe import UniverseEntry


def _entry() -> UniverseEntry:
    return UniverseEntry(
        symbol="QQQ",
        group="Broad Market 1x",
        correlation_group="us_equity",
        direction="long",
        leverage=1.0,
        benchmark="QQQ",
        allowed_regimes=("risk_on", "mixed"),
        max_position_pct=0.1,
    )


def _bars(closes: list[float], volumes: list[int] | None = None) -> list[IntradayBar]:
    start = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)
    rows: list[IntradayBar] = []
    for index, close in enumerate(closes):
        previous = closes[index - 1] if index else close
        open_price = previous
        rows.append(
            IntradayBar(
                timestamp=start + timedelta(minutes=15 * index),
                open=open_price,
                high=max(open_price, close) + 0.18,
                low=min(open_price, close) - 0.18,
                close=close,
                volume=(volumes[index] if volumes else 1000),
            )
        )
    return rows


def _fresh_context():
    now = datetime.now(tz=UTC)
    return context_from_dict(
        {
            "generated_at": now.isoformat(),
            "valid_until": (now + timedelta(hours=4)).isoformat(),
            "regime_hint": "mixed",
            "risk_level": "normal",
            "headline_score": 0,
        }
    )


class ProSwingTests(unittest.TestCase):
    def test_regime_detects_broad_uptrend(self) -> None:
        closes = [100 + index * 0.12 for index in range(70)]
        bars = _bars(closes)
        regime = assess_regime({"SPY": bars, "QQQ": bars, "SOXX": bars}, ["SPY", "QQQ", "SOXX"], {})
        self.assertEqual(regime.label, "risk_on")

    def test_knee_requires_reversal_not_waterfall(self) -> None:
        base = [100 + index * 0.08 + (0.18 if index % 2 == 0 else -0.12) for index in range(61)]
        pullback_and_reversal = base + [104.55, 104.30, 104.05, 103.85, 103.95, 104.10, 104.25, 104.35, 104.85]
        config = {
            "min_entry_rsi": 35,
            "max_entry_rsi": 72,
            "max_entry_extension_atr": 1.5,
        }
        reversal = assess_knee_entry(_entry(), _bars(pullback_and_reversal), config)
        waterfall = assess_knee_entry(_entry(), _bars(base + [104, 103.5, 103, 102.5, 102, 101.5, 101, 100.5, 100]), config)
        self.assertTrue(reversal.eligible, reversal.reasons)
        self.assertFalse(waterfall.eligible)
        self.assertTrue(any(reason.startswith("FAIL") for reason in waterfall.reasons))

    def test_shoulder_exit_waits_for_profit_then_trails(self) -> None:
        rising = [100 + index * 0.28 for index in range(20)]
        bars = _bars(rising + [105.0, 104.8, 102.0])
        decision = assess_shoulder_exit(
            bars,
            entry_price=100,
            initial_stop=98,
            prior_peak=105.5,
            bars_held=10,
            config={"trailing_atr": 2.0},
        )
        self.assertTrue(decision.trailing_active)
        self.assertTrue(decision.should_exit)
        self.assertIn(decision.reason, {"ATR trailing stop", "confirmed shoulder structure break"})

    def test_initial_stop_detects_intrabar_breach(self) -> None:
        bars = _bars([100 + (index % 2) * 0.2 for index in range(20)])
        latest = bars[-1]
        bars[-1] = IntradayBar(
            timestamp=latest.timestamp,
            open=100,
            high=101,
            low=97.5,
            close=100.5,
            volume=1000,
        )
        decision = assess_shoulder_exit(
            bars,
            entry_price=100,
            initial_stop=98,
            prior_peak=100,
            bars_held=2,
            config={},
        )
        self.assertTrue(decision.should_exit)
        self.assertEqual(decision.reason, "initial risk stop")

    def test_position_sizing_and_daily_loss_guard(self) -> None:
        signal = KneeSignal(
            symbol="QQQ",
            eligible=True,
            score=8,
            price=100,
            stop_price=98,
            atr=1,
            rsi=52,
            volume_ratio=1.1,
            reasons=("PASS",),
        )
        account = {
            "risk_per_trade_pct": 0.0025,
            "max_daily_loss_pct": 0.005,
            "max_position_pct": 0.1,
            "max_gross_exposure_pct": 0.3,
            "max_shares_per_trade": 5,
        }
        allowed = size_position(100_000, 50_000, 0, 0, _entry(), signal, account, _fresh_context())
        self.assertTrue(allowed.allowed)
        self.assertEqual(allowed.quantity, 5)
        blocked = size_position(100_000, 50_000, 0, -500, _entry(), signal, account, _fresh_context())
        self.assertFalse(blocked.allowed)
        self.assertIn("daily loss", blocked.reason)

        now = datetime.now(tz=UTC)
        stale = context_from_dict(
            {
                "generated_at": (now - timedelta(days=2)).isoformat(),
                "valid_until": (now - timedelta(days=1)).isoformat(),
                "regime_hint": "mixed",
                "risk_level": "normal",
                "headline_score": 0,
            }
        )
        stale_block = size_position(100_000, 50_000, 0, 0, _entry(), signal, account, stale)
        self.assertFalse(stale_block.allowed)
        self.assertIn("stale", stale_block.reason)


if __name__ == "__main__":
    unittest.main()

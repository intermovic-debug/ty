from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from soxswing.market_context import build_context_from_headlines, context_from_dict
from soxswing.universe import UniverseEntry


def _entry(symbol: str = "SOXL", leverage: float = 3.0) -> UniverseEntry:
    return UniverseEntry(
        symbol=symbol,
        group="Leveraged Research Only",
        correlation_group="semiconductors",
        direction="long",
        leverage=leverage,
        benchmark="SOXX",
        allowed_regimes=("risk_on",),
        max_position_pct=0.04,
    )


class MarketContextTests(unittest.TestCase):
    def test_no_headlines_fails_closed(self) -> None:
        context = build_context_from_headlines([], generated_at=datetime.now(tz=UTC))
        self.assertEqual(context.risk_level, "extreme")
        self.assertEqual(context.risk_multiplier, 0.0)

    def test_unrecognized_headlines_reduce_risk(self) -> None:
        context = build_context_from_headlines(
            ["Macro calendar update", "Sector positioning update", "Treasury market update"],
            generated_at=datetime.now(tz=UTC),
        )
        self.assertEqual(context.risk_level, "caution")
        self.assertEqual(context.risk_multiplier, 0.65)

    def test_risk_off_blocks_leveraged_long(self) -> None:
        now = datetime.now(tz=UTC)
        context = context_from_dict(
            {
                "generated_at": now.isoformat(),
                "valid_until": (now + timedelta(hours=2)).isoformat(),
                "regime_hint": "risk_off",
                "risk_level": "high",
                "headline_score": -0.5,
            }
        )
        allowed, reason = context.policy_for(_entry(), now)
        self.assertFalse(allowed)
        self.assertIn("leveraged", reason)

    def test_stale_context_blocks_even_unleveraged_entry(self) -> None:
        now = datetime.now(tz=UTC)
        context = context_from_dict(
            {
                "generated_at": (now - timedelta(days=2)).isoformat(),
                "valid_until": (now - timedelta(days=1)).isoformat(),
                "regime_hint": "mixed",
                "risk_level": "normal",
                "headline_score": 0,
            }
        )
        allowed, reason = context.policy_for(_entry("SPY", 1.0), now)
        self.assertFalse(allowed)
        self.assertIn("stale", reason)


if __name__ == "__main__":
    unittest.main()

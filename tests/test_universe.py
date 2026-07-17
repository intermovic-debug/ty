from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from soxswing.universe import load_symbols, load_universe_entries


class UniverseTests(unittest.TestCase):
    def test_legacy_string_symbols_still_load(self) -> None:
        entries = load_universe_entries({"symbols": ["soxl", "SPY"]})
        self.assertEqual([entry.symbol for entry in entries], ["SOXL", "SPY"])
        self.assertEqual(entries[0].leverage, 1.0)

    def test_metadata_and_active_groups_load(self) -> None:
        payload = {
            "groups": [
                {
                    "name": "Core",
                    "symbols": [
                        {
                            "symbol": "QQQ",
                            "leverage": 1,
                            "correlation_group": "us_equity",
                            "allowed_regimes": ["risk_on", "mixed"],
                            "max_position_pct": 0.08,
                        }
                    ],
                },
                {"name": "Disabled", "symbols": ["SOXL"]},
            ]
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "universe.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            strategy = {"universe_path": path.name, "active_groups": ["Core"]}
            entries = load_universe_entries(strategy, Path(temporary))
            self.assertEqual(load_symbols(strategy, Path(temporary)), ["QQQ"])
            self.assertEqual(entries[0].correlation_group, "us_equity")
            self.assertEqual(entries[0].allowed_regimes, ("risk_on", "mixed"))
            self.assertEqual(entries[0].max_position_pct, 0.08)


if __name__ == "__main__":
    unittest.main()

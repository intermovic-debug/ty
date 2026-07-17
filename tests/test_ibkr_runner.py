from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from soxswing.ibkr_runner import _load_config


class IbkrRunnerCompatibilityTests(unittest.TestCase):
    def test_config_loader_accepts_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text('{"safety": {"dry_run": true}}', encoding="utf-8-sig")
            self.assertTrue(_load_config(path)["safety"]["dry_run"])


if __name__ == "__main__":
    unittest.main()

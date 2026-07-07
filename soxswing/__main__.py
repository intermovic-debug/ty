from __future__ import annotations

import argparse
from pathlib import Path

from .bot import run_bot
from .config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="SOXL/SOXS swing bot")
    parser.add_argument(
        "--config",
        default="config.example.json",
        help="Path to a JSON config file.",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    config = load_config(config_path)
    result = run_bot(config)

    print(result.summary)


if __name__ == "__main__":
    main()

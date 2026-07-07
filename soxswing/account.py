from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def load_account_snapshot(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as file:
        snapshot = json.load(file)

    cash = snapshot.get("cash_usd", snapshot.get("estimated_cash_usd"))
    if cash is not None:
        snapshot["cash_usd"] = float(cash)
    snapshot["captured_at_utc"] = _parse_timestamp(snapshot.get("captured_at"))
    return snapshot


def resolve_account(
    configured_account: dict[str, Any],
    runtime: dict[str, Any],
    base_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    account = dict(configured_account)
    snapshot_path_value = runtime.get("account_snapshot_path")
    status: dict[str, Any] = {
        "ok": True,
        "used": False,
        "reason": "No account snapshot configured.",
        "path": None,
    }
    if not snapshot_path_value:
        return account, status

    snapshot_path = Path(str(snapshot_path_value))
    if not snapshot_path.is_absolute():
        snapshot_path = base_path / snapshot_path
    status["path"] = str(snapshot_path)
    snapshot = load_account_snapshot(snapshot_path)
    if snapshot is None:
        status.update({"ok": False, "reason": f"Account snapshot not found: {snapshot_path}"})
        return account, status

    captured_at = snapshot.get("captured_at_utc")
    if captured_at is None:
        status.update({"ok": False, "reason": "Account snapshot has no valid captured_at timestamp."})
        return account, status

    age_minutes = (datetime.now(tz=UTC) - captured_at).total_seconds() / 60
    max_age = float(runtime.get("account_snapshot_max_age_minutes", 720))
    if age_minutes > max_age:
        status.update(
            {
                "ok": False,
                "reason": f"Account snapshot is stale: {age_minutes:.0f} minutes old.",
                "captured_at": snapshot.get("captured_at"),
                "age_minutes": round(age_minutes, 1),
            }
        )
        return account, status

    cash = snapshot.get("cash_usd")
    if cash is None:
        status.update({"ok": False, "reason": "Account snapshot has no cash_usd value."})
        return account, status

    account["starting_cash"] = float(cash)
    status.update(
        {
            "ok": True,
            "used": True,
            "reason": f"Using account snapshot cash: {float(cash):.2f} USD.",
            "cash_usd": float(cash),
            "captured_at": snapshot.get("captured_at"),
            "holdings": snapshot.get("holdings", []),
        }
    )
    return account, status


def write_account_snapshot(
    path: Path,
    cash_usd: float,
    cash_krw: float | None = None,
    fx_rate: float | None = None,
    holdings: list[dict[str, Any]] | None = None,
    source: str = "manual_6110",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot: dict[str, Any] = {
        "source": source,
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "cash_usd": round(float(cash_usd), 2),
        "holdings": holdings or [],
    }
    if cash_krw is not None:
        snapshot["cash_krw"] = round(float(cash_krw))
    if fx_rate is not None:
        snapshot["fx_rate_krw_per_usd"] = float(fx_rate)
    path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Update the local account snapshot used by reports")
    parser.add_argument("--path", default="account_snapshot.json")
    parser.add_argument("--cash-usd", required=True, type=float)
    parser.add_argument("--cash-krw", type=float)
    parser.add_argument("--fx-rate", type=float)
    parser.add_argument("--holdings-json", default="[]")
    parser.add_argument("--source", default="manual_6110")
    args = parser.parse_args()

    holdings = json.loads(args.holdings_json)
    if not isinstance(holdings, list):
        raise ValueError("--holdings-json must decode to a list")
    write_account_snapshot(
        Path(args.path),
        cash_usd=args.cash_usd,
        cash_krw=args.cash_krw,
        fx_rate=args.fx_rate,
        holdings=holdings,
        source=args.source,
    )
    print(f"Updated {args.path}")


if __name__ == "__main__":
    main()

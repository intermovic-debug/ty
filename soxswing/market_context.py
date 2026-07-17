from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from .universe import UniverseEntry


class MarketContextError(ValueError):
    pass


RISK_MULTIPLIERS = {
    "normal": 1.0,
    "caution": 0.65,
    "high": 0.30,
    "extreme": 0.0,
}

NEGATIVE_PHRASES = (
    "airstrike",
    "bank failure",
    "cuts guidance",
    "default",
    "earnings miss",
    "export ban",
    "inflation accelerates",
    "invasion",
    "missile",
    "rate hike",
    "rout",
    "sanctions expanded",
    "selloff",
    "tariff increase",
    "war expands",
)

POSITIVE_PHRASES = (
    "beats estimates",
    "ceasefire",
    "inflation cools",
    "raises guidance",
    "rate cut",
    "strong demand",
    "stimulus",
)


def _parse_datetime(value: Any, field_name: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MarketContextError(f"Invalid {field_name}: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class MarketContext:
    generated_at: datetime
    valid_until: datetime
    regime_hint: str
    risk_level: str
    headline_score: float
    blocked_symbols: frozenset[str]
    blocked_groups: frozenset[str]
    preferred_groups: frozenset[str]
    catalysts: tuple[str, ...]
    sources: tuple[dict[str, str], ...]

    @property
    def risk_multiplier(self) -> float:
        return RISK_MULTIPLIERS[self.risk_level]

    def is_fresh(self, now: datetime | None = None) -> bool:
        current = (now or datetime.now(tz=UTC)).astimezone(UTC)
        return self.generated_at <= current <= self.valid_until

    def policy_for(self, entry: UniverseEntry, now: datetime | None = None) -> tuple[bool, str]:
        if not self.is_fresh(now):
            return False, "daily market context is stale"
        if self.risk_level == "extreme":
            return False, "extreme market risk blocks all new entries"
        if entry.symbol in self.blocked_symbols:
            return False, f"{entry.symbol} is blocked by daily context"
        if entry.group.lower() in self.blocked_groups:
            return False, f"group {entry.group} is blocked by daily context"
        if self.regime_hint == "risk_off" and entry.direction == "long" and entry.leverage > 1:
            return False, "risk-off context blocks leveraged long entries"
        return True, "context allows technical evaluation"

    def preference_bonus(self, entry: UniverseEntry) -> int:
        if not self.preferred_groups:
            return 0
        return 1 if entry.group.lower() in self.preferred_groups else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "valid_until": self.valid_until.isoformat(),
            "regime_hint": self.regime_hint,
            "risk_level": self.risk_level,
            "headline_score": self.headline_score,
            "blocked_symbols": sorted(self.blocked_symbols),
            "blocked_groups": sorted(self.blocked_groups),
            "preferred_groups": sorted(self.preferred_groups),
            "catalysts": list(self.catalysts),
            "sources": list(self.sources),
        }


def context_from_dict(raw: dict[str, Any]) -> MarketContext:
    risk_level = str(raw.get("risk_level", "high")).strip().lower()
    if risk_level not in RISK_MULTIPLIERS:
        raise MarketContextError(f"Unsupported risk_level: {risk_level}")
    regime_hint = str(raw.get("regime_hint", "mixed")).strip().lower()
    if regime_hint not in {"risk_on", "mixed", "risk_off"}:
        raise MarketContextError(f"Unsupported regime_hint: {regime_hint}")
    headline_score = float(raw.get("headline_score", 0.0))
    if not -1.0 <= headline_score <= 1.0:
        raise MarketContextError("headline_score must be between -1 and 1")

    return MarketContext(
        generated_at=_parse_datetime(raw.get("generated_at"), "generated_at"),
        valid_until=_parse_datetime(raw.get("valid_until"), "valid_until"),
        regime_hint=regime_hint,
        risk_level=risk_level,
        headline_score=headline_score,
        blocked_symbols=frozenset(str(item).strip().upper() for item in raw.get("blocked_symbols", [])),
        blocked_groups=frozenset(str(item).strip().lower() for item in raw.get("blocked_groups", [])),
        preferred_groups=frozenset(str(item).strip().lower() for item in raw.get("preferred_groups", [])),
        catalysts=tuple(str(item).strip() for item in raw.get("catalysts", []) if str(item).strip()),
        sources=tuple(
            {
                "name": str(item.get("name", "")).strip(),
                "url": str(item.get("url", "")).strip(),
            }
            for item in raw.get("sources", [])
            if isinstance(item, dict)
        ),
    )


def load_market_context(path: Path, now: datetime | None = None, require_fresh: bool = True) -> MarketContext:
    if not path.exists():
        raise MarketContextError(f"Market context file does not exist: {path}")
    with path.open("r", encoding="utf-8-sig") as file:
        context = context_from_dict(json.load(file))
    if require_fresh and not context.is_fresh(now):
        raise MarketContextError(
            f"Market context is stale: generated={context.generated_at.isoformat()} "
            f"valid_until={context.valid_until.isoformat()}"
        )
    return context


def build_context_from_headlines(
    headlines: Iterable[str],
    generated_at: datetime | None = None,
    valid_hours: float = 18.0,
    benchmark_change_pct: float = 0.0,
    sources: Iterable[dict[str, str]] = (),
    minimum_headlines: int = 3,
) -> MarketContext:
    now = (generated_at or datetime.now(tz=UTC)).astimezone(UTC)
    normalized = [str(headline).strip() for headline in headlines if str(headline).strip()]
    negative_hits = [
        headline
        for headline in normalized
        if any(phrase in headline.lower() for phrase in NEGATIVE_PHRASES)
    ]
    positive_hits = [
        headline
        for headline in normalized
        if any(phrase in headline.lower() for phrase in POSITIVE_PHRASES)
    ]
    denominator = max(3, len(negative_hits) + len(positive_hits))
    headline_score = max(-1.0, min(1.0, (len(positive_hits) - len(negative_hits)) / denominator))

    stress = min(0.0, float(benchmark_change_pct))
    if not normalized:
        risk_level = "extreme"
        regime_hint = "risk_off"
    elif len(normalized) < max(1, int(minimum_headlines)):
        risk_level = "high"
        regime_hint = "mixed"
    elif stress <= -3.0 or len(negative_hits) >= 4:
        risk_level = "extreme"
        regime_hint = "risk_off"
    elif stress <= -1.5 or len(negative_hits) >= 2:
        risk_level = "high"
        regime_hint = "risk_off"
    elif stress <= -0.5 or len(negative_hits) > len(positive_hits):
        risk_level = "caution"
        regime_hint = "mixed"
    elif positive_hits:
        risk_level = "normal"
        regime_hint = "risk_on" if len(positive_hits) > len(negative_hits) else "mixed"
    else:
        risk_level = "caution"
        regime_hint = "mixed"

    catalysts = negative_hits + positive_hits
    if not normalized:
        catalysts = ["No current headlines were supplied; new entries are blocked."]
    elif len(normalized) < max(1, int(minimum_headlines)):
        catalysts = ["Too few current headlines were supplied; risk is reduced."] + catalysts
    elif not catalysts:
        catalysts = ["No deterministic risk phrase was recognized; risk is reduced."]

    return MarketContext(
        generated_at=now,
        valid_until=now + timedelta(hours=float(valid_hours)),
        regime_hint=regime_hint,
        risk_level=risk_level,
        headline_score=round(headline_score, 4),
        blocked_symbols=frozenset(),
        blocked_groups=frozenset(),
        preferred_groups=frozenset(),
        catalysts=tuple(catalysts[:8]),
        sources=tuple(dict(source) for source in sources),
    )


def write_market_context(path: Path, context: MarketContext) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(context.to_dict(), file, indent=2, sort_keys=True)
        file.write("\n")

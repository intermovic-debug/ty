from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AccountConfig:
    base_currency: str
    starting_cash: float
    max_position_pct: float
    risk_per_trade_pct: float


@dataclass(frozen=True)
class StrategyConfig:
    symbols: list[str]
    fast_ma: int
    slow_ma: int
    rsi_period: int
    atr_period: int
    momentum_days: int
    stop_atr_multiple: float
    take_profit_atr_multiple: float
    min_signal_score: int


@dataclass(frozen=True)
class RuntimeConfig:
    broker: str
    mode: str
    state_path: Path
    trade_log_path: Path
    report_markdown_path: Path
    report_html_path: Path
    use_state_in_advisory: bool
    dry_run: bool


@dataclass(frozen=True)
class BotConfig:
    account: AccountConfig
    strategy: StrategyConfig
    runtime: RuntimeConfig


def load_config(path: Path) -> BotConfig:
    with path.open("r", encoding="utf-8") as file:
        raw = json.load(file)

    account = raw["account"]
    strategy = raw["strategy"]
    runtime = raw["runtime"]

    return BotConfig(
        account=AccountConfig(
            base_currency=account["base_currency"],
            starting_cash=float(account["starting_cash"]),
            max_position_pct=float(account["max_position_pct"]),
            risk_per_trade_pct=float(account["risk_per_trade_pct"]),
        ),
        strategy=StrategyConfig(
            symbols=list(strategy["symbols"]),
            fast_ma=int(strategy["fast_ma"]),
            slow_ma=int(strategy["slow_ma"]),
            rsi_period=int(strategy["rsi_period"]),
            atr_period=int(strategy["atr_period"]),
            momentum_days=int(strategy["momentum_days"]),
            stop_atr_multiple=float(strategy["stop_atr_multiple"]),
            take_profit_atr_multiple=float(strategy["take_profit_atr_multiple"]),
            min_signal_score=int(strategy["min_signal_score"]),
        ),
        runtime=RuntimeConfig(
            broker=runtime["broker"],
            mode=runtime.get("mode", "advisory"),
            state_path=Path(runtime["state_path"]),
            trade_log_path=Path(runtime["trade_log_path"]),
            report_markdown_path=Path(runtime.get("report_markdown_path", "reports/latest.md")),
            report_html_path=Path(runtime.get("report_html_path", "reports/latest.html")),
            use_state_in_advisory=bool(runtime.get("use_state_in_advisory", False)),
            dry_run=bool(runtime["dry_run"]),
        ),
    )

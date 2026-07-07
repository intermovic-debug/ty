from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .broker import PaperBroker, RealBrokerUnavailable
from .config import BotConfig
from .data import fetch_daily_bars
from .logging import append_trade_log
from .report import write_reports
from .risk import build_entry_order, build_exit_order
from .state import load_state, position_from_state, save_state
from .strategy import choose_signal


@dataclass(frozen=True)
class BotResult:
    summary: str


def run_bot(config: BotConfig) -> BotResult:
    raw_state = load_state(config.runtime.state_path, config.account)
    position = position_from_state(raw_state)
    cash = float(raw_state["cash"])
    if config.runtime.mode == "advisory" and not config.runtime.use_state_in_advisory:
        position = None
        cash = config.account.starting_cash

    bars = {symbol: fetch_daily_bars(symbol) for symbol in config.strategy.symbols}
    signal = choose_signal(bars, config.strategy, position)

    broker_cls = PaperBroker if config.runtime.broker == "paper" else RealBrokerUnavailable
    broker = broker_cls(cash, position, config.strategy)

    today = date.today().isoformat()
    last_entry_date = raw_state.get("last_entry_date")
    order = None
    status = "no_order"
    if config.runtime.mode == "advisory" and not config.runtime.use_state_in_advisory:
        last_entry_date = None

    if signal.action == "enter":
        if last_entry_date == today:
            status = "blocked_one_entry_per_day"
        else:
            order = build_entry_order(signal, cash, config.account, config.strategy)
            if order:
                if config.runtime.dry_run or config.runtime.mode == "advisory":
                    status = "planned_only"
                else:
                    receipt = broker.submit_order(order)
                    cash = float(receipt["cash"])
                    position = broker.position
                    last_entry_date = today
                    status = "paper_filled"
            else:
                status = "blocked_by_risk"
    elif signal.action == "exit" and position:
        order = build_exit_order(signal, position.quantity)
        if order:
            if config.runtime.dry_run or config.runtime.mode == "advisory":
                status = "planned_only"
            else:
                receipt = broker.submit_order(order)
                cash = float(receipt["cash"])
                position = broker.position
                status = "paper_filled"

    if not config.runtime.dry_run and config.runtime.mode != "advisory":
        save_state(config.runtime.state_path, cash, position, last_entry_date)
    append_trade_log(config.runtime.trade_log_path, signal, order, status)
    write_reports(
        config.runtime.report_markdown_path,
        config.runtime.report_html_path,
        signal,
        order,
        status,
        cash,
        position,
        bars,
    )

    position_text = (
        f"{position.symbol} {position.quantity} shares"
        if position
        else "flat"
    )
    return BotResult(
        summary=(
            f"Signal={signal.action} symbol={signal.symbol or '-'} score={signal.score} "
            f"status={status} cash={cash:.2f} position={position_text}\n"
            f"Reason: {signal.reason}\n"
            f"Report: {config.runtime.report_markdown_path}"
        )
    )

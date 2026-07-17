from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from soxswing.ibkr import IbkrBroker, IbkrError
from soxswing.market_context import MarketContextError, load_market_context
from soxswing.models import IntradayBar
from soxswing.pro_swing import (
    assess_regime,
    assess_shoulder_exit,
    rank_knee_candidates,
    size_position,
)
from soxswing.universe import UniverseEntry, load_universe_entries


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as file:
        raw = json.load(file)
    if not isinstance(raw, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return raw


def _resolve(path_value: str, base_path: Path) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else base_path / path


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _pick_number(values: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        value = _safe_float(values.get(key))
        if value is not None:
            return value
    return None


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = datetime.strptime(text, "%Y%m%d %H:%M:%S")
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _fetch_completed_bars(
    broker: IbkrBroker,
    symbol: str,
    market_data: dict[str, Any],
    now: datetime,
) -> list[IntradayBar]:
    try:
        from ib_insync import Stock
    except ImportError as exc:
        raise IbkrError("Install IBKR support with: python -m pip install -r requirements-ibkr.txt") from exc

    ib = broker.connect()
    ib.reqMarketDataType(int(market_data.get("ibkr_market_data_type", 1)))
    contract = Stock(symbol.upper(), "SMART", "USD")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise IbkrError(f"IBKR could not qualify {symbol}")
    rows = ib.reqHistoricalData(
        qualified[0],
        endDateTime="",
        durationStr=str(market_data.get("duration", "10 D")),
        barSizeSetting=str(market_data.get("bar_size", "15 mins")),
        whatToShow="TRADES",
        useRTH=bool(market_data.get("use_rth", True)),
        formatDate=2,
        keepUpToDate=False,
    )
    bar_minutes = int(market_data.get("bar_minutes", 15))
    interval = timedelta(minutes=bar_minutes)
    bars = [
        IntradayBar(
            timestamp=_coerce_datetime(row.date),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=int(float(getattr(row, "volume", 0) or 0)),
        )
        for row in rows
        if _coerce_datetime(row.date) + interval <= now
    ]
    bars.sort(key=lambda bar: bar.timestamp)
    if len(bars) < 55:
        raise IbkrError(f"Not enough completed bars for {symbol}: {len(bars)}")
    completed_at = bars[-1].timestamp + interval
    age_minutes = (now - completed_at).total_seconds() / 60.0
    max_age = float(market_data.get("max_completed_bar_age_minutes", 20))
    if age_minutes < -1 or age_minutes > max_age:
        raise IbkrError(
            f"Latest completed {symbol} bar is stale or mis-timestamped: "
            f"completed={completed_at.isoformat()}, age={age_minutes:.1f} minutes"
        )
    return bars


def _load_state(path: Path, strategy_id: str) -> dict[str, Any]:
    if not path.exists():
        return {"strategy_id": strategy_id, "positions": {}}
    state = _load_json(path)
    if state.get("strategy_id") != strategy_id:
        raise ValueError(
            f"State strategy_id={state.get('strategy_id')!r} does not match {strategy_id!r}"
        )
    state.setdefault("positions", {})
    return state


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(tz=UTC).isoformat(timespec="seconds")
    with path.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=2, sort_keys=True)
        file.write("\n")


def _record_entry(
    state_path: Path,
    strategy_id: str,
    entries: list[UniverseEntry],
    protected: set[str],
    symbol: str,
    quantity: int,
    entry_price: float,
    stop_price: float,
) -> None:
    symbol = symbol.upper()
    allowed = {entry.symbol for entry in entries}
    if symbol not in allowed or symbol in protected:
        raise ValueError(f"{symbol} is not an active, unprotected strategy symbol")
    if quantity <= 0 or stop_price <= 0 or entry_price <= stop_price:
        raise ValueError("Entry requires quantity > 0 and entry_price > stop_price > 0")
    state = _load_state(state_path, strategy_id)
    state["positions"][symbol] = {
        "quantity": int(quantity),
        "entry_price": float(entry_price),
        "initial_stop": float(stop_price),
        "peak_price": float(entry_price),
        "bars_held": 0,
        "last_bar_timestamp": None,
        "opened_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
    }
    _save_state(state_path, state)


def _record_exit(state_path: Path, strategy_id: str, symbol: str) -> None:
    state = _load_state(state_path, strategy_id)
    state["positions"].pop(symbol.upper(), None)
    _save_state(state_path, state)


def _broker_positions(broker: IbkrBroker) -> dict[str, dict[str, Any]]:
    positions: dict[str, dict[str, Any]] = {}
    for row in broker.positions():
        symbol = str(row.get("symbol", "")).upper()
        quantity = int(float(row.get("position", 0) or 0))
        if symbol and quantity != 0:
            positions[symbol] = {
                "quantity": quantity,
                "average_cost": float(row.get("average_cost", 0) or 0),
                "account": str(row.get("account", "")),
            }
    return positions


def _managed_accounts(broker: IbkrBroker) -> list[str]:
    return [str(account) for account in broker.connect().managedAccounts()]


def _account_values(summary: dict[str, Any], require_realized: bool) -> tuple[float, float, float]:
    equity = _pick_number(summary, ["NetLiquidation_USD", "NetLiquidation"])
    cash = _pick_number(
        summary,
        ["SettledCash_USD", "TotalCashValue_USD", "AvailableFunds_USD", "AvailableFunds"],
    )
    realized = _pick_number(summary, ["RealizedPnL_USD", "RealizedPnL"])
    if equity is None or cash is None:
        raise IbkrError("IBKR account summary did not provide USD equity and available cash")
    if realized is None and require_realized:
        raise IbkrError("IBKR account summary did not provide RealizedPnL; daily-loss guard cannot run")
    return equity, max(0.0, cash), realized or 0.0


def _gross_notional(
    positions: dict[str, dict[str, Any]],
    bars_by_symbol: dict[str, list[IntradayBar]],
    external_gross: float,
) -> float:
    gross = max(0.0, external_gross)
    for symbol, row in positions.items():
        price = (
            bars_by_symbol[symbol][-1].close
            if symbol in bars_by_symbol
            else float(row.get("average_cost", 0) or 0)
        )
        gross += abs(int(row["quantity"]) * price)
    return gross


def _markdown_report(report: dict[str, Any]) -> str:
    context = report.get("market_context", {})
    regime = report.get("technical_regime", {})
    lines = [
        "# IBKR Regime Paper Scan",
        "",
        f"- Updated UTC: {report['generated_at']}",
        f"- Status: `{report['status']}`",
        "- Mode: signal-only paper research; this program has no order-placement path.",
        f"- Context risk: `{context.get('risk_level', 'unavailable')}`",
        f"- Technical regime: `{regime.get('label', 'unavailable')}`",
        f"- Protected symbols: `{', '.join(report.get('protected_symbols', []))}`",
        "",
        "## Proposed Actions",
        "",
    ]
    exit_proposals = report.get("exit_proposals", [])
    entry_proposal = report.get("entry_proposal")
    if exit_proposals:
        for proposal in exit_proposals:
            lines.append(
                f"- PAPER EXIT REVIEW: {proposal['symbol']} {proposal['quantity']} shares; "
                f"reason={proposal['decision']['reason']}"
            )
    elif entry_proposal:
        lines.append(
            f"- PAPER ENTRY REVIEW: {entry_proposal['symbol']} {entry_proposal['quantity']} shares; "
            f"closed-bar price={entry_proposal['signal']['price']:.2f}, "
            f"initial stop={entry_proposal['signal']['stop_price']:.2f}"
        )
    else:
        lines.append("- No action. Failed or unavailable inputs always resolve to no trade.")

    lines.extend(["", "## Candidate Scan", "", "| Symbol | Eligible | Score | Price | RSI | Reason |", "|---|---:|---:|---:|---:|---|"])
    for row in report.get("candidates", []):
        signal = row["signal"]
        failed = [reason for reason in signal["reasons"] if reason.startswith("FAIL")]
        reason = "; ".join(failed) if failed else "all entry checks passed"
        lines.append(
            f"| {row['symbol']} | {signal['eligible']} | {signal['score']} | "
            f"{signal['price']:.2f} | {signal['rsi']:.1f} | {reason} |"
        )
    if report.get("blocked"):
        lines.extend(["", "## Blocked", ""])
        lines.extend(f"- {symbol}: {reason}" for symbol, reason in sorted(report["blocked"].items()))
    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in report["warnings"])
    lines.append("")
    return "\n".join(lines)


def _write_report(report: dict[str, Any], json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    with json_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, sort_keys=True)
        file.write("\n")
    markdown_path.write_text(_markdown_report(report), encoding="utf-8")


def _scan(config: dict[str, Any], config_path: Path) -> dict[str, Any]:
    base = config_path.resolve().parent
    strategy = config.get("strategy", {})
    market_data = config.get("market_data", {})
    account_config = config.get("account", {})
    entries = load_universe_entries(strategy, base)
    protected = {str(symbol).upper() for symbol in strategy.get("protected_symbols", [])}
    entries = [entry for entry in entries if entry.symbol not in protected]
    benchmarks = [str(symbol).upper() for symbol in strategy.get("benchmarks", ["SPY", "QQQ", "SOXX"])]
    context_path = _resolve(str(strategy.get("market_context_path", "data/market_context_latest.json")), base)
    context = load_market_context(
        context_path,
        require_fresh=bool(strategy.get("require_fresh_context", True)),
    )
    strategy_id = str(strategy.get("strategy_id", "regime-knee-shoulder-v1"))
    runtime = config.get("runtime", {})
    state_path = _resolve(str(runtime.get("state_path", "data/regime_paper_state.json")), base)
    state = _load_state(state_path, strategy_id)

    broker = IbkrBroker(config)
    try:
        accounts = _managed_accounts(broker)
        if not accounts or any(not account.upper().startswith("DU") for account in accounts):
            raise IbkrError(f"Paper-only scan expected DU accounts, got: {accounts or ['unknown']}")
        now = datetime.now(tz=UTC)
        symbols = sorted({entry.symbol for entry in entries} | set(benchmarks) | set(state["positions"]))
        bars_by_symbol: dict[str, list[IntradayBar]] = {}
        data_errors: dict[str, str] = {}
        for symbol in symbols:
            try:
                bars_by_symbol[symbol] = _fetch_completed_bars(broker, symbol, market_data, now)
            except Exception as exc:
                data_errors[symbol] = str(exc)

        missing_benchmarks = [symbol for symbol in benchmarks if symbol not in bars_by_symbol]
        if missing_benchmarks:
            raise IbkrError(f"Missing current benchmark bars: {missing_benchmarks}; errors={data_errors}")
        regime = assess_regime(bars_by_symbol, benchmarks, strategy)
        positions = _broker_positions(broker)
        summary = broker.account_summary()
        equity, cash, realized = _account_values(
            summary,
            bool(account_config.get("require_realized_pnl", True)),
        )
        gross = _gross_notional(
            positions,
            bars_by_symbol,
            float(account_config.get("external_gross_notional_usd", 0.0)),
        )

        ranked, blocked = rank_knee_candidates(entries, bars_by_symbol, regime, context, strategy)
        for symbol, error in data_errors.items():
            blocked.setdefault(symbol, error)
        if bool(strategy.get("block_existing_positions", True)):
            for entry in entries:
                if entry.symbol in positions and entry.symbol not in state["positions"]:
                    blocked[entry.symbol] = "existing broker position is not tagged to this strategy"
        for symbol in state["positions"]:
            blocked[symbol] = "symbol already has a strategy-managed paper position"
        occupied_groups = {
            entry.correlation_group
            for entry in entries
            if entry.symbol in positions or entry.symbol in state["positions"]
        }
        candidates: list[dict[str, Any]] = []
        for entry, signal in ranked:
            if entry.symbol in blocked:
                continue
            if entry.correlation_group in occupied_groups and entry.symbol not in state["positions"]:
                blocked[entry.symbol] = f"correlation group {entry.correlation_group} is already occupied"
                continue
            candidates.append({"symbol": entry.symbol, "group": entry.group, "signal": asdict(signal)})

        exit_proposals: list[dict[str, Any]] = []
        warnings: list[str] = []
        for symbol, managed in state["positions"].items():
            broker_position = positions.get(symbol)
            if not broker_position or int(broker_position["quantity"]) < int(managed["quantity"]):
                warnings.append(f"Managed state for {symbol} does not match the IBKR paper position; exit signal blocked")
                continue
            bars = bars_by_symbol.get(symbol)
            if not bars:
                warnings.append(f"No current completed bars for managed position {symbol}")
                continue
            last_seen = managed.get("last_bar_timestamp")
            new_bars = (
                [bars[-1]]
                if last_seen is None
                else [bar for bar in bars if bar.timestamp > _coerce_datetime(last_seen)]
            )
            managed["bars_held"] = int(managed.get("bars_held", 0)) + len(new_bars)
            managed["last_bar_timestamp"] = bars[-1].timestamp.isoformat()
            decision = assess_shoulder_exit(
                bars,
                float(managed["entry_price"]),
                float(managed["initial_stop"]),
                float(managed.get("peak_price", managed["entry_price"])),
                int(managed["bars_held"]),
                strategy,
            )
            managed["peak_price"] = decision.peak_price
            if decision.should_exit:
                exit_proposals.append(
                    {
                        "symbol": symbol,
                        "quantity": int(managed["quantity"]),
                        "decision": asdict(decision),
                        "mode": "paper_review_only",
                    }
                )
        _save_state(state_path, state)

        entry_proposal: dict[str, Any] | None = None
        if not exit_proposals and len(state["positions"]) < int(account_config.get("max_positions", 2)):
            entry_by_symbol = {entry.symbol: entry for entry in entries}
            for row in candidates:
                signal_dict = row["signal"]
                if not signal_dict["eligible"]:
                    continue
                entry = entry_by_symbol[row["symbol"]]
                signal = next(signal for candidate_entry, signal in ranked if candidate_entry.symbol == entry.symbol)
                sizing = size_position(equity, cash, gross, realized, entry, signal, account_config, context)
                if sizing.allowed:
                    entry_proposal = {
                        "symbol": entry.symbol,
                        "quantity": sizing.quantity,
                        "signal": signal_dict,
                        "sizing": asdict(sizing),
                        "mode": "paper_review_only",
                    }
                    break
                blocked[entry.symbol] = sizing.reason

        return {
            "generated_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
            "status": "ok",
            "mode": "paper_signal_only",
            "order_placement_available": False,
            "accounts": accounts,
            "market_context": context.to_dict(),
            "technical_regime": asdict(regime),
            "account_guard": {
                "equity_usd": round(equity, 2),
                "available_cash_usd": round(cash, 2),
                "daily_realized_pnl_usd": round(realized, 2),
                "gross_notional_usd": round(gross, 2),
            },
            "protected_symbols": sorted(protected),
            "unmanaged_broker_positions": {
                symbol: row for symbol, row in positions.items() if symbol not in state["positions"]
            },
            "managed_positions": state["positions"],
            "candidates": candidates,
            "blocked": blocked,
            "entry_proposal": entry_proposal,
            "exit_proposals": exit_proposals,
            "warnings": warnings,
        }
    finally:
        broker.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="IBKR paper-only regime/knee/shoulder scanner")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.ibkr.regime_paper.example.json"),
    )
    parser.add_argument("command", nargs="?", choices=("scan", "record-entry", "record-exit"), default="scan")
    parser.add_argument("--symbol")
    parser.add_argument("--quantity", type=int)
    parser.add_argument("--entry-price", type=float)
    parser.add_argument("--stop-price", type=float)
    args = parser.parse_args()

    config = _load_json(args.config)
    base = args.config.resolve().parent
    strategy = config.get("strategy", {})
    runtime = config.get("runtime", {})
    strategy_id = str(strategy.get("strategy_id", "regime-knee-shoulder-v1"))
    state_path = _resolve(str(runtime.get("state_path", "data/regime_paper_state.json")), base)
    entries = load_universe_entries(strategy, base)
    protected = {str(symbol).upper() for symbol in strategy.get("protected_symbols", [])}

    if args.command == "record-entry":
        if not all(value is not None for value in (args.symbol, args.quantity, args.entry_price, args.stop_price)):
            parser.error("record-entry requires --symbol, --quantity, --entry-price, and --stop-price")
        _record_entry(
            state_path,
            strategy_id,
            entries,
            protected,
            str(args.symbol),
            int(args.quantity),
            float(args.entry_price),
            float(args.stop_price),
        )
        print(f"Recorded paper strategy entry for {str(args.symbol).upper()} in {state_path}")
        return 0
    if args.command == "record-exit":
        if not args.symbol:
            parser.error("record-exit requires --symbol")
        _record_exit(state_path, strategy_id, args.symbol)
        print(f"Removed paper strategy position for {args.symbol.upper()} from {state_path}")
        return 0

    json_path = _resolve(
        str(runtime.get("report_json_path", "reports/ibkr_regime_paper_latest.json")), base
    )
    markdown_path = _resolve(
        str(runtime.get("report_markdown_path", "reports/ibkr_regime_paper_latest.md")), base
    )
    try:
        report = _scan(config, args.config)
        exit_code = 0
    except (IbkrError, MarketContextError, ValueError, OSError) as exc:
        report = {
            "generated_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
            "status": "blocked",
            "mode": "paper_signal_only",
            "order_placement_available": False,
            "protected_symbols": sorted(protected),
            "entry_proposal": None,
            "exit_proposals": [],
            "candidates": [],
            "blocked": {"scan": str(exc)},
            "warnings": ["Fail-closed: no trade signal was produced."],
        }
        exit_code = 2
    _write_report(report, json_path, markdown_path)
    print(json.dumps(report, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

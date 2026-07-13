from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


LOCAL_ROOT = Path(__file__).resolve().parent
FALLBACK_REPO_ROOT = Path(r"C:\Users\Administrator\Documents\Codex\ty")

if (LOCAL_ROOT / "soxswing").exists():
    REPO_ROOT = LOCAL_ROOT
elif FALLBACK_REPO_ROOT.exists():
    REPO_ROOT = FALLBACK_REPO_ROOT
else:
    REPO_ROOT = LOCAL_ROOT

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from soxswing.ibkr import IbkrBroker, IbkrError
    from soxswing.intraday import _passes_entry_filter, _rank_scores, _symbol_score
    from soxswing.models import IntradayBar
except Exception as exc:  # pragma: no cover - gives a cleaner setup error for CLI users.
    raise SystemExit(
        "Could not import the soxswing package. Run this script from the repo root, "
        "or install/update the GitHub repo first."
    ) from exc


@dataclass(frozen=True)
class SignalDecision:
    action: str
    status: str
    symbol: str = ""
    quantity: int = 0
    signal_price: float = 0.0
    limit_price: float = 0.0
    reason: str = ""
    score: int | None = None
    rsi: float | None = None
    momentum: float | None = None
    ib_status: str = ""
    order_id: int | None = None
    perm_id: int | None = None


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def _resolve_path(path_value: str | None, config_path: Path) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    if path.is_absolute():
        return path
    candidates = [
        config_path.parent / path,
        LOCAL_ROOT / path,
        REPO_ROOT / path,
        LOCAL_ROOT / "ibkr_runtime" / path.name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return config_path.parent / path


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    text = str(value).strip()
    for fmt in ("%Y%m%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text[: len(fmt)], fmt)
            return parsed.replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"Cannot parse IBKR bar timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _fetch_ibkr_bars(
    broker: IbkrBroker,
    symbol: str,
    duration: str,
    bar_size: str,
    use_rth: bool,
) -> list[IntradayBar]:
    ib = broker.connect()
    try:
        from ib_insync import Stock
    except ImportError as exc:
        raise IbkrError(
            "ib-insync is not installed. Run: python -m pip install -r requirements-ibkr.txt"
        ) from exc

    contract = Stock(symbol.upper(), "SMART", "USD")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise IbkrError(f"IBKR could not qualify stock contract for {symbol}.")

    bars = ib.reqHistoricalData(
        qualified[0],
        endDateTime="",
        durationStr=duration,
        barSizeSetting=bar_size,
        whatToShow="TRADES",
        useRTH=use_rth,
        formatDate=2,
        keepUpToDate=False,
    )
    if not bars:
        raise IbkrError(f"No historical bars returned for {symbol}.")

    rows: list[IntradayBar] = []
    for bar in bars:
        rows.append(
            IntradayBar(
                timestamp=_coerce_datetime(bar.date),
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
                volume=int(getattr(bar, "volume", 0) or 0),
            )
        )
    if len(rows) < 40:
        raise IbkrError(f"Not enough IBKR bars for {symbol}: {len(rows)}")
    return rows


def _fetch_scores(
    broker: IbkrBroker,
    symbols: list[str],
    strategy: dict[str, Any],
    market_data: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    scores: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    duration = str(market_data.get("duration", "5 D"))
    bar_size = str(market_data.get("bar_size", "15 mins"))
    use_rth = bool(market_data.get("use_rth", True))
    for symbol in symbols:
        try:
            bars = _fetch_ibkr_bars(broker, symbol, duration, bar_size, use_rth)
            scores[symbol] = _symbol_score(bars, strategy)
        except Exception as exc:
            errors[symbol] = str(exc)
    return scores, errors


def _load_state(state_path: Path) -> dict[str, Any]:
    today = datetime.now(tz=UTC).date().isoformat()
    if not state_path.exists():
        return {"trade_day": today, "orders_today": 0, "peaks": {}, "last_order_at": None}
    state = _load_json(state_path)
    if state.get("trade_day") != today:
        state = {"trade_day": today, "orders_today": 0, "peaks": {}, "last_order_at": None}
    state.setdefault("peaks", {})
    state.setdefault("orders_today", 0)
    return state


def _save_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(tz=UTC).isoformat(timespec="seconds")
    with state_path.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=2, sort_keys=True)


def _current_positions(broker: IbkrBroker, symbols: list[str]) -> dict[str, dict[str, Any]]:
    wanted = {symbol.upper() for symbol in symbols}
    positions: dict[str, dict[str, Any]] = {}
    for row in broker.positions():
        symbol = str(row.get("symbol", "")).upper()
        qty = int(float(row.get("position", 0) or 0))
        if symbol in wanted and qty > 0:
            positions[symbol] = {
                "quantity": qty,
                "average_cost": float(row.get("average_cost", 0.0) or 0.0),
                "account": str(row.get("account", "")),
            }
    return positions


def _managed_accounts(broker: IbkrBroker) -> list[str]:
    ib = broker.connect()
    try:
        return [str(account) for account in ib.managedAccounts()]
    except Exception:
        return []


def _looks_like_paper_account(accounts: list[str]) -> bool:
    return any(account.upper().startswith("DU") for account in accounts)


def _quote_for_order(broker: IbkrBroker, symbol: str, fallback_price: float) -> dict[str, Any]:
    try:
        quote = broker.quote(symbol, wait_seconds=2.0)
    except Exception as exc:
        return {
            "symbol": symbol,
            "price": fallback_price,
            "price_field": "historical_close_fallback",
            "bid": None,
            "ask": None,
            "delayed": True,
            "error": str(exc),
        }
    return quote


def _limit_price(
    action: str,
    quote: dict[str, Any],
    fallback_price: float,
    buy_buffer_pct: float,
    sell_buffer_pct: float,
) -> float:
    action = action.upper()
    if action == "BUY":
        reference = _safe_float(quote.get("ask")) or _safe_float(quote.get("price")) or fallback_price
        return round(reference * (1 + buy_buffer_pct), 2)
    reference = _safe_float(quote.get("bid")) or _safe_float(quote.get("price")) or fallback_price
    return round(reference * (1 - sell_buffer_pct), 2)


def _quantity_for_price(price: float, fixed_qty: int, max_notional: float) -> int:
    if price <= 0:
        return 0
    by_notional = int(max_notional // price) if max_notional > 0 else fixed_qty
    return max(0, min(int(fixed_qty), by_notional))


def _append_csv(path: Path, fields: list[str], row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in fields})


def _log_heartbeat(path: Path, scores: dict[str, dict[str, Any]], errors: dict[str, str]) -> None:
    fields = [
        "timestamp",
        "symbol",
        "bar_timestamp",
        "price",
        "score",
        "rsi",
        "momentum",
        "fast_ema",
        "slow_ema",
        "atr",
        "error",
    ]
    now = datetime.now(tz=UTC).isoformat(timespec="seconds")
    for symbol, score in scores.items():
        _append_csv(
            path,
            fields,
            {
                "timestamp": now,
                "symbol": symbol,
                "bar_timestamp": score["timestamp"].isoformat(),
                "price": round(float(score["price"]), 4),
                "score": int(score["score"]),
                "rsi": round(float(score["rsi"]), 2),
                "momentum": round(float(score["momentum"]), 6),
                "fast_ema": round(float(score["fast_ema"]), 4),
                "slow_ema": round(float(score["slow_ema"]), 4),
                "atr": round(float(score["atr"]), 4),
                "error": "",
            },
        )
    for symbol, error in errors.items():
        _append_csv(path, fields, {"timestamp": now, "symbol": symbol, "error": error})


def _log_signal(path: Path, decision: SignalDecision) -> None:
    fields = [
        "timestamp",
        "action",
        "status",
        "symbol",
        "quantity",
        "signal_price",
        "limit_price",
        "score",
        "rsi",
        "momentum",
        "reason",
        "ib_status",
        "order_id",
        "perm_id",
    ]
    row = asdict(decision)
    row["timestamp"] = datetime.now(tz=UTC).isoformat(timespec="seconds")
    _append_csv(path, fields, row)


def _write_summary(
    path: Path,
    config_path: Path,
    create_orders: bool,
    accounts: list[str],
    decision: SignalDecision,
    scores: dict[str, dict[str, Any]],
    positions: dict[str, dict[str, Any]],
    errors: dict[str, str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ranked = _rank_scores(scores) if scores else []
    lines = [
        "# IBKR Paper Forward Test",
        "",
        f"- Updated at UTC: {datetime.now(tz=UTC).isoformat(timespec='seconds')}",
        f"- Config: `{config_path}`",
        f"- Order creation enabled: `{create_orders}`",
        f"- Managed accounts: `{', '.join(accounts) if accounts else 'unknown'}`",
        f"- Latest decision: `{decision.action}` / `{decision.status}`",
        f"- Reason: {decision.reason}",
        "",
        "## Ranked Scores",
        "",
        "| Rank | Symbol | Price | Score | RSI | Momentum | Bar Time |",
        "|---:|---|---:|---:|---:|---:|---|",
    ]
    for index, (symbol, score) in enumerate(ranked, start=1):
        lines.append(
            "| {rank} | {symbol} | {price:.4f} | {score} | {rsi:.2f} | {momentum:.2%} | {bar_time} |".format(
                rank=index,
                symbol=symbol,
                price=float(score["price"]),
                score=int(score["score"]),
                rsi=float(score["rsi"]),
                momentum=float(score["momentum"]),
                bar_time=score["timestamp"].isoformat(),
            )
        )
    lines.extend(["", "## Positions", ""])
    if positions:
        lines.extend(
            f"- {symbol}: quantity={row['quantity']}, avg_cost={row['average_cost']:.4f}, account={row['account']}"
            for symbol, row in sorted(positions.items())
        )
    else:
        lines.append("- No SOXL/SOXS paper positions detected.")
    if errors:
        lines.extend(["", "## Data Errors", ""])
        lines.extend(f"- {symbol}: {error}" for symbol, error in sorted(errors.items()))
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _place_order_if_allowed(
    broker: IbkrBroker,
    decision: SignalDecision,
    create_orders: bool,
    allow_delayed_quotes: bool,
    quote: dict[str, Any],
) -> SignalDecision:
    if decision.action not in {"BUY", "SELL"}:
        return decision
    if not create_orders:
        return SignalDecision(**{**asdict(decision), "status": "signal_only"})
    if bool(quote.get("delayed")) and not allow_delayed_quotes:
        return SignalDecision(
            **{
                **asdict(decision),
                "status": "blocked_delayed_quote",
                "reason": decision.reason + " Delayed quote blocked order creation.",
            }
        )
    try:
        result = broker.place_limit_order(
            decision.symbol,
            decision.action,
            decision.quantity,
            decision.limit_price,
            create_order_requested=True,
        )
    except Exception as exc:
        return SignalDecision(
            **{
                **asdict(decision),
                "status": "order_error",
                "reason": f"{decision.reason} Order error: {exc}",
            }
        )
    return SignalDecision(
        **{
            **asdict(decision),
            "status": "order_submitted",
            "ib_status": str(result.get("status", "")),
            "order_id": result.get("order_id"),
            "perm_id": result.get("perm_id"),
        }
    )


def _decide_once(
    broker: IbkrBroker,
    config: dict[str, Any],
    strategy_config: dict[str, Any],
    config_path: Path,
    create_orders: bool,
    fixed_qty: int,
    max_notional: float,
    allow_delayed_quotes: bool,
) -> SignalDecision:
    forward = config.get("forward_test", {})
    market_data = config.get("market_data", {})
    strategy = strategy_config["strategy"]
    symbols = [str(symbol).upper() for symbol in forward.get("symbols", strategy.get("symbols", ["SOXL", "SOXS"]))]
    runtime_dir = _resolve_path(str(forward.get("runtime_dir", "ibkr_runtime/paper_forward")), config_path)
    if runtime_dir is None:
        runtime_dir = LOCAL_ROOT / "ibkr_runtime" / "paper_forward"
    today_dir = runtime_dir / datetime.now(tz=UTC).strftime("%Y%m%d")
    state_path = today_dir / "state.json"
    heartbeat_path = today_dir / "heartbeat.csv"
    signals_path = today_dir / "signals.csv"
    summary_path = today_dir / "summary.md"

    accounts = _managed_accounts(broker)
    require_du = bool(forward.get("require_du_account_for_orders", True))
    if create_orders and require_du and not _looks_like_paper_account(accounts):
        raise IbkrError(
            "Paper order creation is blocked because managedAccounts() did not show a DU* paper account. "
            f"Accounts seen: {accounts or ['unknown']}"
        )

    scores, errors = _fetch_scores(broker, symbols, strategy, market_data)
    _log_heartbeat(heartbeat_path, scores, errors)
    if not scores:
        decision = SignalDecision(action="WATCH", status="no_data", reason=f"No usable scores. Errors: {errors}")
        _log_signal(signals_path, decision)
        _write_summary(summary_path, config_path, create_orders, accounts, decision, scores, {}, errors)
        return decision

    state = _load_state(state_path)
    positions = _current_positions(broker, symbols)
    max_orders = int(forward.get("max_orders_per_day", strategy.get("max_trades_per_day", 1)))
    buy_buffer = float(forward.get("buy_limit_buffer_pct", 0.001))
    sell_buffer = float(forward.get("sell_limit_buffer_pct", 0.001))

    decision = SignalDecision(action="WATCH", status="no_edge", reason="No entry or exit condition.")

    if positions:
        for symbol, row in positions.items():
            if symbol not in scores:
                continue
            price = float(scores[symbol]["price"])
            entry = float(row["average_cost"])
            peaks = state.setdefault("peaks", {})
            previous_peak = float(peaks.get(symbol, entry))
            peak = max(previous_peak, price)
            peaks[symbol] = round(peak, 4)
            stop = entry * (1 - float(strategy["stop_loss_pct"]))
            take_profit = entry * (1 + float(strategy["take_profit_pct"]))
            trailing = peak * (1 - float(strategy["trailing_stop_pct"]))
            exit_reason = ""
            if price <= stop:
                exit_reason = f"stop loss: price {price:.4f} <= {stop:.4f}"
            elif price <= trailing:
                exit_reason = f"trailing stop: price {price:.4f} <= {trailing:.4f}"
            elif price >= take_profit:
                exit_reason = f"take profit: price {price:.4f} >= {take_profit:.4f}"

            if exit_reason:
                qty = min(int(row["quantity"]), fixed_qty)
                quote = _quote_for_order(broker, symbol, price)
                limit = _limit_price("SELL", quote, price, buy_buffer, sell_buffer)
                decision = SignalDecision(
                    action="SELL",
                    status="candidate",
                    symbol=symbol,
                    quantity=qty,
                    signal_price=round(price, 4),
                    limit_price=limit,
                    reason=exit_reason,
                    score=int(scores[symbol]["score"]),
                    rsi=float(scores[symbol]["rsi"]),
                    momentum=float(scores[symbol]["momentum"]),
                )
                decision = _place_order_if_allowed(broker, decision, create_orders, allow_delayed_quotes, quote)
                state["orders_today"] = int(state.get("orders_today", 0)) + int(create_orders)
                state["last_order_at"] = datetime.now(tz=UTC).isoformat(timespec="seconds")
                break
        else:
            held = ", ".join(f"{symbol} x{row['quantity']}" for symbol, row in positions.items())
            decision = SignalDecision(action="HOLD", status="holding", reason=f"Holding paper position: {held}")
    elif int(state.get("orders_today", 0)) >= max_orders:
        decision = SignalDecision(action="WATCH", status="max_orders_reached", reason="Max paper orders reached.")
    else:
        ranked = _rank_scores(scores)
        best_symbol, best = ranked[0]
        second_symbol, second = ranked[1] if len(ranked) > 1 else ("-", {"score": -99})
        if _passes_entry_filter(best, second, strategy):
            price = float(best["price"])
            qty = _quantity_for_price(price, fixed_qty, max_notional)
            if qty <= 0:
                decision = SignalDecision(action="WATCH", status="size_blocked", reason="Quantity is 0 under max_notional.")
            else:
                quote = _quote_for_order(broker, best_symbol, price)
                limit = _limit_price("BUY", quote, price, buy_buffer, sell_buffer)
                reason = (
                    f"{best_symbol} passed entry filter. "
                    f"Next={second_symbol} score={second['score']}; reasons={'; '.join(best['reasons'])}"
                )
                decision = SignalDecision(
                    action="BUY",
                    status="candidate",
                    symbol=best_symbol,
                    quantity=qty,
                    signal_price=round(price, 4),
                    limit_price=limit,
                    reason=reason,
                    score=int(best["score"]),
                    rsi=float(best["rsi"]),
                    momentum=float(best["momentum"]),
                )
                decision = _place_order_if_allowed(broker, decision, create_orders, allow_delayed_quotes, quote)
                state["orders_today"] = int(state.get("orders_today", 0)) + int(create_orders)
                state["last_order_at"] = datetime.now(tz=UTC).isoformat(timespec="seconds")
        else:
            reason = (
                f"No clear edge. Best={best_symbol} score={best['score']}, "
                f"second={second_symbol} score={second['score']}."
            )
            decision = SignalDecision(
                action="WATCH",
                status="no_edge",
                symbol=best_symbol,
                signal_price=round(float(best["price"]), 4),
                reason=reason,
                score=int(best["score"]),
                rsi=float(best["rsi"]),
                momentum=float(best["momentum"]),
            )

    _save_state(state_path, state)
    _log_signal(signals_path, decision)
    _write_summary(summary_path, config_path, create_orders, accounts, decision, scores, positions, errors)
    return decision


def _prepare_order_config(config: dict[str, Any], create_orders: bool, fixed_qty: int) -> dict[str, Any]:
    prepared = json.loads(json.dumps(config))
    safety = prepared.setdefault("safety", {})
    if create_orders:
        safety["dry_run"] = False
        safety["paper_only"] = True
        safety["allow_order_create"] = True
        safety["transmit_orders"] = True
        safety["max_order_qty"] = max(int(safety.get("max_order_qty", 1)), int(fixed_qty))
    else:
        safety["dry_run"] = True
        safety["allow_order_create"] = False
        safety["transmit_orders"] = False
    return prepared


def run(args: argparse.Namespace) -> None:
    config_path = Path(args.config).resolve()
    config = _load_json(config_path)
    strategy_path = _resolve_path(config.get("strategy_config_path"), config_path)
    if strategy_path is None or not strategy_path.exists():
        raise SystemExit(
            "Could not find strategy_config_path. Expected config.intraday.cash_optimized_semis_ibkr_1y.json "
            "or another valid strategy config."
        )
    strategy_config = _load_json(strategy_path)

    fixed_qty = int(args.fixed_qty or config.get("forward_test", {}).get("fixed_qty", 1))
    max_notional = float(args.max_notional or config.get("forward_test", {}).get("max_notional_usd", 300.0))
    create_orders = bool(args.create_paper_orders)
    allow_delayed = bool(args.allow_delayed_paper_quotes)

    prepared_config = _prepare_order_config(config, create_orders, fixed_qty)
    if args.market_data_type is not None:
        prepared_config.setdefault("market_data", {})["ibkr_market_data_type"] = int(args.market_data_type)

    poll_seconds = int(args.poll_seconds or prepared_config.get("market_data", {}).get("poll_seconds", 60))
    end_at = datetime.now(tz=UTC) + timedelta(hours=float(args.duration_hours))
    broker = IbkrBroker(prepared_config)
    try:
        iteration = 0
        while True:
            iteration += 1
            decision = _decide_once(
                broker=broker,
                config=prepared_config,
                strategy_config=strategy_config,
                config_path=config_path,
                create_orders=create_orders,
                fixed_qty=fixed_qty,
                max_notional=max_notional,
                allow_delayed_quotes=allow_delayed,
            )
            print(
                "[{ts}] {action} {status} {symbol} qty={qty} signal={signal:.4f} limit={limit:.2f} {reason}".format(
                    ts=datetime.now().isoformat(timespec="seconds"),
                    action=decision.action,
                    status=decision.status,
                    symbol=decision.symbol or "-",
                    qty=decision.quantity,
                    signal=decision.signal_price,
                    limit=decision.limit_price,
                    reason=decision.reason[:180],
                ),
                flush=True,
            )
            if args.once or datetime.now(tz=UTC) >= end_at:
                break
            if args.iterations and iteration >= int(args.iterations):
                break
            time.sleep(poll_seconds)
    finally:
        broker.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="IBKR paper-only SOXL/SOXS forward test runner.")
    parser.add_argument("--config", default="config.ibkr.paper_forward.example.json")
    parser.add_argument("--duration-hours", type=float, default=6.5)
    parser.add_argument("--poll-seconds", type=int, default=None)
    parser.add_argument("--iterations", type=int, default=0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--fixed-qty", type=int, default=None)
    parser.add_argument("--max-notional", type=float, default=None)
    parser.add_argument("--market-data-type", type=int, default=None, help="1=live, 3=delayed, 4=delayed frozen.")
    parser.add_argument(
        "--create-paper-orders",
        action="store_true",
        help="Actually create and transmit PAPER orders. Requires paper port and DU* account by default.",
    )
    parser.add_argument(
        "--allow-delayed-paper-quotes",
        action="store_true",
        help="Allow paper order creation even when IBKR quote is delayed. Use only for fill plumbing tests.",
    )
    try:
        run(parser.parse_args())
    except IbkrError as exc:
        cause_text = repr(exc.__cause__) if exc.__cause__ else ""
        message = str(exc)
        if "ConnectionRefused" in cause_text or "WinError 1225" in cause_text:
            message = (
                "IBKR API socket refused the connection on the configured port. "
                "Use Trader Workstation or IB Gateway, log in to the paper account, "
                "then enable API socket access. In TWS: Edit > Global Configuration > API > Settings > "
                "Enable ActiveX and Socket Clients. Paper TWS usually listens on port 7497. "
                "IBKR Desktop may be logged in visually but still not expose the TWS API socket."
            )
        raise SystemExit(message) from exc


if __name__ == "__main__":
    main()

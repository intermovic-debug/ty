from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .data import fetch_intraday_bars
from .ibkr import IbkrBroker, IbkrError, load_safety_config, socket_check
from .intraday import _symbol_score


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    price: float
    timestamp: datetime
    source: str
    delayed: bool
    scores: dict[str, dict[str, Any]]
    errors: dict[str, str]


def _load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def _resolve_path(config_path: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return config_path.parent / path


def _load_state(path: Path) -> dict[str, Any]:
    today = datetime.now(tz=UTC).date().isoformat()
    if not path.exists():
        return {"trade_day": today, "orders_today": 0, "last_order_at": None, "created_orders": []}
    with path.open("r", encoding="utf-8") as file:
        state = json.load(file)
    if state.get("trade_day") != today:
        state["trade_day"] = today
        state["orders_today"] = 0
        state["last_order_at"] = None
    state.setdefault("created_orders", [])
    return state


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with path.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)


def _append_log(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["timestamp", "command", "symbol", "action", "quantity", "limit_price", "status", "reason"]
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in fields})


def _market_snapshot(config: dict[str, Any], mock_price: float | None = None) -> MarketSnapshot:
    strategy = config["strategy"]
    market = config["market_data"]
    base_symbol = str(strategy.get("base_symbol", "SOXL")).upper()
    now = datetime.now(tz=UTC)
    if mock_price is not None:
        return MarketSnapshot(
            symbol=base_symbol,
            price=float(mock_price),
            timestamp=now,
            source="mock",
            delayed=False,
            scores={},
            errors={},
        )

    source = str(market.get("source", "yahoo")).lower()
    if source == "ibkr":
        broker = IbkrBroker(config)
        try:
            quote = broker.quote(base_symbol)
        finally:
            broker.disconnect()
        return MarketSnapshot(
            symbol=base_symbol,
            price=float(quote["price"]),
            timestamp=now,
            source="ibkr",
            delayed=bool(quote.get("delayed", False)),
            scores={},
            errors={},
        )

    interval = str(market.get("interval", "15m"))
    range_days = int(market.get("range_days", 5))
    symbols = [base_symbol] + [str(symbol).upper() for symbol in market.get("confirm_symbols", [])]
    scores: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    latest_price: float | None = None
    latest_timestamp: datetime | None = None

    for symbol in dict.fromkeys(symbols):
        try:
            bars = fetch_intraday_bars(symbol, interval=interval, range_days=range_days)
            scores[symbol] = _symbol_score(bars, _score_config(config))
            if symbol == base_symbol:
                latest_price = float(bars[-1].close)
                latest_timestamp = bars[-1].timestamp
        except Exception as exc:
            errors[symbol] = str(exc)

    if latest_price is None or latest_timestamp is None:
        raise RuntimeError(f"No {base_symbol} market data loaded. Errors: {errors}")

    return MarketSnapshot(
        symbol=base_symbol,
        price=latest_price,
        timestamp=latest_timestamp,
        source=str(market.get("source", "yahoo")),
        delayed=False,
        scores=scores,
        errors=errors,
    )


def _score_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "fast_ema": 8,
        "slow_ema": 21,
        "rsi_period": 14,
        "atr_period": 14,
    }


def _is_stale(config: dict[str, Any], snapshot: MarketSnapshot) -> tuple[bool, int]:
    threshold = int(config["market_data"].get("stale_after_minutes", 45)) * 60
    age = max(0, int((datetime.now(tz=UTC) - snapshot.timestamp).total_seconds()))
    return age > threshold, age


def _green_confirmations(snapshot: MarketSnapshot) -> list[str]:
    green: list[str] = []
    for symbol, score in snapshot.scores.items():
        if symbol == snapshot.symbol:
            continue
        if float(score.get("momentum", 0.0)) > 0 and float(score.get("price", 0.0)) > float(score.get("fast_ema", 0.0)):
            green.append(symbol)
    return green


def _build_plan(config: dict[str, Any], snapshot: MarketSnapshot, state: dict[str, Any], config_path: Path) -> dict[str, Any]:
    strategy = config["strategy"]
    safety = config["safety"]
    runtime = config["runtime"]
    stop_path = _resolve_path(config_path, str(safety.get("emergency_stop_path", "STOP_TRADING.txt")))
    stale, stale_seconds = _is_stale(config, snapshot)

    position = strategy.get("recovery_position", {})
    held_qty = int(position.get("quantity", 0))
    average_price = float(position.get("average_price", 0.0))
    needed_to_average = ((average_price / snapshot.price) - 1) if snapshot.price > 0 and average_price > 0 else 0.0

    plan: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "action": "WAIT",
        "symbol": snapshot.symbol,
        "quantity": 0,
        "limit_price": None,
        "order_type": "LMT",
        "reason": "No rule matched.",
        "market": {
            "price": round(snapshot.price, 4),
            "timestamp": snapshot.timestamp.isoformat(),
            "source": snapshot.source,
            "delayed": snapshot.delayed,
            "stale": stale,
            "stale_seconds": stale_seconds,
            "errors": snapshot.errors,
        },
        "position": {
            "quantity": held_qty,
            "average_price": average_price,
            "unrealized_pnl": round((snapshot.price - average_price) * held_qty, 2),
            "needed_to_average": needed_to_average,
        },
        "guardrails": {
            "dry_run": bool(safety.get("dry_run", True)),
            "allow_order_create": bool(safety.get("allow_order_create", False)),
            "transmit_orders": bool(safety.get("transmit_orders", False)),
            "orders_today": int(state.get("orders_today", 0)),
            "max_daily_orders": int(safety.get("max_daily_orders", 1)),
            "emergency_stop": stop_path.exists(),
        },
        "green_confirmations": _green_confirmations(snapshot),
        "ticket": None,
        "report_path": str(_resolve_path(config_path, runtime["report_path"])),
        "ticket_path": str(_resolve_path(config_path, runtime["ticket_path"])),
    }

    if stop_path.exists():
        plan["reason"] = f"Emergency stop file exists: {stop_path}"
        return plan
    if snapshot.delayed and not bool(config["market_data"].get("allow_delayed_quotes_for_trading", False)):
        plan["reason"] = "IBKR quote is delayed; actionable tickets are blocked."
        return plan
    if stale:
        plan["reason"] = f"Market data stale: {stale_seconds}s old."
        return plan
    if int(state.get("orders_today", 0)) >= int(safety.get("max_daily_orders", 1)):
        plan["reason"] = "Max daily orders reached."
        return plan
    if held_qty <= 0:
        plan["reason"] = "No recovery position quantity configured."
        return _maybe_scalp_plan(config, snapshot, plan)

    max_qty = int(safety.get("max_order_qty", 1))
    buffer_pct = float(strategy.get("limit_price_buffer_pct", 0.0))
    for rung in sorted(strategy.get("recovery_sell_ladder", []), key=lambda item: float(item["target_price"])):
        target = float(rung["target_price"])
        qty = int(rung["quantity"])
        if snapshot.price >= target:
            order_qty = max(0, min(qty, held_qty, max_qty))
            limit_price = round(max(target, snapshot.price * (1 - buffer_pct)), 2)
            if order_qty > 0:
                plan.update(
                    {
                        "action": "SELL",
                        "quantity": order_qty,
                        "limit_price": limit_price,
                        "reason": f"Recovery ladder reached: {rung.get('name', target)} at {target:.2f}.",
                        "ticket": {
                            "symbol": snapshot.symbol,
                            "action": "SELL",
                            "quantity": order_qty,
                            "order_type": "LMT",
                            "limit_price": limit_price,
                            "target_price": target,
                            "tif": str(safety.get("tif", "DAY")),
                            "outside_rth": bool(safety.get("outside_rth", False)),
                            "transmit": bool(safety.get("transmit_orders", False)),
                        },
                    }
                )
                return plan

    return _maybe_scalp_plan(config, snapshot, plan)


def _maybe_scalp_plan(config: dict[str, Any], snapshot: MarketSnapshot, plan: dict[str, Any]) -> dict[str, Any]:
    scalp = config["strategy"].get("scalp", {})
    if not bool(scalp.get("enabled", False)):
        plan["reason"] = plan["reason"] + " Scalp disabled."
        return plan
    green = plan["green_confirmations"]
    min_green = int(scalp.get("min_green_confirmations", 2))
    trigger = float(scalp.get("entry_trigger", 0.0))
    if snapshot.price < trigger:
        plan["reason"] = f"Scalp entry blocked: {snapshot.price:.2f} < trigger {trigger:.2f}."
        return plan
    if len(green) < min_green:
        plan["reason"] = f"Scalp entry blocked: green confirmations {len(green)} < {min_green}."
        return plan

    qty = min(int(scalp.get("max_quantity", 1)), int(config["safety"].get("max_order_qty", 1)))
    limit_price = round(snapshot.price, 2)
    plan.update(
        {
            "action": "BUY",
            "quantity": qty,
            "limit_price": limit_price,
            "reason": "Conservative scalp rule matched.",
            "ticket": {
                "symbol": snapshot.symbol,
                "action": "BUY",
                "quantity": qty,
                "order_type": "LMT",
                "limit_price": limit_price,
                "take_profit": round(snapshot.price * (1 + float(scalp.get("take_profit_pct", 0.015))), 2),
                "stop_loss": round(snapshot.price * (1 - float(scalp.get("stop_loss_pct", 0.008))), 2),
                "tif": str(config["safety"].get("tif", "DAY")),
                "outside_rth": bool(config["safety"].get("outside_rth", False)),
                "transmit": bool(config["safety"].get("transmit_orders", False)),
            },
        }
    )
    return plan


def _write_outputs(config: dict[str, Any], config_path: Path, plan: dict[str, Any]) -> None:
    runtime = config["runtime"]
    report_path = _resolve_path(config_path, runtime["report_path"])
    ticket_path = _resolve_path(config_path, runtime["ticket_path"])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    ticket_path.parent.mkdir(parents=True, exist_ok=True)

    guard = plan["guardrails"]
    position = plan["position"]
    market = plan["market"]
    lines = [
        "# IBKR SOXL Runner Report",
        "",
        f"- Generated at: {plan['generated_at']}",
        f"- Action: {plan['action']}",
        f"- Reason: {plan['reason']}",
        f"- Market: {plan['symbol']} {market['price']:.4f} via {market['source']} at {market['timestamp']}",
        f"- Delayed quote: {market['delayed']}",
        f"- Stale: {market['stale']} ({market['stale_seconds']} seconds)",
        f"- Recovery position: {position['quantity']} shares, average {position['average_price']:.4f}",
        f"- Unrealized PnL: {position['unrealized_pnl']:.2f} USD",
        f"- Needed to average: {position['needed_to_average']:.2%}",
        f"- Green confirmations: {', '.join(plan['green_confirmations']) or '-'}",
        "",
        "## Guardrails",
        "",
        f"- dry_run: {guard['dry_run']}",
        f"- allow_order_create: {guard['allow_order_create']}",
        f"- transmit_orders: {guard['transmit_orders']}",
        f"- orders_today: {guard['orders_today']} / {guard['max_daily_orders']}",
        f"- emergency_stop: {guard['emergency_stop']}",
    ]
    if market["errors"]:
        lines.extend(["", "## Data Errors", ""])
        for symbol, message in sorted(market["errors"].items()):
            lines.append(f"- {symbol}: {message}")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")

    ticket = plan.get("ticket")
    if not ticket:
        ticket_path.write_text(
            "# IBKR Order Ticket\n\nNo actionable ticket right now.\n",
            encoding="utf-8-sig",
        )
        return

    ticket_lines = [
        "# IBKR Order Ticket",
        "",
        "Review this before doing anything in TWS.",
        "",
        f"- Symbol: {ticket['symbol']}",
        f"- Action: {ticket['action']}",
        f"- Quantity: {ticket['quantity']}",
        f"- Order type: {ticket['order_type']}",
        f"- Limit price: {ticket['limit_price']}",
        f"- TIF: {ticket['tif']}",
        f"- Outside RTH: {ticket['outside_rth']}",
        f"- Transmit: {ticket['transmit']}",
        "",
        "Default config does not transmit live orders. Keep paper mode until socket, quotes, order creation, cancel, and logs are verified.",
    ]
    extra_keys = ["target_price", "take_profit", "stop_loss"]
    for key in extra_keys:
        if key in ticket:
            ticket_lines.insert(-2, f"- {key}: {ticket[key]}")
    ticket_path.write_text("\n".join(ticket_lines) + "\n", encoding="utf-8-sig")


def _print_plan(plan: dict[str, Any]) -> None:
    market = plan["market"]
    print(f"action={plan['action']} symbol={plan['symbol']} qty={plan['quantity']} limit={plan['limit_price']}")
    print(
        f"price={market['price']} source={market['source']} "
        f"delayed={market['delayed']} stale={market['stale']} age={market['stale_seconds']}s"
    )
    print(f"reason={plan['reason']}")
    print(f"report={plan['report_path']}")
    print(f"ticket={plan['ticket_path']}")


def _run_plan(config_path: Path, mock_price: float | None) -> dict[str, Any]:
    config = _load_config(config_path)
    state_path = _resolve_path(config_path, config["runtime"]["state_path"])
    state = _load_state(state_path)
    snapshot = _market_snapshot(config, mock_price)
    plan = _build_plan(config, snapshot, state, config_path)
    _write_outputs(config, config_path, plan)
    return plan


def _stage_order(config_path: Path, mock_price: float | None, create_order: bool) -> dict[str, Any]:
    config = _load_config(config_path)
    state_path = _resolve_path(config_path, config["runtime"]["state_path"])
    log_path = _resolve_path(config_path, config["runtime"]["log_path"])
    state = _load_state(state_path)
    snapshot = _market_snapshot(config, mock_price)
    plan = _build_plan(config, snapshot, state, config_path)
    _write_outputs(config, config_path, plan)
    if not plan.get("ticket"):
        return {"status": "no_ticket", "reason": plan["reason"], "plan": plan}

    safety = load_safety_config(config)
    if int(state.get("orders_today", 0)) >= int(config["safety"].get("max_daily_orders", 1)):
        return {"status": "blocked", "reason": "Max daily orders reached.", "plan": plan}

    broker = IbkrBroker(config)
    ticket = plan["ticket"]
    result = broker.place_limit_order(
        str(ticket["symbol"]),
        str(ticket["action"]),
        int(ticket["quantity"]),
        float(ticket["limit_price"]),
        create_order_requested=create_order,
    )
    broker.disconnect()
    if not safety.dry_run and result.get("status") not in {"dry_run_blocked"}:
        state["orders_today"] = int(state.get("orders_today", 0)) + 1
        state["last_order_at"] = datetime.now(tz=UTC).isoformat()
        state["created_orders"].append(result)
        _save_state(state_path, state)

    _append_log(
        log_path,
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "command": "stage-order",
            "symbol": ticket["symbol"],
            "action": ticket["action"],
            "quantity": ticket["quantity"],
            "limit_price": ticket["limit_price"],
            "status": result.get("status"),
            "reason": plan["reason"],
        },
    )
    return result


def run_watch(config_path: Path, mock_price: float | None, iterations: int | None) -> None:
    config = _load_config(config_path)
    poll_seconds = int(config["runtime"].get("poll_seconds", 30))
    count = 0
    while True:
        try:
            plan = _run_plan(config_path, mock_price)
            _print_plan(plan)
        except Exception as exc:
            print(f"watch_error={exc}")
        count += 1
        if iterations is not None and count >= iterations:
            return
        time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="IBKR paper/live gated runner for SOXL recovery trading.")
    parser.add_argument("--config", default="config.ibkr.example.json")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status")
    subparsers.add_parser("socket-test")
    subparsers.add_parser("connect-test")
    subparsers.add_parser("account")
    subparsers.add_parser("positions")
    snapshot_parser = subparsers.add_parser("write-snapshot")
    snapshot_parser.add_argument("--path", default="account_snapshot.ibkr.json")
    quote_parser = subparsers.add_parser("quote")
    quote_parser.add_argument("--symbol", default="SOXL")

    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--mock-price", type=float, default=None)

    watch_parser = subparsers.add_parser("watch")
    watch_parser.add_argument("--mock-price", type=float, default=None)
    watch_parser.add_argument("--iterations", type=int, default=None)

    stage_parser = subparsers.add_parser("stage-order")
    stage_parser.add_argument("--mock-price", type=float, default=None)
    stage_parser.add_argument("--create-order", action="store_true")

    args = parser.parse_args()
    config_path = Path(args.config)
    config = _load_config(config_path)

    if args.command == "status":
        safety = config["safety"]
        connection = config["connection"]
        print(f"config={config_path}")
        print(f"host={connection['host']} port={connection['port']} client_id={connection['client_id']}")
        print(
            "dry_run={dry_run} allow_order_create={allow_order_create} transmit_orders={transmit_orders} paper_only={paper_only}".format(
                **safety
            )
        )
        return
    if args.command == "socket-test":
        print(json.dumps(socket_check(config), indent=2))
        return
    if args.command == "connect-test":
        broker = IbkrBroker(config)
        try:
            broker.connect()
            print("IBKR connect-test OK")
        except IbkrError as exc:
            raise SystemExit(f"IBKR blocked: {exc}") from exc
        finally:
            broker.disconnect()
        return
    if args.command == "account":
        broker = IbkrBroker(config)
        try:
            print(json.dumps(broker.account_summary(), indent=2))
        except IbkrError as exc:
            raise SystemExit(f"IBKR blocked: {exc}") from exc
        finally:
            broker.disconnect()
        return
    if args.command == "write-snapshot":
        broker = IbkrBroker(config)
        try:
            summary = broker.account_summary()
            positions = broker.positions()
        except IbkrError as exc:
            raise SystemExit(f"IBKR blocked: {exc}") from exc
        finally:
            broker.disconnect()
        cash = float(summary.get("AvailableFunds_USD", summary.get("TotalCashValue_USD", 0.0)) or 0.0)
        snapshot = {
            "source": "ibkr_api",
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "cash_usd": round(cash, 2),
            "net_liquidation_usd": round(float(summary.get("NetLiquidation_USD", 0.0) or 0.0), 2),
            "buying_power_usd": round(float(summary.get("BuyingPower_USD", 0.0) or 0.0), 2),
            "holdings": positions,
        }
        path = Path(args.path)
        if not path.is_absolute():
            path = config_path.parent / path
        path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {path}")
        return
    if args.command == "positions":
        broker = IbkrBroker(config)
        try:
            print(json.dumps(broker.positions(), indent=2))
        except IbkrError as exc:
            raise SystemExit(f"IBKR blocked: {exc}") from exc
        finally:
            broker.disconnect()
        return
    if args.command == "quote":
        broker = IbkrBroker(config)
        try:
            print(json.dumps(broker.quote(args.symbol), indent=2))
        except IbkrError as exc:
            raise SystemExit(f"IBKR blocked: {exc}") from exc
        finally:
            broker.disconnect()
        return
    if args.command == "plan":
        plan = _run_plan(config_path, args.mock_price)
        _print_plan(plan)
        return
    if args.command == "watch":
        run_watch(config_path, args.mock_price, args.iterations)
        return
    if args.command == "stage-order":
        try:
            result = _stage_order(config_path, args.mock_price, args.create_order)
            print(json.dumps(result, indent=2))
        except IbkrError as exc:
            raise SystemExit(f"IBKR blocked: {exc}") from exc


if __name__ == "__main__":
    main()

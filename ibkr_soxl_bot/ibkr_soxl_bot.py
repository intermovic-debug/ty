from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class BotError(RuntimeError):
    pass


@dataclass
class Config:
    path: Path
    raw: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> "Config":
        if not path.exists():
            raise BotError(f"Config not found: {path}")
        return cls(path=path, raw=json.loads(path.read_text(encoding="utf-8")))

    @property
    def ibkr(self) -> dict[str, Any]:
        return self.raw.setdefault("ibkr", {})

    @property
    def trading(self) -> dict[str, Any]:
        return self.raw.setdefault("trading", {})

    @property
    def recovery_position(self) -> dict[str, Any]:
        return self.raw.setdefault("recovery_position", {})

    @property
    def scalp_rules(self) -> dict[str, Any]:
        return self.raw.setdefault("scalp_rules", {})

    def symbol(self) -> str:
        return str(self.trading.get("symbol", "SOXL")).upper()

    def max_qty_per_order(self) -> int:
        return int(self.trading.get("max_qty_per_order", 1))

    def allow_live_orders(self) -> bool:
        return bool(self.trading.get("allow_live_orders", False))

    def paper_trading(self) -> bool:
        return bool(self.ibkr.get("paper_trading", True))


def _import_ib_insync():
    try:
        from ib_insync import IB, Stock, LimitOrder
    except ImportError as exc:
        raise BotError(
            "Missing dependency. Run: python -m pip install -r requirements.txt"
        ) from exc
    return IB, Stock, LimitOrder


def connect_ib(config: Config):
    IB, _Stock, _LimitOrder = _import_ib_insync()
    ib = IB()
    host = config.ibkr.get("host", "127.0.0.1")
    port = int(config.ibkr.get("port", 7497))
    client_id = int(config.ibkr.get("client_id", 7))
    try:
        ib.connect(host, port, clientId=client_id, timeout=10)
    except Exception as exc:
        raise BotError(
            "IBKR API connection failed. Start TWS/IB Gateway, log in to the "
            f"matching account, and confirm API socket port {port} is enabled."
        ) from exc
    return ib


def make_contract(config: Config):
    _IB, Stock, _LimitOrder = _import_ib_insync()
    return Stock(
        config.symbol(),
        str(config.trading.get("exchange", "SMART")),
        str(config.trading.get("currency", "USD")),
    )


def status(config: Config) -> None:
    print(f"config: {config.path}")
    print(f"symbol: {config.symbol()}")
    print(f"paper_trading: {config.paper_trading()}")
    print(f"host: {config.ibkr.get('host', '127.0.0.1')}")
    print(f"port: {config.ibkr.get('port', 7497)}")
    print(f"client_id: {config.ibkr.get('client_id', 7)}")
    print(f"allow_live_orders: {config.allow_live_orders()}")
    print(f"max_qty_per_order: {config.max_qty_per_order()}")
    print(f"recovery_qty: {config.recovery_position.get('qty')}")
    print(f"recovery_avg_price: {config.recovery_position.get('avg_price')}")
    print("sell_ladder:")
    for item in config.recovery_position.get("sell_ladder", []):
        print(f"  - {item['price']} / {item['qty']} shares")


def connect_test(config: Config) -> None:
    ib = connect_ib(config)
    try:
        print("connected:", ib.isConnected())
        print("managed_accounts:", ib.managedAccounts())
    finally:
        ib.disconnect()


def quote(config: Config) -> None:
    ib = connect_ib(config)
    try:
        contract = make_contract(config)
        ib.qualifyContracts(contract)
        ticker = ib.reqMktData(contract, "", False, False)
        ib.sleep(2)
        print(f"symbol: {config.symbol()}")
        print(f"bid: {ticker.bid}")
        print(f"ask: {ticker.ask}")
        print(f"last: {ticker.last}")
        print(f"marketPrice: {ticker.marketPrice()}")
    finally:
        ib.disconnect()


def calculate_plan(config: Config, price: float) -> None:
    qty = int(config.recovery_position.get("qty", 0))
    avg = float(config.recovery_position.get("avg_price", 0))
    if qty <= 0 or avg <= 0:
        raise BotError("recovery_position.qty and avg_price are required")

    cost = qty * avg
    value = qty * price
    pnl = value - cost
    pnl_pct = ((price / avg) - 1.0) * 100
    to_avg_pct = ((avg / price) - 1.0) * 100 if price > 0 else 0

    print(f"current_price: {price:.4f}")
    print(f"recovery_qty: {qty}")
    print(f"avg_price: {avg:.4f}")
    print(f"unrealized_pnl_usd: {pnl:.2f}")
    print(f"unrealized_pnl_pct: {pnl_pct:.2f}%")
    print(f"needed_to_avg_pct: {to_avg_pct:.2f}%")
    print()
    print("sell ladder estimate:")
    total_proceeds = 0.0
    total_qty = 0
    for item in config.recovery_position.get("sell_ladder", []):
        sell_price = float(item["price"])
        sell_qty = int(item["qty"])
        leg_pnl = (sell_price - avg) * sell_qty
        total_proceeds += sell_price * sell_qty
        total_qty += sell_qty
        print(f"  {sell_price:.2f} / {sell_qty} shares -> pnl_usd {leg_pnl:.2f}")
    if total_qty:
        ladder_pnl = total_proceeds - (avg * total_qty)
        print(f"total_ladder_qty: {total_qty}")
        print(f"total_ladder_pnl_usd: {ladder_pnl:.2f}")


def validate_order(config: Config, action: str, qty: int, limit_price: float, live: bool) -> None:
    if config.symbol() != "SOXL":
        raise BotError("Only SOXL is allowed in this scaffold")
    if action not in {"BUY", "SELL"}:
        raise BotError("action must be BUY or SELL")
    if qty <= 0:
        raise BotError("qty must be positive")
    if qty > config.max_qty_per_order():
        raise BotError(f"qty exceeds max_qty_per_order: {qty} > {config.max_qty_per_order()}")
    if limit_price <= 0:
        raise BotError("limit price must be positive")
    if live and not config.allow_live_orders():
        raise BotError("Live order blocked. Set trading.allow_live_orders=true in local config first.")
    if live and not config.paper_trading():
        raise BotError("Live-account orders are blocked in this scaffold. Use Paper first.")


def place_limit(config: Config, action: str, qty: int, limit_price: float, live: bool) -> None:
    action = action.upper()
    validate_order(config, action, qty, limit_price, live)
    print(f"order_request: {action} {qty} {config.symbol()} LMT {limit_price:.2f}")
    if not live:
        print("DRY_RUN: no order sent")
        return

    ib = connect_ib(config)
    try:
        _IB, _Stock, LimitOrder = _import_ib_insync()
        contract = make_contract(config)
        ib.qualifyContracts(contract)
        order = LimitOrder(action, qty, limit_price)
        order.outsideRth = bool(config.trading.get("outside_rth", False))
        trade = ib.placeOrder(contract, order)
        ib.sleep(1)
        print("submitted:", trade.orderStatus.status)
        print("orderId:", trade.order.orderId)
    finally:
        ib.disconnect()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="IBKR SOXL safety-first bot scaffold")
    parser.add_argument("--config", default="config.example.json")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status")
    sub.add_parser("connect-test")
    sub.add_parser("quote")

    plan = sub.add_parser("plan")
    plan.add_argument("--price", type=float, required=True)

    order = sub.add_parser("place-limit")
    order.add_argument("--action", choices=["BUY", "SELL"], required=True)
    order.add_argument("--qty", type=int, required=True)
    order.add_argument("--limit", type=float, required=True)
    order.add_argument("--live", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config = Config.load(Path(args.config))

    if args.command == "status":
        status(config)
    elif args.command == "connect-test":
        connect_test(config)
    elif args.command == "quote":
        quote(config)
    elif args.command == "plan":
        calculate_plan(config, args.price)
    elif args.command == "place-limit":
        place_limit(config, args.action, args.qty, args.limit, args.live)
    else:
        parser.error(f"Unsupported command: {args.command}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BotError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)

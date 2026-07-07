from __future__ import annotations

import socket
from dataclasses import dataclass
from math import isfinite
from typing import Any


class IbkrError(RuntimeError):
    pass


@dataclass(frozen=True)
class IbkrConnectionConfig:
    host: str
    port: int
    client_id: int
    readonly: bool
    timeout_seconds: float


@dataclass(frozen=True)
class IbkrSafetyConfig:
    dry_run: bool
    paper_only: bool
    allow_order_create: bool
    transmit_orders: bool
    allowed_symbols: tuple[str, ...]
    paper_ports: tuple[int, ...]
    max_order_qty: int
    limit_order_only: bool
    outside_rth: bool
    tif: str
    order_ref: str


def load_connection_config(config: dict[str, Any]) -> IbkrConnectionConfig:
    raw = config.get("connection", {})
    return IbkrConnectionConfig(
        host=str(raw.get("host", "127.0.0.1")),
        port=int(raw.get("port", 7497)),
        client_id=int(raw.get("client_id", 27)),
        readonly=bool(raw.get("readonly", False)),
        timeout_seconds=float(raw.get("timeout_seconds", 8)),
    )


def load_safety_config(config: dict[str, Any]) -> IbkrSafetyConfig:
    raw = config.get("safety", {})
    return IbkrSafetyConfig(
        dry_run=bool(raw.get("dry_run", True)),
        paper_only=bool(raw.get("paper_only", True)),
        allow_order_create=bool(raw.get("allow_order_create", False)),
        transmit_orders=bool(raw.get("transmit_orders", False)),
        allowed_symbols=tuple(str(symbol).upper() for symbol in raw.get("allowed_symbols", ["SOXL", "SOXS"])),
        paper_ports=tuple(int(port) for port in raw.get("paper_ports", [7497, 4002])),
        max_order_qty=int(raw.get("max_order_qty", 1)),
        limit_order_only=bool(raw.get("limit_order_only", True)),
        outside_rth=bool(raw.get("outside_rth", False)),
        tif=str(raw.get("tif", "DAY")),
        order_ref=str(raw.get("order_ref", "soxswing-ibkr")),
    )


def socket_check(config: dict[str, Any]) -> dict[str, Any]:
    connection = load_connection_config(config)
    try:
        with socket.create_connection(
            (connection.host, connection.port),
            timeout=connection.timeout_seconds,
        ):
            return {
                "ok": True,
                "host": connection.host,
                "port": connection.port,
                "message": "TWS or IB Gateway socket is reachable.",
            }
    except OSError as exc:
        return {
            "ok": False,
            "host": connection.host,
            "port": connection.port,
            "message": str(exc),
        }


def _require_ib_insync() -> tuple[Any, Any, Any]:
    try:
        from ib_insync import IB, LimitOrder, Stock
    except ImportError as exc:
        raise IbkrError(
            "ib-insync is not installed. Install it with: python -m pip install -r requirements-ibkr.txt"
        ) from exc
    return IB, LimitOrder, Stock


class IbkrBroker:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.connection = load_connection_config(config)
        self.safety = load_safety_config(config)
        self._ib: Any | None = None

    def connect(self) -> Any:
        if self._ib is not None and self._ib.isConnected():
            return self._ib
        if self.safety.paper_only and self.connection.port not in self.safety.paper_ports:
            raise IbkrError(
                f"paper_only=true blocks port {self.connection.port}. "
                f"Allowed paper ports: {list(self.safety.paper_ports)}"
            )

        IB, _LimitOrder, _Stock = _require_ib_insync()
        ib = IB()
        try:
            ib.connect(
                self.connection.host,
                self.connection.port,
                clientId=self.connection.client_id,
                timeout=self.connection.timeout_seconds,
                readonly=self.connection.readonly,
            )
        except Exception as exc:
            message = str(exc)
            if "client id" in message.lower() or "peer closed" in message.lower():
                hint = (
                    f"IBKR client_id={self.connection.client_id} is already in use. "
                    "Close the other bot/process or use a different client_id in the config."
                )
            else:
                hint = (
                    "IBKR API socket is reachable, but full ib-insync synchronization failed. "
                    "If TWS still has 'Read-Only API' enabled, positions, executions, open orders, "
                    "and paper order creation will be blocked. Use socket-test/plan in dry-run, or "
                    "disable Read-Only API only when you are ready to test paper mode."
                )
            raise IbkrError(
                hint
            ) from exc
        self._ib = ib
        return ib

    def disconnect(self) -> None:
        if self._ib is not None and self._ib.isConnected():
            self._ib.disconnect()

    def positions(self) -> list[dict[str, Any]]:
        ib = self.connect()
        rows: list[dict[str, Any]] = []
        for position in ib.positions():
            contract = position.contract
            rows.append(
                {
                    "account": position.account,
                    "symbol": getattr(contract, "symbol", ""),
                    "sec_type": getattr(contract, "secType", ""),
                    "currency": getattr(contract, "currency", ""),
                    "position": float(position.position),
                    "average_cost": float(position.avgCost),
                }
            )
        return rows

    def account_summary(self) -> dict[str, Any]:
        ib = self.connect()
        rows = ib.accountSummary()
        summary: dict[str, Any] = {}
        for row in rows:
            key = str(row.tag)
            currency = str(row.currency or "")
            value: Any = row.value
            parsed = _safe_float(value)
            if parsed is not None:
                value = parsed
            if currency:
                summary[f"{key}_{currency}"] = value
            summary.setdefault(key, value)
        return summary

    def quote(self, symbol: str, wait_seconds: float = 3.0) -> dict[str, Any]:
        symbol = symbol.upper()
        if symbol not in self.safety.allowed_symbols and self.safety.allowed_symbols:
            raise IbkrError(f"{symbol} is not in allowed_symbols: {list(self.safety.allowed_symbols)}")

        ib = self.connect()
        _IB, _LimitOrder, Stock = _require_ib_insync()
        contract = Stock(symbol, "SMART", "USD")
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise IbkrError(f"IBKR could not qualify stock contract for {symbol}.")

        market_data_type = int(self.config.get("market_data", {}).get("ibkr_market_data_type", 3))
        ib.reqMarketDataType(market_data_type)
        ticker = ib.reqMktData(qualified[0], "", True, False)
        ib.sleep(wait_seconds)
        ib.cancelMktData(qualified[0])

        candidates = [
            ("market_price", _safe_float(ticker.marketPrice())),
            ("last", _safe_float(ticker.last)),
            ("close", _safe_float(ticker.close)),
            ("bid", _safe_float(ticker.bid)),
            ("ask", _safe_float(ticker.ask)),
        ]
        selected_name = None
        selected_price = None
        for name, value in candidates:
            if value is not None and value > 0:
                selected_name = name
                selected_price = value
                break

        if selected_price is None:
            raise IbkrError(
                f"No usable quote for {symbol}. Check TWS market data permissions or delayed data settings."
            )

        return {
            "symbol": symbol,
            "price": round(selected_price, 4),
            "price_field": selected_name,
            "bid": _safe_float(ticker.bid),
            "ask": _safe_float(ticker.ask),
            "last": _safe_float(ticker.last),
            "close": _safe_float(ticker.close),
            "market_data_type": market_data_type,
            "delayed": market_data_type in {3, 4},
        }

    def limit_order_preview(
        self,
        symbol: str,
        action: str,
        quantity: int,
        limit_price: float,
    ) -> dict[str, Any]:
        self._validate_limit_order(symbol, action, quantity, limit_price)
        return {
            "status": "preview",
            "symbol": symbol.upper(),
            "action": action.upper(),
            "quantity": int(quantity),
            "order_type": "LMT",
            "limit_price": round(float(limit_price), 2),
            "outside_rth": self.safety.outside_rth,
            "tif": self.safety.tif,
            "transmit": self.safety.transmit_orders,
            "dry_run": self.safety.dry_run,
        }

    def place_limit_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        limit_price: float,
        create_order_requested: bool,
    ) -> dict[str, Any]:
        self._validate_limit_order(symbol, action, quantity, limit_price)
        preview = self.limit_order_preview(symbol, action, quantity, limit_price)
        if self.safety.dry_run:
            preview["status"] = "dry_run_blocked"
            preview["message"] = "dry_run=true, so no order was created in TWS."
            return preview
        if not self.safety.allow_order_create:
            raise IbkrError("allow_order_create=false blocks creating an order in TWS.")
        if not create_order_requested:
            raise IbkrError("Pass the explicit CLI flag to create an order in TWS.")

        ib = self.connect()
        _IB, LimitOrder, Stock = _require_ib_insync()
        contract = Stock(symbol.upper(), "SMART", "USD")
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise IbkrError(f"IBKR could not qualify stock contract for {symbol}.")

        order = LimitOrder(action.upper(), int(quantity), round(float(limit_price), 2))
        order.transmit = self.safety.transmit_orders
        order.outsideRth = self.safety.outside_rth
        order.tif = self.safety.tif
        order.orderRef = self.safety.order_ref

        trade = ib.placeOrder(qualified[0], order)
        ib.sleep(1)
        return {
            "status": str(getattr(trade.orderStatus, "status", "")),
            "symbol": symbol.upper(),
            "action": action.upper(),
            "quantity": int(quantity),
            "order_type": "LMT",
            "limit_price": round(float(limit_price), 2),
            "outside_rth": self.safety.outside_rth,
            "tif": self.safety.tif,
            "transmit": self.safety.transmit_orders,
            "order_id": getattr(order, "orderId", None),
            "perm_id": getattr(trade.orderStatus, "permId", None),
        }

    def _validate_limit_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        limit_price: float,
    ) -> None:
        symbol = symbol.upper()
        action = action.upper()
        if symbol not in self.safety.allowed_symbols:
            raise IbkrError(f"{symbol} is not in allowed_symbols: {list(self.safety.allowed_symbols)}")
        if action not in {"BUY", "SELL"}:
            raise IbkrError(f"Unsupported action: {action}")
        if quantity <= 0:
            raise IbkrError("Order quantity must be positive.")
        if quantity > self.safety.max_order_qty:
            raise IbkrError(f"Order quantity {quantity} exceeds max_order_qty={self.safety.max_order_qty}.")
        if self.safety.limit_order_only and limit_price <= 0:
            raise IbkrError("limit_order_only=true requires a positive limit price.")


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(number):
        return None
    return number

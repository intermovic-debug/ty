from __future__ import annotations

import argparse
import ctypes
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

user32 = ctypes.WinDLL("user32", use_last_error=True)

VK_CONTROL = 0x11
VK_RETURN = 0x0D
VK_BACK = 0x08
KEYEVENTF_KEYUP = 0x0002

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = BASE_DIR / "configs" / "meritz_soxl_home_config.template.json"


@dataclass
class Point:
    x: int
    y: int

    @classmethod
    def from_list(cls, value: Any) -> "Point | None":
        if value is None:
            return None
        if not isinstance(value, list) or len(value) != 2:
            return None
        return cls(int(value[0]), int(value[1]))

    def to_list(self) -> list[int]:
        return [self.x, self.y]


@dataclass
class Signal:
    signal_id: str
    symbol: str
    action: str
    qty: int
    created_at: datetime
    note: str = ""


class ConfigError(RuntimeError):
    pass


class Win32Input:
    class CURSOR_POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    @staticmethod
    def get_cursor_pos() -> Point:
        point = Win32Input.CURSOR_POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            raise ctypes.WinError(ctypes.get_last_error())
        return Point(point.x, point.y)

    @staticmethod
    def set_cursor_pos(point: Point) -> None:
        if not user32.SetCursorPos(point.x, point.y):
            raise ctypes.WinError(ctypes.get_last_error())

    @staticmethod
    def _key_event(vk: int, key_up: bool = False) -> None:
        flags = KEYEVENTF_KEYUP if key_up else 0
        user32.keybd_event(vk, 0, flags, 0)

    @staticmethod
    def press(vk: int, delay: float = 0.06) -> None:
        Win32Input._key_event(vk, False)
        time.sleep(delay)
        Win32Input._key_event(vk, True)

    @staticmethod
    def hotkey(*keys: int, hold_delay: float = 0.06) -> None:
        for key in keys:
            Win32Input._key_event(key, False)
            time.sleep(hold_delay)
        for key in reversed(keys):
            Win32Input._key_event(key, True)
            time.sleep(hold_delay)

    @staticmethod
    def left_click(point: Point, settle: float = 0.15) -> None:
        Win32Input.set_cursor_pos(point)
        time.sleep(0.05)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.03)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(settle)

    @staticmethod
    def double_click(point: Point, settle: float = 0.18) -> None:
        Win32Input.left_click(point, settle=0.06)
        Win32Input.left_click(point, settle=settle)

    @staticmethod
    def type_ascii(text: str, inter_key_delay: float = 0.04) -> None:
        for char in text:
            if char == "\n":
                Win32Input.press(VK_RETURN)
                continue
            code = user32.VkKeyScanW(ord(char))
            if code == -1:
                raise ValueError(f"Unsupported character: {char!r}")
            vk = code & 0xFF
            shift_state = (code >> 8) & 0xFF
            if shift_state & 1:
                Win32Input._key_event(0x10, False)
            Win32Input.press(vk, delay=inter_key_delay)
            if shift_state & 1:
                Win32Input._key_event(0x10, True)
            time.sleep(inter_key_delay)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class Config:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.raw = load_json(path)

    def save(self) -> None:
        write_json(self.path, self.raw)

    def safety(self) -> dict[str, Any]:
        return self.raw.setdefault("safety", {})

    def ui(self) -> dict[str, Any]:
        return self.raw.setdefault("ui", {})

    def paths(self) -> dict[str, Any]:
        return self.raw.setdefault("paths", {})

    def signal_file(self) -> Path:
        value = self.raw.get("signal", {}).get("signal_file")
        if not value:
            raise ConfigError("signal.signal_file is required")
        p = Path(value)
        return p if p.is_absolute() else BASE_DIR / p

    def stop_file(self) -> Path:
        value = self.safety().get("emergency_stop_file", "STOP_TRADING.txt")
        p = Path(value)
        return p if p.is_absolute() else BASE_DIR / p

    def state_file(self) -> Path:
        value = self.paths().get("state_file", "meritz_soxl_state.json")
        p = Path(value)
        return p if p.is_absolute() else BASE_DIR / p

    def log_file(self) -> Path:
        value = self.paths().get("log_file", "meritz_soxl_bot.log")
        p = Path(value)
        return p if p.is_absolute() else BASE_DIR / p

    def dry_run(self) -> bool:
        return bool(self.safety().get("dry_run", True))

    def allowed_symbols(self) -> list[str]:
        return [str(x).upper() for x in self.safety().get("allowed_symbols", ["SOXL"])]

    def allowed_actions(self) -> list[str]:
        return [str(x).upper() for x in self.safety().get("allowed_actions", ["BUY", "SELL"])]

    def max_qty_per_order(self) -> int:
        return int(self.safety().get("max_qty_per_order", 1))

    def max_orders_per_day(self) -> int:
        return int(self.safety().get("max_orders_per_day", 4))

    def stale_signal_seconds(self) -> int:
        return int(self.safety().get("stale_signal_seconds", 90))

    def cooldown_seconds_for_action(self, action: str) -> int:
        by_action = self.safety().get("cooldown_seconds_by_action", {})
        if isinstance(by_action, dict) and action.upper() in by_action:
            return int(by_action[action.upper()])
        return int(self.safety().get("cooldown_seconds", 300))

    def poll_seconds(self) -> float:
        return float(self.raw.get("signal", {}).get("poll_seconds", 2))

    def focus_click(self) -> Point | None:
        return Point.from_list(self.ui().get("focus_click"))

    def confirm_button(self) -> Point | None:
        return Point.from_list(self.ui().get("confirm_button"))

    def confirm_after_order(self) -> bool:
        return bool(self.ui().get("confirm_after_order", False))

    def settle_delay(self) -> float:
        return float(self.ui().get("settle_delay_ms", 250)) / 1000.0

    def symbol_ui(self, symbol: str) -> dict[str, Point]:
        item = self.ui().get("symbols", {}).get(symbol.upper())
        if not item:
            raise ConfigError(f"No UI mapping for symbol: {symbol}")
        result: dict[str, Point] = {}
        for key in ("panel_anchor", "qty_input", "buy_button", "sell_button"):
            point = Point.from_list(item.get(key))
            if point is None:
                raise ConfigError(f"Missing ui.symbols.{symbol}.{key}")
            result[key] = point
        return result


class Bot:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.state_path = config.state_file()
        self.state = self._load_state()
        self.logger = self._build_logger(config.log_file())
        self.last_signal_mtime: float | None = None

    def _build_logger(self, path: Path) -> logging.Logger:
        logger = logging.getLogger("meritz_sox_bot")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
        return logger

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            return load_json(self.state_path)
        return {
            "trading_date": datetime.now().date().isoformat(),
            "orders_today": 0,
            "last_signal_id": None,
            "last_order_ts": None,
        }

    def _save_state(self) -> None:
        write_json(self.state_path, self.state)

    def _rotate_day_if_needed(self) -> None:
        today = datetime.now().date().isoformat()
        if self.state.get("trading_date") != today:
            self.state = {
                "trading_date": today,
                "orders_today": 0,
                "last_signal_id": None,
                "last_order_ts": None,
            }
            self._save_state()

    def poll_signal(self) -> Signal | None:
        path = self.config.signal_file()
        if not path.exists():
            return None
        stat = path.stat()
        if self.last_signal_mtime is not None and stat.st_mtime <= self.last_signal_mtime:
            return None
        self.last_signal_mtime = stat.st_mtime
        raw = load_json(path)
        return Signal(
            signal_id=str(raw["id"]),
            symbol=str(raw["symbol"]).upper(),
            action=str(raw["action"]).upper(),
            qty=int(raw.get("qty", 1)),
            created_at=datetime.fromisoformat(str(raw["created_at"])),
            note=str(raw.get("note", "")),
        )

    def validate(self, signal: Signal) -> None:
        if signal.signal_id == self.state.get("last_signal_id"):
            raise ConfigError(f"Duplicate signal ignored: {signal.signal_id}")
        if signal.symbol not in self.config.allowed_symbols():
            raise ConfigError(f"Blocked symbol: {signal.symbol}")
        if signal.action not in self.config.allowed_actions():
            raise ConfigError(f"Blocked action: {signal.action}")
        if signal.qty <= 0 or signal.qty > self.config.max_qty_per_order():
            raise ConfigError(f"qty blocked: {signal.qty}")
        age = datetime.now(signal.created_at.tzinfo) - signal.created_at
        if age.total_seconds() > self.config.stale_signal_seconds():
            raise ConfigError("Signal is stale")
        if self.config.dry_run():
            return
        if int(self.state.get("orders_today", 0)) >= self.config.max_orders_per_day():
            raise ConfigError("Daily order limit reached")
        cooldown = self.config.cooldown_seconds_for_action(signal.action)
        last_ts = self.state.get("last_order_ts")
        if cooldown > 0 and last_ts:
            last = datetime.fromisoformat(last_ts)
            if (datetime.now(last.tzinfo) - last).total_seconds() < cooldown:
                raise ConfigError("Cooldown still active")

    def click(self, point: Point, label: str) -> None:
        if self.config.dry_run():
            self.logger.info("DRY_RUN click %-14s (%s, %s)", label, point.x, point.y)
            return
        Win32Input.left_click(point, settle=self.config.settle_delay())

    def double_click(self, point: Point, label: str) -> None:
        if self.config.dry_run():
            self.logger.info("DRY_RUN double_click %-7s (%s, %s)", label, point.x, point.y)
            return
        Win32Input.double_click(point, settle=self.config.settle_delay())

    def replace_text(self, text: str) -> None:
        if self.config.dry_run():
            self.logger.info("DRY_RUN type %s", text)
            return
        Win32Input.hotkey(VK_CONTROL, 0x41)
        time.sleep(0.08)
        Win32Input.press(VK_BACK)
        time.sleep(0.08)
        Win32Input.type_ascii(text)

    def execute(self, signal: Signal) -> None:
        self.logger.info("Signal %s %s %s qty=%s", signal.signal_id, signal.symbol, signal.action, signal.qty)
        focus = self.config.focus_click()
        if focus:
            self.click(focus, "focus")
        ui = self.config.symbol_ui(signal.symbol)
        self.click(ui["panel_anchor"], "panel")
        self.double_click(ui["qty_input"], "qty")
        self.replace_text(str(signal.qty))
        button = ui["buy_button"] if signal.action == "BUY" else ui["sell_button"]
        self.click(button, signal.action.lower())
        if self.config.confirm_after_order():
            confirm = self.config.confirm_button()
            if confirm is None:
                raise ConfigError("confirm_button required")
            self.click(confirm, "confirm")
        if self.config.dry_run():
            self.logger.info("DRY_RUN complete; state not updated")
            return
        self.state["last_signal_id"] = signal.signal_id
        self.state["orders_today"] = int(self.state.get("orders_today", 0)) + 1
        self.state["last_order_ts"] = datetime.now().astimezone().isoformat()
        self._save_state()

    def run(self, once: bool = False) -> None:
        self.logger.info("Bot started dry_run=%s", self.config.dry_run())
        while True:
            self._rotate_day_if_needed()
            if self.config.stop_file().exists():
                self.logger.warning("Stop file exists: %s", self.config.stop_file())
                if once:
                    return
                time.sleep(self.config.poll_seconds())
                continue
            signal = self.poll_signal()
            if signal is not None:
                try:
                    self.validate(signal)
                    self.execute(signal)
                except Exception as exc:  # noqa: BLE001
                    self.logger.exception("Signal failed: %s", exc)
            if once:
                return
            time.sleep(self.config.poll_seconds())


def record_cursor() -> None:
    point = Win32Input.get_cursor_pos()
    print(f"{point.x},{point.y}")


def set_nested(raw: dict[str, Any], dotted: str, value: Any) -> None:
    target = raw
    parts = dotted.split(".")
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    target[parts[-1]] = value


def record_point(config_path: Path, dotted: str) -> None:
    config = Config(config_path)
    point = Win32Input.get_cursor_pos()
    set_nested(config.raw, dotted, point.to_list())
    config.save()
    print(f"recorded {dotted} = {point.to_list()}")


def calibrate(config_path: Path) -> None:
    config = Config(config_path)
    print("Hover each HTS location, then press Enter. Type s then Enter to skip optional points.")

    def prompt(label: str, optional: bool = False) -> Point | None:
        answer = input(label + (" (Enter or s): " if optional else " (Enter): ")).strip().lower()
        if optional and answer == "s":
            return None
        point = Win32Input.get_cursor_pos()
        print(f"  {point.x},{point.y}")
        return point

    focus = prompt("1. Safe HTS focus/background point", optional=True)
    if focus:
        config.ui()["focus_click"] = focus.to_list()
    symbol = "SOXL"
    config.ui().setdefault("symbols", {}).setdefault(symbol, {})
    item = config.ui()["symbols"][symbol]
    item["panel_anchor"] = prompt("2. SOXL order panel anchor").to_list()
    item["qty_input"] = prompt("3. SOXL quantity input").to_list()
    item["buy_button"] = prompt("4. SOXL buy button").to_list()
    item["sell_button"] = prompt("5. SOXL sell button").to_list()
    confirm = prompt("6. Confirm popup button", optional=True)
    config.ui()["confirm_button"] = confirm.to_list() if confirm else None
    config.save()
    print(f"saved calibration: {config_path}")


def status(config: Config) -> None:
    print(f"config: {config.path}")
    print(f"dry_run: {config.dry_run()}")
    print(f"signal_file: {config.signal_file()}")
    print(f"state_file: {config.state_file()}")
    print(f"log_file: {config.log_file()}")
    print(f"allowed_symbols: {config.allowed_symbols()}")
    print(f"max_qty_per_order: {config.max_qty_per_order()}")
    if config.state_file().exists():
        print(config.state_file().read_text(encoding="utf-8"))


def test_order(config: Config, symbol: str, action: str, qty: int, live: bool) -> None:
    if live:
        config.raw.setdefault("safety", {})["dry_run"] = False
    signal = Signal(
        signal_id=f"manual-test-{int(time.time())}",
        symbol=symbol.upper(),
        action=action.upper(),
        qty=qty,
        created_at=datetime.now().astimezone(),
        note="manual test",
    )
    bot = Bot(config)
    bot.validate(signal)
    bot.execute(signal)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Meritz SOXL desktop order router")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("cursor")

    calibrate_cmd = sub.add_parser("calibrate")
    calibrate_cmd.add_argument("--config", default=str(DEFAULT_CONFIG))

    record = sub.add_parser("record-point")
    record.add_argument("--config", default=str(DEFAULT_CONFIG))
    record.add_argument("--path", required=True)

    run = sub.add_parser("run")
    run.add_argument("--config", default=str(DEFAULT_CONFIG))
    run.add_argument("--once", action="store_true")

    st = sub.add_parser("status")
    st.add_argument("--config", default=str(DEFAULT_CONFIG))

    test = sub.add_parser("test-order")
    test.add_argument("--config", default=str(DEFAULT_CONFIG))
    test.add_argument("--symbol", choices=["SOXL"], required=True)
    test.add_argument("--action", choices=["BUY", "SELL"], required=True)
    test.add_argument("--qty", type=int, default=1)
    test.add_argument("--live", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "cursor":
        record_cursor()
        return 0
    if args.command == "calibrate":
        calibrate(Path(args.config))
        return 0
    if args.command == "record-point":
        record_point(Path(args.config), args.path)
        return 0

    config = Config(Path(args.config))
    if args.command == "status":
        status(config)
        return 0
    if args.command == "run":
        Bot(config).run(once=args.once)
        return 0
    if args.command == "test-order":
        test_order(config, args.symbol, args.action, args.qty, args.live)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

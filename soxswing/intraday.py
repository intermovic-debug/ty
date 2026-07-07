from __future__ import annotations

import argparse
import csv
import json
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

from .account import resolve_account
from .data import fetch_intraday_bars
from .models import IntradayBar, IntradayPosition
from .universe import load_symbols


def _ema(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"Need at least {period} values")
    multiplier = 2 / (period + 1)
    current = sum(values[:period]) / period
    for value in values[period:]:
        current = (value * multiplier) + (current * (1 - multiplier))
    return current


def _rsi(values: list[float], period: int) -> float:
    if len(values) <= period:
        raise ValueError(f"Need more than {period} values")
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(values[-period - 1 : -1], values[-period:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))
    average_gain = sum(gains) / period
    average_loss = sum(losses) / period
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def _atr(bars: list[IntradayBar], period: int) -> float:
    if len(bars) <= period:
        raise ValueError(f"Need more than {period} bars")
    true_ranges: list[float] = []
    for previous, current in zip(bars[-period - 1 : -1], bars[-period:]):
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return sum(true_ranges) / period


def _load_state(path: Path, starting_cash: float) -> dict[str, Any]:
    if not path.exists():
        return {
            "cash": starting_cash,
            "realized_pnl": 0.0,
            "position": None,
            "trades_today": 0,
            "last_trade_at": None,
            "trade_day": datetime.now(tz=UTC).date().isoformat(),
        }
    with path.open("r", encoding="utf-8") as file:
        state = json.load(file)
    today = datetime.now(tz=UTC).date().isoformat()
    if state.get("trade_day") != today:
        state["trades_today"] = 0
        state["realized_pnl"] = 0.0
        state["trade_day"] = today
        state["last_trade_at"] = None
    return state


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with path.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)


def _position_from_state(state: dict[str, Any]) -> IntradayPosition | None:
    raw = state.get("position")
    if not raw:
        return None
    return IntradayPosition(
        symbol=raw["symbol"],
        quantity=int(raw["quantity"]),
        entry_price=float(raw["entry_price"]),
        stop_price=float(raw["stop_price"]),
        take_profit_price=float(raw["take_profit_price"]),
        trailing_stop_price=float(raw["trailing_stop_price"]),
        peak_price=float(raw["peak_price"]),
        opened_at=raw["opened_at"],
    )


def _symbol_score(bars: list[IntradayBar], cfg: dict[str, Any]) -> dict[str, Any]:
    closes = [bar.close for bar in bars]
    latest = bars[-1]
    fast = _ema(closes[-80:], int(cfg["fast_ema"]))
    slow = _ema(closes[-80:], int(cfg["slow_ema"]))
    rsi = _rsi(closes, int(cfg["rsi_period"]))
    atr = _atr(bars, int(cfg["atr_period"]))
    prev_close = closes[-5]
    momentum = (latest.close / prev_close) - 1

    score = 0
    reasons: list[str] = []
    if fast > slow:
        score += 2
        reasons.append("fast EMA > slow EMA")
    else:
        reasons.append("fast EMA <= slow EMA")
    if latest.close > fast:
        score += 1
        reasons.append("price above fast EMA")
    if 48 <= rsi <= 68:
        score += 1
        reasons.append(f"RSI balanced {rsi:.1f}")
    elif rsi > 74:
        score -= 1
        reasons.append(f"RSI overheated {rsi:.1f}")
    else:
        reasons.append(f"RSI weak/neutral {rsi:.1f}")
    if momentum > 0:
        score += 1
        reasons.append(f"short momentum {momentum:.2%}")

    return {
        "score": score,
        "price": latest.close,
        "atr": atr,
        "rsi": rsi,
        "fast_ema": fast,
        "slow_ema": slow,
        "momentum": momentum,
        "reasons": reasons,
        "timestamp": latest.timestamp,
    }


def _size_position(cash: float, price: float, atr: float, account: dict[str, Any], strategy: dict[str, Any]) -> int:
    max_notional = cash * float(account["max_position_pct"])
    stop_distance = max(price * float(strategy["stop_loss_pct"]), atr * 0.75)
    risk_budget = cash * float(account["risk_per_trade_pct"])
    by_notional = int(max_notional // price)
    by_risk = int(risk_budget // stop_distance)
    return max(0, min(by_notional, by_risk))


def _passes_entry_filter(best: dict[str, Any], second: dict[str, Any], strategy: dict[str, Any]) -> bool:
    if int(best["score"]) < int(strategy["entry_score"]):
        return False
    min_score_gap = int(strategy.get("min_score_gap", 1))
    if int(best["score"]) - int(second["score"]) < min_score_gap:
        return False
    if float(best["momentum"]) < float(strategy.get("min_momentum_pct", 0.0)):
        return False
    min_rsi = float(strategy.get("min_entry_rsi", 0.0))
    max_rsi = float(strategy.get("max_entry_rsi", 100.0))
    if not min_rsi <= float(best["rsi"]) <= max_rsi:
        return False
    return True


def _append_log(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "timestamp",
        "action",
        "symbol",
        "quantity",
        "price",
        "cash",
        "realized_pnl",
        "status",
        "reason",
    ]
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in fields})


def _fetch_scores(symbols: list[str], strategy: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    scores: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for symbol in symbols:
        try:
            bars = fetch_intraday_bars(symbol, strategy["interval"], int(strategy["range_days"]))
            scores[symbol] = _symbol_score(bars, strategy)
        except Exception as exc:
            errors[symbol] = str(exc)
    if not scores:
        raise RuntimeError(f"No market data loaded. Errors: {errors}")
    return scores, errors


def _score_rows(scores: dict[str, dict[str, Any]]) -> str:
    ranked = sorted(scores.items(), key=lambda item: item[1]["score"], reverse=True)
    return "\n".join(
        "<tr><td>{rank}</td><td>{symbol}</td><td>{score}</td><td>{price:.2f}</td><td>{rsi:.1f}</td><td>{mom:.2%}</td><td>{reason}</td></tr>".format(
            rank=index + 1,
            symbol=escape(symbol),
            score=int(score["score"]),
            price=float(score["price"]),
            rsi=float(score["rsi"]),
            mom=float(score["momentum"]),
            reason=escape("; ".join(score["reasons"])),
        )
        for index, (symbol, score) in enumerate(ranked)
    )


def _write_report(
    markdown_path: Path,
    html_path: Path,
    decision: dict[str, Any],
    scores: dict[str, dict[str, Any]],
    errors: dict[str, str],
    state: dict[str, Any],
    account_status: dict[str, Any],
) -> None:
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().isoformat(timespec="seconds")
    position = state.get("position")
    position_text = "none"
    if position:
        position_text = (
            f"{position['symbol']} {position['quantity']} shares, "
            f"entry {position['entry_price']:.2f}, stop {position['stop_price']:.2f}, "
            f"trail {position['trailing_stop_price']:.2f}, take {position['take_profit_price']:.2f}"
        )

    ranked = sorted(scores.items(), key=lambda item: item[1]["score"], reverse=True)
    lines = [
        "# Multi-Symbol Intraday Watch",
        "",
        f"- Generated at: {now}",
        f"- Action: {decision['action']}",
        f"- Status: {decision['status']}",
        f"- Reason: {decision['reason']}",
        f"- Account snapshot: {account_status['reason']}",
        f"- Cash: {state['cash']:.2f} USD",
        f"- Realized PnL today: {state['realized_pnl']:.2f} USD",
        f"- Trades today: {state['trades_today']}",
        f"- Position: {position_text}",
        "",
        "## Ranked Scores",
        "",
    ]
    for symbol, score in ranked:
        lines.append(
            f"- {symbol}: score={score['score']} price={score['price']:.2f} "
            f"RSI={score['rsi']:.1f} momentum={score['momentum']:.2%}"
        )
    if errors:
        lines.extend(["", "## Data Errors", ""])
        for symbol, message in sorted(errors.items()):
            lines.append(f"- {symbol}: {message}")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")

    rows = _score_rows(scores)
    error_rows = "".join(
        f"<tr><td>{escape(symbol)}</td><td>{escape(message)}</td></tr>"
        for symbol, message in sorted(errors.items())
    )
    errors_html = ""
    if error_rows:
        errors_html = f"""
  <section class="panel">
    <h2>Data Errors</h2>
    <table>
      <tr><th>Symbol</th><th>Error</th></tr>
      {error_rows}
    </table>
  </section>
"""

    html = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Multi-Symbol Intraday Watch</title>
  <style>
    body {{ margin: 0; background: #f7f8fa; color: #202124; font-family: Segoe UI, Malgun Gothic, sans-serif; }}
    main {{ max-width: 1040px; margin: 0 auto; padding: 30px 20px 48px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    .muted {{ color: #5f6368; }}
    .panel {{ background: #fff; border: 1px solid #dfe3e8; border-radius: 8px; padding: 18px; margin-top: 16px; }}
    .action {{ border-left: 5px solid #2f6fed; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 10px 8px; border-bottom: 1px solid #e8eaed; text-align: left; vertical-align: top; }}
    th {{ color: #5f6368; }}
  </style>
</head>
<body>
<main>
  <h1>Multi-Symbol Intraday Watch</h1>
  <div class="muted">Generated at: {escape(now)}</div>
  <section class="panel action">
    <h2>Current Action</h2>
    <p><strong>{escape(decision['action'])}</strong> / {escape(decision['status'])}</p>
    <p>{escape(decision['reason'])}</p>
  </section>
  <section class="panel">
    <h2>Account And Position</h2>
    <table>
      <tr><td>Cash</td><td>{state['cash']:.2f} USD</td></tr>
      <tr><td>Account snapshot</td><td>{escape(account_status['reason'])}</td></tr>
      <tr><td>Realized PnL today</td><td>{state['realized_pnl']:.2f} USD</td></tr>
      <tr><td>Trades today</td><td>{state['trades_today']}</td></tr>
      <tr><td>Position</td><td>{escape(position_text)}</td></tr>
    </table>
  </section>
  <section class="panel">
    <h2>Ranked Scores</h2>
    <table>
      <tr><th>Rank</th><th>Symbol</th><th>Score</th><th>Price</th><th>RSI</th><th>Momentum</th><th>Reason</th></tr>
      {rows}
    </table>
  </section>
{errors_html}
</main>
</body>
</html>
"""
    html_path.write_text(html, encoding="utf-8-sig")


def _notify(runtime: dict[str, Any], decision: dict[str, Any]) -> None:
    if not runtime.get("notify_on_action", False):
        return
    if decision["action"] not in {"paper_buy", "paper_sell", "halt"}:
        return

    script = Path(runtime.get("notification_script", "notify_signal.ps1"))
    report = Path(runtime["report_html_path"]).resolve()
    title = "Leveraged ETF signal"
    message = f"{decision['action']} / {decision['reason']}"

    if not script.exists():
        return

    subprocess.Popen(
        [
            "powershell.exe",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script.resolve()),
            "-Title",
            title,
            "-Message",
            message,
            "-ReportPath",
            str(report),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _rank_scores(scores: dict[str, dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    return sorted(scores.items(), key=lambda item: item[1]["score"], reverse=True)


def run(config_path: Path) -> str:
    with config_path.open("r", encoding="utf-8") as file:
        config = json.load(file)
    strategy = config["strategy"]
    runtime = config["runtime"]
    account, account_status = resolve_account(config["account"], runtime, config_path.parent)
    symbols = load_symbols(strategy, config_path.parent)
    if not symbols:
        raise ValueError("No symbols configured")

    state_path = Path(runtime["state_path"])
    state = _load_state(state_path, float(account["starting_cash"]))
    if (
        runtime.get("sync_cash_from_snapshot", True)
        and account_status["ok"]
        and not state.get("position")
    ):
        state["cash"] = round(float(account["starting_cash"]), 2)
    position = _position_from_state(state)
    scores, errors = _fetch_scores(symbols, strategy)

    now = datetime.now(tz=UTC)
    decision: dict[str, Any] = {"action": "watch", "status": "no_order", "reason": "No setup"}
    latest_data_at = max(score["timestamp"] for score in scores.values())
    max_daily_loss = -float(account["starting_cash"]) * float(account["max_daily_loss_pct"])

    if runtime.get("require_fresh_account_snapshot", False) and not account_status["ok"]:
        decision = {
            "action": "watch",
            "status": "account_snapshot_stale",
            "reason": account_status["reason"],
        }
    elif now - latest_data_at > timedelta(minutes=int(strategy.get("stale_after_minutes", 45))):
        decision = {
            "action": "watch",
            "status": "stale_data",
            "reason": f"Latest 15-minute bar is stale: {latest_data_at.isoformat()}",
        }
    elif float(state["realized_pnl"]) <= max_daily_loss:
        decision = {"action": "halt", "status": "daily_loss_limit", "reason": "Daily loss limit reached"}
    elif position:
        if position.symbol not in scores:
            decision = {
                "action": "hold",
                "status": "missing_position_data",
                "reason": f"Cannot update {position.symbol}; latest data was not available.",
            }
        else:
            latest_price = scores[position.symbol]["price"]
            peak = max(position.peak_price, latest_price)
            trailing = max(position.trailing_stop_price, peak * (1 - float(strategy["trailing_stop_pct"])))
            exit_reason = None
            if latest_price <= position.stop_price:
                exit_reason = "stop loss"
            elif latest_price <= trailing:
                exit_reason = "trailing stop"
            elif latest_price >= position.take_profit_price:
                exit_reason = "take profit"

            if exit_reason:
                proceeds = position.quantity * latest_price
                pnl = (latest_price - position.entry_price) * position.quantity
                state["cash"] = round(float(state["cash"]) + proceeds, 2)
                state["realized_pnl"] = round(float(state["realized_pnl"]) + pnl, 2)
                state["position"] = None
                state["trades_today"] = int(state["trades_today"]) + 1
                state["last_trade_at"] = now.isoformat()
                decision = {
                    "action": "paper_sell",
                    "status": "paper_filled",
                    "reason": f"{position.symbol} {position.quantity} shares closed by {exit_reason}, PnL={pnl:.2f}",
                    "symbol": position.symbol,
                    "quantity": position.quantity,
                    "price": round(latest_price, 2),
                }
            else:
                updated = IntradayPosition(
                    symbol=position.symbol,
                    quantity=position.quantity,
                    entry_price=position.entry_price,
                    stop_price=position.stop_price,
                    take_profit_price=position.take_profit_price,
                    trailing_stop_price=round(trailing, 2),
                    peak_price=round(peak, 2),
                    opened_at=position.opened_at,
                )
                state["position"] = asdict(updated)
                decision = {"action": "hold", "status": "holding", "reason": f"Holding {position.symbol}"}
    else:
        last_trade_at = state.get("last_trade_at")
        in_cooldown = False
        if last_trade_at:
            in_cooldown = now - datetime.fromisoformat(last_trade_at) < timedelta(minutes=int(strategy["cooldown_minutes"]))

        if int(state["trades_today"]) >= int(strategy["max_trades_per_day"]):
            decision = {"action": "watch", "status": "max_trades_reached", "reason": "Max trades per day reached"}
        elif in_cooldown:
            decision = {"action": "watch", "status": "cooldown", "reason": "Cooling down after the last trade"}
        else:
            ranked = _rank_scores(scores)
            best_symbol, best = ranked[0]
            second_symbol, second = ranked[1] if len(ranked) > 1 else ("-", {"score": -99})
            if _passes_entry_filter(best, second, strategy):
                qty = _size_position(float(state["cash"]), best["price"], best["atr"], account, strategy)
                if qty > 0:
                    cost = qty * best["price"]
                    state["cash"] = round(float(state["cash"]) - cost, 2)
                    stop = best["price"] * (1 - float(strategy["stop_loss_pct"]))
                    take_profit = best["price"] * (1 + float(strategy["take_profit_pct"]))
                    trailing = best["price"] * (1 - float(strategy["trailing_stop_pct"]))
                    state["position"] = asdict(
                        IntradayPosition(
                            symbol=best_symbol,
                            quantity=qty,
                            entry_price=round(best["price"], 2),
                            stop_price=round(stop, 2),
                            take_profit_price=round(take_profit, 2),
                            trailing_stop_price=round(trailing, 2),
                            peak_price=round(best["price"], 2),
                            opened_at=now.isoformat(),
                        )
                    )
                    state["trades_today"] = int(state["trades_today"]) + 1
                    state["last_trade_at"] = now.isoformat()
                    decision = {
                        "action": "paper_buy",
                        "status": "paper_filled",
                        "reason": f"{best_symbol} paper entry. Next candidate {second_symbol} score={second['score']}",
                        "symbol": best_symbol,
                        "quantity": qty,
                        "price": round(best["price"], 2),
                    }
                else:
                    decision = {"action": "watch", "status": "risk_blocked", "reason": "Position size is 0 under risk limits"}
            else:
                decision = {"action": "watch", "status": "no_edge", "reason": "No symbol has a clear enough edge"}

    _save_state(state_path, state)
    _append_log(
        Path(runtime["trade_log_path"]),
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "action": decision["action"],
            "symbol": decision.get("symbol", ""),
            "quantity": decision.get("quantity", ""),
            "price": decision.get("price", ""),
            "cash": state["cash"],
            "realized_pnl": state["realized_pnl"],
            "status": decision["status"],
            "reason": decision["reason"],
        },
    )
    _write_report(
        Path(runtime["report_markdown_path"]),
        Path(runtime["report_html_path"]),
        decision,
        scores,
        errors,
        state,
        account_status,
    )
    _notify(runtime, decision)
    return f"{decision['action']} {decision['status']}: {decision['reason']}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-symbol intraday paper watcher")
    parser.add_argument("--config", default="config.intraday.example.json")
    args = parser.parse_args()
    print(run(Path(args.config)))


if __name__ == "__main__":
    main()

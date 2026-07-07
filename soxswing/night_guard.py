from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    timestamp: datetime
    closes: list[float]


def _fetch_quote(symbol: str, interval: str, range_days: int) -> Quote:
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{symbol}?range={range_days}d&interval={interval}&includePrePost=false"
    )
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"Failed to download {symbol}: {exc}") from exc

    result = payload.get("chart", {}).get("result") or []
    if not result:
        raise RuntimeError(f"No chart result for {symbol}")

    chart = result[0]
    timestamps = chart.get("timestamp") or []
    quote = (chart.get("indicators", {}).get("quote") or [{}])[0]
    closes_raw = quote.get("close") or []
    pairs: list[tuple[datetime, float]] = []
    for timestamp, close in zip(timestamps, closes_raw):
        if close is None:
            continue
        pairs.append((datetime.fromtimestamp(timestamp, tz=UTC), float(close)))
    if not pairs:
        raise RuntimeError(f"No valid close values for {symbol}")

    latest_time, latest_price = pairs[-1]
    return Quote(
        symbol=symbol,
        price=latest_price,
        timestamp=latest_time,
        closes=[price for _, price in pairs],
    )


def _ema(values: list[float], period: int) -> float:
    if len(values) < period:
        return values[-1]
    multiplier = 2 / (period + 1)
    current = sum(values[:period]) / period
    for value in values[period:]:
        current = (value * multiplier) + (current * (1 - multiplier))
    return current


def _trend(quote: Quote) -> dict[str, Any]:
    closes = quote.closes
    fast = _ema(closes[-30:], 5)
    slow = _ema(closes[-60:], 20)
    momentum_5 = (closes[-1] / closes[-6] - 1) if len(closes) >= 6 else 0.0
    momentum_15 = (closes[-1] / closes[-16] - 1) if len(closes) >= 16 else 0.0

    if closes[-1] > fast > slow and momentum_5 > 0:
        label = "up"
    elif closes[-1] < fast < slow and momentum_5 < 0:
        label = "down"
    else:
        label = "mixed"

    return {
        "label": label,
        "fast_ema": fast,
        "slow_ema": slow,
        "momentum_5": momentum_5,
        "momentum_15": momentum_15,
    }


def _money(value: float) -> str:
    return f"{value:,.4f}"


def _position_plan(position: dict[str, Any], quote: Quote, trend: dict[str, Any], warn_buffer_pct: float) -> dict[str, Any]:
    symbol = str(position["symbol"]).upper()
    quantity = int(position["quantity"])
    average = float(position["average_price"])
    target = float(position["target_price"])
    stop = float(position["stop_price"])
    price = quote.price
    unrealized = (price - average) * quantity
    unrealized_pct = (price / average - 1) if average else 0.0
    target_distance = target - price
    stop_distance = price - stop

    if price >= target:
        status = "target_hit"
        action = "목표가 도달. HTS 자동매도 감시가 실행됐는지 확인."
    elif price <= stop:
        status = "stop_hit"
        action = "손실제한가 도달. HTS 자동매도 감시가 실행됐는지 확인."
    elif 0 <= target_distance <= price * warn_buffer_pct:
        status = "near_target"
        action = "익절가 근처. 감시 등록 상태와 체결 여부 확인."
    elif 0 <= stop_distance <= price * warn_buffer_pct:
        status = "near_stop"
        action = "손절가 근처. 추가매수 금지, 감시 등록 상태 확인."
    else:
        status = "watch"
        action = "감시 유지."

    return {
        "symbol": symbol,
        "quantity": quantity,
        "average_price": average,
        "price": price,
        "target_price": target,
        "stop_price": stop,
        "unrealized": unrealized,
        "unrealized_pct": unrealized_pct,
        "target_distance": target_distance,
        "stop_distance": stop_distance,
        "trend": trend,
        "status": status,
        "action": action,
    }


def _write_reports(paths: dict[str, Path], plans: list[dict[str, Any]], errors: dict[str, str], generated_at: str) -> None:
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Night Guard",
        "",
        f"- Generated at: {generated_at}",
        "- Mode: read-only monitor and Meritz value ticket",
        "- Live order entry/submission: disabled",
        "",
        "## Positions",
        "",
    ]
    for plan in plans:
        trend = plan["trend"]
        lines.extend(
            [
                f"### {plan['symbol']}",
                "",
                f"- Quantity: {plan['quantity']}",
                f"- Average: {_money(plan['average_price'])}",
                f"- Current: {_money(plan['price'])}",
                f"- Unrealized PnL: {plan['unrealized']:.2f} USD ({plan['unrealized_pct']:.2%})",
                f"- Trend: {trend['label']} / 5m {trend['momentum_5']:.2%} / 15m {trend['momentum_15']:.2%}",
                f"- Target: {_money(plan['target_price'])}",
                f"- Stop: {_money(plan['stop_price'])}",
                f"- Status: {plan['status']}",
                f"- Action: {plan['action']}",
                "",
            ]
        )

    if errors:
        lines.extend(["## Data Errors", ""])
        for symbol, message in sorted(errors.items()):
            lines.append(f"- {symbol}: {message}")
        lines.append("")

    lines.extend(
        [
            "## Meritz 6106 Values",
            "",
            "Use these in `[6106] 해외주식 자동감시 주문` -> `자동매도 조건설정`.",
            "",
        ]
    )
    for plan in plans:
        lines.extend(
            [
                f"### {plan['symbol']} 자동매도",
                "",
                "```text",
                "기준가: 평균단가",
                "이익실현: 체크",
                f"이익실현 입력값: {_money(plan['target_price'])}",
                "이익보존: 체크 안 함",
                "손실제한: 체크",
                f"손실제한 입력값: {_money(plan['stop_price'])}",
                "주문유형: 보통(지정가)",
                "주문가격: 현재가",
                "틱: 0",
                f"주문수량: {plan['quantity']}주 또는 매도가능수량 100%",
                "감시기간: 5일",
                "```",
                "",
            ]
        )
    paths["markdown"].write_text("\n".join(lines), encoding="utf-8-sig")

    ticket_lines = [
        "# Meritz 6106 Night Ticket",
        "",
        f"- Generated at: {generated_at}",
        "",
    ]
    for plan in plans:
        ticket_lines.extend(
            [
                f"## {plan['symbol']}",
                "",
                f"- 현재가: {_money(plan['price'])}",
                f"- 평단: {_money(plan['average_price'])}",
                f"- 수량: {plan['quantity']}",
                f"- 이익실현 입력값: {_money(plan['target_price'])}",
                f"- 손실제한 입력값: {_money(plan['stop_price'])}",
                f"- 상태: {plan['status']}",
                "",
            ]
        )
    paths["ticket"].write_text("\n".join(ticket_lines), encoding="utf-8-sig")

    rows = "\n".join(
        """
        <tr>
          <td>{symbol}</td>
          <td>{qty}</td>
          <td>{avg}</td>
          <td>{price}</td>
          <td>{pnl}</td>
          <td>{target}</td>
          <td>{stop}</td>
          <td>{trend}</td>
          <td>{status}</td>
        </tr>
        """.format(
            symbol=escape(plan["symbol"]),
            qty=plan["quantity"],
            avg=escape(_money(plan["average_price"])),
            price=escape(_money(plan["price"])),
            pnl=escape(f"{plan['unrealized']:.2f} USD / {plan['unrealized_pct']:.2%}"),
            target=escape(_money(plan["target_price"])),
            stop=escape(_money(plan["stop_price"])),
            trend=escape(plan["trend"]["label"]),
            status=escape(plan["status"]),
        )
        for plan in plans
    )
    html = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Night Guard</title>
  <style>
    body {{ margin: 0; background: #f7f8fa; color: #202124; font-family: Segoe UI, Malgun Gothic, sans-serif; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px 20px 48px; }}
    h1 {{ margin: 0 0 6px; font-size: 28px; }}
    .muted {{ color: #5f6368; }}
    .panel {{ background: #fff; border: 1px solid #dfe3e8; border-radius: 8px; padding: 16px; margin-top: 16px; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 9px 8px; border-bottom: 1px solid #e8eaed; text-align: left; }}
    th {{ color: #5f6368; }}
    code {{ font-size: 15px; }}
  </style>
</head>
<body>
<main>
  <h1>Night Guard</h1>
  <div class="muted">Generated at: {escape(generated_at)}</div>
  <section class="panel">
    <table>
      <tr><th>Symbol</th><th>Qty</th><th>Avg</th><th>Current</th><th>PnL</th><th>Target</th><th>Stop</th><th>Trend</th><th>Status</th></tr>
      {rows}
    </table>
  </section>
  <section class="panel">
    <h2>Meritz 6106</h2>
    <p>Use the generated Markdown ticket for exact values. This monitor does not enter or submit orders.</p>
  </section>
</main>
</body>
</html>
"""
    paths["html"].write_text(html, encoding="utf-8-sig")


def _notify(config: dict[str, Any], plans: list[dict[str, Any]]) -> None:
    runtime = config.get("runtime", {})
    if not runtime.get("notify_on_alert", True):
        return
    alert_plans = [plan for plan in plans if plan["status"] in {"target_hit", "stop_hit", "near_target", "near_stop"}]
    if not alert_plans:
        return
    script = Path(runtime.get("notification_script", "notify_signal.ps1"))
    if not script.exists():
        return
    message = "; ".join(f"{plan['symbol']} {plan['status']} {plan['price']:.2f}" for plan in alert_plans)
    subprocess.Popen(
        [
            "powershell.exe",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script.resolve()),
            "-Title",
            "Night Guard",
            "-Message",
            message,
            "-ReportPath",
            str(Path(runtime.get("report_html_path", "reports/night_guard.html")).resolve()),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def run_once(config_path: Path) -> str:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    quote_cfg = config.get("quotes", {})
    interval = str(quote_cfg.get("interval", "1m"))
    range_days = int(quote_cfg.get("range_days", 1))
    stale_after = int(quote_cfg.get("stale_after_minutes", 20))
    warn_buffer_pct = float(config.get("guard", {}).get("warn_near_exit_pct", 0.003))

    plans: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for position in config.get("positions", []):
        if int(position.get("quantity", 0)) <= 0:
            continue
        symbol = str(position["symbol"]).upper()
        try:
            quote = _fetch_quote(symbol, interval, range_days)
            if datetime.now(tz=UTC) - quote.timestamp > timedelta(minutes=stale_after):
                errors[symbol] = f"stale quote: {quote.timestamp.isoformat()}"
                continue
            plans.append(_position_plan(position, quote, _trend(quote), warn_buffer_pct))
        except Exception as exc:
            errors[symbol] = str(exc)

    runtime = config.get("runtime", {})
    paths = {
        "markdown": Path(runtime.get("report_markdown_path", "reports/night_guard.md")),
        "html": Path(runtime.get("report_html_path", "reports/night_guard.html")),
        "ticket": Path(runtime.get("ticket_path", "reports/night_guard_ticket.md")),
    }
    generated_at = datetime.now().isoformat(timespec="seconds")
    _write_reports(paths, plans, errors, generated_at)
    _notify(config, plans)

    if plans:
        summary = ", ".join(f"{plan['symbol']} {plan['status']} {plan['price']:.2f}" for plan in plans)
    else:
        summary = "no positions checked"
    if errors:
        summary += f"; errors={len(errors)}"
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only overnight guard for existing leveraged ETF positions")
    parser.add_argument("--config", default="config.night_guard.example.json")
    parser.add_argument("--watch", action="store_true", help="Run until stopped")
    parser.add_argument("--interval-seconds", type=int, default=60)
    args = parser.parse_args()

    config_path = Path(args.config)
    if args.watch:
        while True:
            print(run_once(config_path), flush=True)
            time.sleep(max(10, args.interval_seconds))
    else:
        print(run_once(config_path))


if __name__ == "__main__":
    main()

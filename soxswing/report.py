from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path

from .models import Bar, Order, Position, Signal


def _money(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.2f}"


def _manual_action(signal: Signal, order: Order | None, position: Position | None) -> str:
    if signal.action == "enter" and order:
        return (
            f"Flat 기준: iMeritz에서 {order.symbol} 매수 후보를 확인하세요. "
            f"수량 {order.quantity}주, 기준가 {_money(order.price)}, "
            f"손절 기준 {_money(order.stop_price)}, 익절 기준 {_money(order.take_profit_price)}."
        )
    if signal.action == "exit" and order:
        return f"보유 기준: iMeritz에서 {order.symbol} 매도/청산 후보를 확인하세요. 수량 {order.quantity}주."
    if signal.action == "hold" and position:
        return (
            f"보유 기준: {position.symbol} {position.quantity}주 유지 후보입니다. "
            f"손절 {_money(position.stop_price)}, 익절 {_money(position.take_profit_price)}."
        )
    return "신규 주문 후보 없음. iMeritz에서는 관망 또는 기존 보유분만 점검하세요."


def write_reports(
    markdown_path: Path,
    html_path: Path,
    signal: Signal,
    order: Order | None,
    status: str,
    cash: float,
    position: Position | None,
    bars: dict[str, list[Bar]],
) -> None:
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.parent.mkdir(parents=True, exist_ok=True)

    latest_dates = {symbol: symbol_bars[-1].date.isoformat() for symbol, symbol_bars in bars.items()}
    latest_prices = {symbol: symbol_bars[-1].close for symbol, symbol_bars in bars.items()}
    position_text = (
        f"{position.symbol} {position.quantity}주 / 진입가 {_money(position.entry_price)}"
        if position
        else "없음 또는 봇이 모름"
    )
    action = _manual_action(signal, order, position)
    generated_at = datetime.now().isoformat(timespec="seconds")

    lines = [
        "# SOXL/SOXS Daily Check",
        "",
        f"- 생성 시각: {generated_at}",
        f"- 실행 상태: {status}",
        f"- 봇 기준 현금: {_money(cash)}",
        f"- 봇 기준 포지션: {position_text}",
        "",
        "## 오늘 액션",
        "",
        action,
        "",
        "## 신호",
        "",
        f"- 액션: {signal.action}",
        f"- 종목: {signal.symbol or '-'}",
        f"- 점수: {signal.score}",
        f"- 기준가: {_money(signal.price)}",
        f"- 이유: {signal.reason}",
        "",
        "## 가격 데이터",
        "",
    ]

    for symbol in sorted(latest_dates):
        lines.append(f"- {symbol}: {latest_dates[symbol]} 종가 {_money(latest_prices[symbol])}")

    lines.extend(
        [
            "",
            "## 안전 원칙",
            "",
            "- 이 리포트는 자동 실주문을 하지 않습니다.",
            "- iMeritz 주문 전 계좌, 종목, 수량, 가격, 환율, 프리/애프터장 여부를 직접 확인하세요.",
            "- 실제 보유 포지션이 봇 상태와 다르면 실제 계좌를 우선하세요.",
        ]
    )

    markdown = "\n".join(lines) + "\n"
    markdown_path.write_text(markdown, encoding="utf-8-sig")

    price_rows = "\n".join(
        "<tr><td>{symbol}</td><td>{date}</td><td>{price}</td></tr>".format(
            symbol=escape(symbol),
            date=escape(latest_dates[symbol]),
            price=escape(_money(latest_prices[symbol])),
        )
        for symbol in sorted(latest_dates)
    )
    order_rows = ""
    if order:
        order_rows = """
        <tr><td>종목</td><td>{symbol}</td></tr>
        <tr><td>수량</td><td>{quantity}</td></tr>
        <tr><td>기준가</td><td>{price}</td></tr>
        <tr><td>손절 기준</td><td>{stop}</td></tr>
        <tr><td>익절 기준</td><td>{take_profit}</td></tr>
        """.format(
            symbol=escape(order.symbol),
            quantity=order.quantity,
            price=escape(_money(order.price)),
            stop=escape(_money(order.stop_price)),
            take_profit=escape(_money(order.take_profit_price)),
        )
    else:
        order_rows = "<tr><td>주문 후보</td><td>없음</td></tr>"

    html = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>SOXL/SOXS Daily Check</title>
  <style>
    body {{ font-family: Segoe UI, Malgun Gothic, sans-serif; margin: 0; color: #202124; background: #f6f7f9; }}
    main {{ max-width: 920px; margin: 0 auto; padding: 32px 20px 48px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    h2 {{ margin: 24px 0 10px; font-size: 18px; }}
    .muted {{ color: #5f6368; }}
    .panel {{ background: #fff; border: 1px solid #dfe3e8; border-radius: 8px; padding: 18px; margin-top: 16px; }}
    .action {{ border-left: 5px solid #2f6fed; }}
    table {{ border-collapse: collapse; width: 100%; background: #fff; }}
    td, th {{ border-bottom: 1px solid #e8eaed; padding: 10px 8px; text-align: left; }}
    th {{ color: #5f6368; font-weight: 600; }}
    ul {{ margin: 8px 0 0; padding-left: 20px; }}
  </style>
</head>
<body>
<main>
  <h1>SOXL/SOXS Daily Check</h1>
  <div class="muted">생성 시각: {escape(generated_at)} · 실행 상태: {escape(status)}</div>

  <section class="panel action">
    <h2>오늘 액션</h2>
    <p>{escape(action)}</p>
  </section>

  <section class="panel">
    <h2>신호</h2>
    <table>
      <tr><td>액션</td><td>{escape(signal.action)}</td></tr>
      <tr><td>종목</td><td>{escape(signal.symbol or "-")}</td></tr>
      <tr><td>점수</td><td>{signal.score}</td></tr>
      <tr><td>기준가</td><td>{escape(_money(signal.price))}</td></tr>
      <tr><td>이유</td><td>{escape(signal.reason)}</td></tr>
    </table>
  </section>

  <section class="panel">
    <h2>주문 후보</h2>
    <table>{order_rows}</table>
  </section>

  <section class="panel">
    <h2>가격 데이터</h2>
    <table>
      <tr><th>종목</th><th>데이터 기준일</th><th>종가</th></tr>
      {price_rows}
    </table>
  </section>

  <section class="panel">
    <h2>안전 원칙</h2>
    <ul>
      <li>이 리포트는 자동 실주문을 하지 않습니다.</li>
      <li>iMeritz 주문 전 계좌, 종목, 수량, 가격, 환율, 프리/애프터장 여부를 직접 확인하세요.</li>
      <li>실제 보유 포지션이 봇 상태와 다르면 실제 계좌를 우선하세요.</li>
    </ul>
  </section>
</main>
</body>
</html>
"""
    html_path.write_text(html, encoding="utf-8-sig")

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

from .account import resolve_account
from .data import fetch_intraday_bars
from .intraday import _passes_entry_filter, _size_position, _symbol_score
from .universe import load_symbols


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _ranked_score_rows(ranked_scores: list[tuple[str, dict[str, Any]]]) -> str:
    return "\n".join(
        "<tr><td>{rank}</td><td>{symbol}</td><td>{score}</td><td>{price}</td><td>{rsi:.1f}</td><td>{momentum:.2%}</td><td>{reason}</td></tr>".format(
            rank=index + 1,
            symbol=escape(symbol),
            score=int(score["score"]),
            price=escape(_money(float(score["price"]))),
            rsi=float(score["rsi"]),
            momentum=float(score["momentum"]),
            reason=escape("; ".join(score["reasons"])),
        )
        for index, (symbol, score) in enumerate(ranked_scores)
    )


def _write_plan(markdown_path: Path, html_path: Path, plan: dict[str, Any]) -> None:
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Multi-Symbol Sleep Plan",
        "",
        f"- Generated at: {plan['generated_at']}",
        f"- Status: {plan['status']}",
        f"- Reason: {plan['reason']}",
        f"- Account snapshot: {plan['account_status']['reason']}",
        "",
        "## Order Plan",
        "",
    ]

    if plan.get("order"):
        order = plan["order"]
        lines.extend(
            [
                f"- Candidate symbol: {order['symbol']}",
                f"- Reference entry: {_money(order['entry_price'])}",
                f"- Max quantity: {order['quantity']} shares",
                f"- Estimated notional: {_money(order['notional'])} USD",
                f"- Stop price: {_money(order['stop_price'])}",
                f"- Take-profit price: {_money(order['take_profit_price'])}",
                f"- Trailing reference: {_money(order['trailing_stop_price'])}",
            ]
        )
    else:
        lines.append("- No new entry candidate.")

    lines.extend(
        [
            "",
            "## Meritz Steps",
            "",
            "- Check `[6110] overseas stock balance/cash` first.",
            "- Use `[0600] overseas stock conditional order` or a supported auto-watch order screen.",
            "- Do not leave a naked market order before sleep.",
            "- If entering, pair the idea with stop-loss and take-profit protection.",
            "- Confirm account, symbol, quantity, price, and valid time before final submit.",
            "",
            "## Ranked Scores",
            "",
        ]
    )
    for symbol, score in plan["ranked_scores"]:
        lines.append(
            f"- {symbol}: score={score['score']}, price={_money(score['price'])}, "
            f"RSI={score['rsi']:.1f}, momentum={score['momentum']:.2%}"
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")

    order_html = "<p>No new entry candidate.</p>"
    if plan.get("order"):
        order = plan["order"]
        order_html = """
        <table>
          <tr><td>Candidate symbol</td><td>{symbol}</td></tr>
          <tr><td>Reference entry</td><td>{entry}</td></tr>
          <tr><td>Max quantity</td><td>{qty} shares</td></tr>
          <tr><td>Estimated notional</td><td>{notional} USD</td></tr>
          <tr><td>Stop price</td><td>{stop}</td></tr>
          <tr><td>Take-profit price</td><td>{take}</td></tr>
          <tr><td>Trailing reference</td><td>{trail}</td></tr>
        </table>
        """.format(
            symbol=escape(order["symbol"]),
            entry=escape(_money(order["entry_price"])),
            qty=order["quantity"],
            notional=escape(_money(order["notional"])),
            stop=escape(_money(order["stop_price"])),
            take=escape(_money(order["take_profit_price"])),
            trail=escape(_money(order["trailing_stop_price"])),
        )

    score_rows = _ranked_score_rows(plan["ranked_scores"])
    html = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Multi-Symbol Sleep Plan</title>
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
    li {{ margin: 6px 0; }}
  </style>
</head>
<body>
<main>
  <h1>Multi-Symbol Sleep Plan</h1>
  <div class="muted">Generated at: {escape(plan['generated_at'])}</div>
  <section class="panel action">
    <h2>Status</h2>
    <p><strong>{escape(plan['status'])}</strong></p>
    <p>{escape(plan['reason'])}</p>
    <p>{escape(plan['account_status']['reason'])}</p>
  </section>
  <section class="panel">
    <h2>Order Plan</h2>
    {order_html}
  </section>
  <section class="panel">
    <h2>Meritz Steps</h2>
    <ol>
      <li>Open [6110] overseas stock balance/cash and confirm cash, holdings, and open orders.</li>
      <li>Open [0600] overseas stock conditional order or a supported auto-watch order screen.</li>
      <li>Use the order ticket copy buttons for symbol, quantity, stop, and take-profit values.</li>
      <li>Before final submit, confirm account, symbol, quantity, price, and valid time.</li>
    </ol>
  </section>
  <section class="panel">
    <h2>Ranked Scores</h2>
    <table>
      <tr><th>Rank</th><th>Symbol</th><th>Score</th><th>Price</th><th>RSI</th><th>Momentum</th><th>Reason</th></tr>
      {score_rows}
    </table>
  </section>
</main>
</body>
</html>
"""
    html_path.write_text(html, encoding="utf-8-sig")


def _write_order_ticket(path: Path, plan: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    order = plan.get("order")
    if order:
        rows = [
            ("Screen", "[0600] overseas conditional order"),
            ("Account", "Select directly in iMeritz"),
            ("Account snapshot", plan["account_status"]["reason"]),
            ("Symbol", order["symbol"]),
            ("Order idea", "Buy only if condition is met"),
            ("Quantity", str(order["quantity"])),
            ("Reference entry", f"{order['entry_price']:.2f}"),
            ("Stop price", f"{order['stop_price']:.2f}"),
            ("Take-profit price", f"{order['take_profit_price']:.2f}"),
            ("Trailing reference", f"{order['trailing_stop_price']:.2f}"),
            ("Valid time", "US regular session"),
        ]
        status_text = "Copy-ready order candidate"
        warning = "Paste values into iMeritz, then confirm account, symbol, quantity, price, and valid time before final submit."
    else:
        rows = [
            ("Status", plan["status"]),
            ("Reason", plan["reason"]),
            ("Account snapshot", plan["account_status"]["reason"]),
            ("Default action", "Do not create a new conditional order"),
        ]
        status_text = "No new order candidate"
        warning = "No fresh candidate is available. The default action is to avoid a new order."

    table_rows = "\n".join(
        """
        <tr>
          <td>{label}</td>
          <td><code id="v{idx}">{value}</code></td>
          <td><button type="button" data-copy="v{idx}">Copy</button></td>
        </tr>
        """.format(idx=index, label=escape(label), value=escape(str(value)))
        for index, (label, value) in enumerate(rows)
    )

    html = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Meritz Order Ticket</title>
  <style>
    body {{ margin: 0; background: #f7f8fa; color: #202124; font-family: Segoe UI, Malgun Gothic, sans-serif; }}
    main {{ max-width: 880px; margin: 0 auto; padding: 30px 20px 48px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    .muted {{ color: #5f6368; }}
    .panel {{ background: #fff; border: 1px solid #dfe3e8; border-radius: 8px; padding: 18px; margin-top: 16px; }}
    .action {{ border-left: 5px solid #2f6fed; }}
    table {{ width: 100%; border-collapse: collapse; }}
    td {{ padding: 10px 8px; border-bottom: 1px solid #e8eaed; vertical-align: middle; }}
    td:first-child {{ width: 160px; color: #5f6368; font-weight: 600; }}
    code {{ font-size: 18px; color: #111827; }}
    button {{ min-width: 64px; padding: 8px 12px; border: 1px solid #c8ced7; border-radius: 6px; background: #fff; cursor: pointer; }}
    button:hover {{ background: #eef3ff; border-color: #9eb8ff; }}
    .warn {{ color: #9a3412; }}
  </style>
</head>
<body>
<main>
  <h1>Meritz Order Ticket</h1>
  <div class="muted">Generated at: {escape(plan['generated_at'])}</div>
  <section class="panel action">
    <h2>{escape(status_text)}</h2>
    <p>{escape(plan['reason'])}</p>
    <p class="warn">{escape(warning)}</p>
  </section>
  <section class="panel">
    <h2>Copy Values</h2>
    <table>{table_rows}</table>
  </section>
  <section class="panel">
    <h2>Use In iMeritz</h2>
    <ol>
      <li>Check [6110] balance/cash first.</li>
      <li>Open [0600] overseas conditional order.</li>
      <li>Copy values from this ticket into the matching fields.</li>
      <li>You press the final submit/confirm button only after reviewing everything.</li>
    </ol>
  </section>
</main>
<script>
  document.querySelectorAll('button[data-copy]').forEach(function(button) {{
    button.addEventListener('click', function() {{
      var id = button.getAttribute('data-copy');
      var text = document.getElementById(id).innerText;
      navigator.clipboard.writeText(text).then(function() {{
        var old = button.innerText;
        button.innerText = 'Copied';
        setTimeout(function() {{ button.innerText = old; }}, 900);
      }});
    }});
  }});
</script>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8-sig")


def run(config_path: Path) -> str:
    with config_path.open("r", encoding="utf-8") as file:
        config = json.load(file)

    strategy = config["strategy"]
    runtime = config["runtime"]
    account, account_status = resolve_account(config["account"], runtime, config_path.parent)
    symbols = load_symbols(strategy, config_path.parent)
    bars = {
        symbol: fetch_intraday_bars(symbol, strategy["interval"], int(strategy["range_days"]))
        for symbol in symbols
    }
    scores = {symbol: _symbol_score(symbol_bars, strategy) for symbol, symbol_bars in bars.items()}
    ranked_scores = sorted(scores.items(), key=lambda item: item[1]["score"], reverse=True)
    now = datetime.now(tz=UTC)
    latest_data_at = max(score["timestamp"] for score in scores.values())

    plan: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": "watch",
        "reason": "No new entry candidate.",
        "order": None,
        "scores": scores,
        "ranked_scores": ranked_scores,
        "account_status": account_status,
    }

    if runtime.get("require_fresh_account_snapshot", False) and not account_status["ok"]:
        plan["status"] = "account_snapshot_stale"
        plan["reason"] = account_status["reason"]
    elif now - latest_data_at > timedelta(minutes=int(strategy.get("stale_after_minutes", 45))):
        plan["status"] = "stale_data"
        plan["reason"] = f"Latest 15-minute bar is stale: {latest_data_at.isoformat()}"
    else:
        best_symbol, best = ranked_scores[0]
        second_symbol, second = ranked_scores[1] if len(ranked_scores) > 1 else ("-", {"score": -99})
        if _passes_entry_filter(best, second, strategy):
            quantity = _size_position(float(account["starting_cash"]), best["price"], best["atr"], account, strategy)
            if quantity > 0:
                entry = float(best["price"])
                plan["status"] = "entry_candidate"
                plan["reason"] = (
                    f"{best_symbol} is the strongest candidate. "
                    f"Second candidate {second_symbol} score={second['score']}. "
                    "Use only with stop-loss and take-profit protection."
                )
                plan["order"] = {
                    "symbol": best_symbol,
                    "entry_price": round(entry, 2),
                    "quantity": quantity,
                    "notional": round(entry * quantity, 2),
                    "stop_price": round(entry * (1 - float(strategy["stop_loss_pct"])), 2),
                    "take_profit_price": round(entry * (1 + float(strategy["take_profit_pct"])), 2),
                    "trailing_stop_price": round(entry * (1 - float(strategy["trailing_stop_pct"])), 2),
                }
            else:
                plan["status"] = "risk_blocked"
                plan["reason"] = "Position sizing returned 0 shares under current risk limits."
        else:
            plan["status"] = "no_edge"
            plan["reason"] = "No symbol has a clear enough edge."

    _write_plan(Path("reports/sleep_plan.md"), Path("reports/sleep_plan.html"), plan)
    _write_order_ticket(Path(runtime.get("order_ticket_path", "reports/order_ticket.html")), plan)
    return f"{plan['status']}: {plan['reason']}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a multi-symbol pre-sleep bracket-order plan")
    parser.add_argument("--config", default="config.intraday.example.json")
    args = parser.parse_args()
    print(run(Path(args.config)))


if __name__ == "__main__":
    main()

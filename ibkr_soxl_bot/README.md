# IBKR SOXL Bot

Safety-first scaffold for automating SOXL monitoring and limit orders through
Interactive Brokers TWS or IB Gateway.

This project is intentionally conservative:

- Paper Trading is the default.
- Real orders require both `trading.allow_live_orders=true` and the CLI `--live` flag.
- Live-account orders are blocked in this scaffold until the paper flow is verified.
- Market orders are not used. The bot only creates limit orders.
- The public example config contains no personal holdings.

## Why IBKR

IBKR's TWS API can automate trading strategies, request market data, and monitor
account or portfolio state in real time. It still requires TWS or IB Gateway to
be running and authenticated on the computer.

Useful official docs:

- https://interactivebrokers.github.io/tws-api/introduction.html
- https://interactivebrokers.github.io/tws-api/initial_setup.html
- https://interactivebrokers.github.io/tws-api/basic_orders.html
- https://www.interactivebrokers.com/campus/ibkr-api-page/twsapi-doc/

## IBKR Setup

In TWS:

1. Open `Edit -> Global Configuration -> API -> Settings`.
2. Enable `Enable ActiveX and Socket Clients`.
3. Use Paper Trading first. The usual default paper port is `7497`.
4. Keep `Read-Only API` enabled for the first connection test.
5. Disable `Read-Only API` only after quotes and account connection are verified.

IB Gateway can be used instead of TWS after the workflow is stable.

## Install

```powershell
cd C:\Users\Administrator\Documents\Codex\2026-05-28\new-chat\ibkr_soxl_bot
python -m pip install -r requirements.txt
Copy-Item .\config.example.json .\config.local.json
```

Edit `config.local.json` with private holdings and risk limits. Do not commit or
upload `config.local.json`.

## Commands

Show config:

```powershell
python .\ibkr_soxl_bot.py --config .\config.local.json status
```

Test connection:

```powershell
python .\ibkr_soxl_bot.py --config .\config.local.json connect-test
```

Get quote:

```powershell
python .\ibkr_soxl_bot.py --config .\config.local.json quote
```

Calculate the recovery plan at a manual price:

```powershell
python .\ibkr_soxl_bot.py --config .\config.local.json plan --price 188
```

Dry-run a limit order:

```powershell
python .\ibkr_soxl_bot.py --config .\config.local.json place-limit --action BUY --qty 1 --limit 180
python .\ibkr_soxl_bot.py --config .\config.local.json place-limit --action SELL --qty 1 --limit 220
```

Paper live order test:

```powershell
python .\ibkr_soxl_bot.py --config .\config.local.json place-limit --action BUY --qty 1 --limit 180 --live
```

## Current Safety Rule

Do not let this bot decide with unlimited freedom. Let it execute pre-approved
rules:

- Existing recovery position is separated from scalp trades.
- New scalp size stays small while the recovery position is large.
- If two scalp trades fail in a day, stop for the day.
- If price moves violently, stop adding size and only manage exits.

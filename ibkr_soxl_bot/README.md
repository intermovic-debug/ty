# IBKR SOXL Bot

IBKR TWS or IB Gateway API scaffold for safer SOXL automation.

Defaults:

- Paper Trading first.
- `dry-run` behavior by default: `allow_live_orders=false`.
- Limit orders only.
- SOXL only.
- Recovery position and scalp position are separated.

## IBKR setup

1. Login to TWS or IB Gateway Paper account.
2. In TWS: `Edit` -> `Global Configuration` -> `API` -> `Settings`.
3. Enable `ActiveX and Socket Clients`.
4. Paper port is usually `7497`; live port is usually `7496`.
5. Keep `Read-Only API` enabled for connection tests. Disable it only for Paper order tests.

## Install

```powershell
cd ibkr_soxl_bot
python -m pip install -r requirements.txt
Copy-Item .\config.example.json .\config.local.json
```

## Commands

```powershell
python .\ibkr_soxl_bot.py --config .\config.local.json status
python .\ibkr_soxl_bot.py --config .\config.local.json connect-test
python .\ibkr_soxl_bot.py --config .\config.local.json quote
python .\ibkr_soxl_bot.py --config .\config.local.json plan --price 188
```

Dry-run order:

```powershell
python .\ibkr_soxl_bot.py --config .\config.local.json place-limit --action BUY --qty 1 --limit 180
```

Live Paper order requires both `allow_live_orders=true` in local config and CLI `--live`.

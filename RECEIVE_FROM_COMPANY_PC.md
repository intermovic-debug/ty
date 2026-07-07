# Continue On Another PC

Use this when moving the project to a company or second Windows PC.

## Clone

```powershell
git clone https://github.com/intermovic-debug/ty.git
cd ty
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-ibkr.txt
```

## Start With Paper-Only Checks

```powershell
.\.venv\Scripts\python.exe -m soxswing.intraday --config .\config.intraday.cash_conservative_semis_tiered.json
.\.venv\Scripts\python.exe -m soxswing.intraday --config .\config.intraday.cash_optimized_semis_tiered.json
```

## If TWS Is Installed On That PC

```powershell
.\.venv\Scripts\python.exe -m soxswing.ibkr_runner --config .\config.ibkr.cash_start.example.json socket-test
.\.venv\Scripts\python.exe -m soxswing.ibkr_runner --config .\config.ibkr.cash_start.example.json connect-test
```

Keep the default config safe:

- `dry_run=true`
- `allow_order_create=false`
- `transmit_orders=false`

## Do Not Copy These Manually

- `account_snapshot*.json`
- `data/`
- `logs/`
- `reports/`
- screenshots
- `*.local.json`
<<<<<<< HEAD
=======

>>>>>>> 85253ad (Add IBKR cash-start strategy workspace)

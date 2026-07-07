# Work PC Resume - 2026-07-08

This repo is a sanitized handoff for continuing the SOXL/SOXS automation project from another Windows PC.

## Current Direction

- Old Meritz/SOXL recovery work is now secondary.
- Primary direction is IBKR cash-start after the user closes old SOXL elsewhere and funds IBKR.
- Keep all execution in paper/dry-run first.
- Do not create or transmit live IBKR orders until account snapshot, market data, paper order staging, cancel flow, logging, and emergency stop behavior are verified.

## What Changed Overnight

- Added realistic cash-start backtest variants for SOXL/SOXS.
- Added low-turnover and IBKR tiered-commission sweeps.
- Added a grid optimizer for realistic SOXL/SOXS cash-start settings.
- Added IBKR historical-data CSV fetcher for 1-year 15-minute bars.
- Added two paper-mode configs:
  - `config.intraday.cash_optimized_semis_tiered.json`
  - `config.intraday.cash_conservative_semis_tiered.json`

## Best Current Finding

The best candidate is still SOXL/SOXS only.

Recent 60-day realistic backtest with IBKR tiered-like commission assumption:

- Best strategy: SOXL/SOXS semiconductor pair only
- Return: about `+10.59%`
- Max drawdown: about `-1.08%`
- Trades: `103`
- Win rate: about `57.28%`

The conservative one-trade-per-day cluster was slightly lower return but calmer:

- Return cluster: about `+9.43%`
- Max drawdown: around `-0.99%`
- Trades: about `55`
- Win rate: about `67%`

Adding TQQQ/SQQQ made the recent realistic result worse, so keep the first live/paper phase to SOXL/SOXS only.

## Company PC Setup

```powershell
git clone https://github.com/intermovic-debug/ty.git
cd ty
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-ibkr.txt
```

If continuing without venv:

```powershell
python -m pip install -r requirements-ibkr.txt
```

## Home/IBKR PC Next Steps

After TWS is logged in and API socket is enabled:

```powershell
python -m soxswing.ibkr_runner --config .\config.ibkr.cash_start.example.json socket-test
python -m soxswing.ibkr_runner --config .\config.ibkr.cash_start.example.json connect-test
python -m soxswing.ibkr_runner --config .\config.ibkr.cash_start.example.json write-snapshot --path .\account_snapshot.ibkr.json
python -m soxswing.ibkr_historical --config .\config.ibkr.cash_start.example.json --symbols SOXL SOXS --duration "1 Y" --bar-size "15 mins" --out .\data\ibkr_historical
```

Run current paper signals:

```powershell
python -m soxswing.intraday --config .\config.intraday.cash_conservative_semis_tiered.json
python -m soxswing.intraday --config .\config.intraday.cash_optimized_semis_tiered.json
```

Run backtests:

```powershell
python -m soxswing.backtest_cash_realistic --config .\config.cash_realistic_sweep.ibkr_tiered_60d.json
python -m soxswing.optimize_cash_realistic --range-days 60 --minimum-commission 0.35 --commission-per-share 0.0035 --report .\reports\cash_realistic_optimizer_60d_tiered.md --output-config .\config.intraday.cash_optimized_semis_tiered.json
```

## Safety Notes

- `account_snapshot*.json`, `data/`, `logs/`, `reports/`, screenshots, and `*.local.json` are intentionally ignored.
- `config.ibkr.cash_start.example.json` keeps `dry_run=true`, `allow_order_create=false`, and `transmit_orders=false`.
- IBKR delayed quotes are blocked for actionable tickets by default.
- Keep `STOP_TRADING.txt` as emergency stop behavior for any future live-capable runner.
<<<<<<< HEAD
=======

>>>>>>> 85253ad (Add IBKR cash-start strategy workspace)

# Work PC IBKR 1Y Handoff - 2026-07-08

## Status

This handoff contains the company PC results for continuing the IBKR SOXL/SOXS automation work on another PC.

No live orders were created or transmitted. All work here was historical-data fetch, dry-run/paper-safe checks, and backtesting.

## Completed Results

### IBKR 1-year 15-minute historical data

Fetched from TWS/IBKR, RTH only on the company PC:

- `ibkr_runtime/historical/SOXL_1y_15mins.csv`
- `ibkr_runtime/historical/SOXS_1y_15mins.csv`
- `ibkr_runtime/historical/TQQQ_1y_15mins.csv`
- `ibkr_runtime/historical/SQQQ_1y_15mins.csv`

The raw CSV files are intentionally not required in GitHub because they can be re-fetched from IBKR/TWS and may be regenerated. The result reports and scripts are committed; if the local zip is available, it also contains the CSV cache.

Dataset window:

- First synchronized bar UTC: `2025-07-08T13:30:00+00:00`
- Last synchronized bar UTC: `2026-07-07T19:45:00+00:00`
- Synchronized bars: `6,502`

### Existing candidate re-test

Report:

- `ibkr_runtime/reports/backtest_cash_realistic_ibkr_1y.md`

Result ranking:

| Rank | Variant | Return | Max DD | Trades | Win Rate | Symbols |
| ---: | --- | ---: | ---: | ---: | ---: | --- |
| 1 | `semis_original_best_tiered` | `13.61%` | `-5.90%` | `457` | `42.23%` | SOXL, SOXS |
| 2 | `semis_windowed_tiered` | `8.17%` | `-7.06%` | `446` | `40.81%` | SOXL, SOXS |
| 3 | `multi_selective_tiered` | `0.91%` | `-2.73%` | `163` | `39.88%` | SOXL, SOXS, TQQQ, SQQQ |
| 4 | `semis_low_turnover_tiered` | `1.59%` | `-3.41%` | `214` | `41.59%` | SOXL, SOXS |
| 5 | `semis_trend_rider_tiered` | `1.07%` | `-4.58%` | `214` | `38.79%` | SOXL, SOXS |

Interpretation:

- SOXL/SOXS only still beats adding TQQQ/SQQQ in this run.
- The old 60-day result looked much cleaner than the 1-year result; do not assume the 60-day edge is stable.

### 1-year optimizer

Report:

- `ibkr_runtime/reports/cash_realistic_optimizer_ibkr_1y.md`

Best risk-adjusted candidate:

- Return: `17.18%`
- Max drawdown: `-3.12%`
- Trades: `238`
- Win rate: `47.06%`
- PnL on $3,000 simulation: `$515.51`
- Fees estimate: `$169.85`

Best config patch:

```json
{
  "account": {
    "max_position_pct": 0.3,
    "risk_per_trade_pct": 0.003
  },
  "strategy": {
    "entry_score": 4,
    "min_score_gap": 1,
    "min_momentum_pct": 0.004,
    "min_entry_rsi": 42,
    "max_entry_rsi": 76,
    "stop_loss_pct": 0.005,
    "take_profit_pct": 0.02,
    "trailing_stop_pct": 0.006,
    "cooldown_minutes": 60,
    "max_trades_per_day": 1,
    "active_groups": ["Semiconductors 3x"]
  }
}
```

Full optimized config written locally:

- `ibkr_runtime/config.intraday.cash_optimized_semis_ibkr_1y.json`

## New Scripts

Upload these to the repo or keep them in a `handoff/2026-07-08-ibkr-1y/` folder:

- `run_ibkr_csv_cash_backtest.py`
- `run_ibkr_csv_cash_optimizer.py`

The scripts intentionally convert IBKR CSV timestamps from local/KST timestamps to UTC before applying strategy time filters. This avoids distorted trade-window results.

## Suggested Home PC Resume Steps

From the existing repo folder on the home PC:

```powershell
git pull
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-ibkr.txt
.\.venv\Scripts\python.exe -m pip install tzdata
```

Fetch or copy the historical CSV files under:

```text
ibkr_runtime/historical/
```

Then run:

```powershell
.\.venv\Scripts\python.exe .\run_ibkr_csv_cash_backtest.py
.\.venv\Scripts\python.exe .\run_ibkr_csv_cash_optimizer.py
```

If CSV files are missing, re-fetch them from TWS/IBKR first:

```powershell
.\.venv\Scripts\python.exe -m soxswing.ibkr_historical --config .\config.ibkr.cash_start.example.json --symbols SOXL SOXS TQQQ SQQQ --duration "1 Y" --bar-size "15 mins" --out .\ibkr_runtime\historical
```

## Current Recommendation For Strategy Direction

Use paper mode first, not live:

- Start with SOXL/SOXS only.
- Do not add TQQQ/SQQQ yet based on this 1-year result.
- Prefer the optimized 1-trade-per-day candidate over the older 2-trades-per-day candidate.
- Keep live-order flags disabled until quote freshness, paper order staging, cancellation, logs, and emergency stop are verified.

## GitHub Upload Contents

These files should be enough for the home PC to resume:

- `WORK_PC_IBKR_1Y_HANDOFF_2026-07-08.md`
- `run_ibkr_csv_cash_backtest.py`
- `run_ibkr_csv_cash_optimizer.py`
- `config.intraday.cash_optimized_semis_ibkr_1y.json`
- `reports/backtest_cash_realistic_ibkr_1y.md`
- `reports/cash_realistic_optimizer_ibkr_1y.md`

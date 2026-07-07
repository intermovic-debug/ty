# Cash-Start IBKR Plan

Use this path after the old SOXL position is closed outside this system and new
cash is available at IBKR.

## What Changes

- No recovery ladder.
- No inherited SOXL average price.
- Strategy starts flat with cash.
- New trades must be small until paper tests are verified.
- Existing config for recovery testing stays separate.

## Baseline Backtests

The cash-only forward paper config is:

```powershell
python -m soxswing.backtest_intraday --config .\config.backtest.optimized.forward_paper.60d.json
python -m soxswing.backtest_daily --config .\config.backtest.daily.365d.json
python -m soxswing.backtest_daily --config .\config.backtest.daily.730d.json
```

Yahoo intraday data does not provide one full year of 15-minute bars through the
current endpoint. For a one-year intraday test, collect IBKR historical bars into
local CSV first, then run the backtester from that cache.

## Paper Startup

```powershell
python -m soxswing.ibkr_runner --config .\config.ibkr.cash_start.example.json status
python -m soxswing.ibkr_runner --config .\config.ibkr.cash_start.example.json connect-test
python -m soxswing.ibkr_runner --config .\config.ibkr.cash_start.example.json account
python -m soxswing.ibkr_runner --config .\config.ibkr.cash_start.example.json positions
python -m soxswing.ibkr_runner --config .\config.ibkr.cash_start.example.json plan
```

Default behavior:

- Generates reports/tickets only.
- Blocks delayed IBKR quotes.
- Blocks order creation with `dry_run=true`.
- Blocks transmission with `transmit_orders=false`.

## Before Live

1. Confirm deposited cash and settled USD buying power.
2. Confirm real-time data status for SOXL/SOXS or use another verified data feed.
3. Run at least one paper session with `Transmit=false`.
4. Run emergency stop test by creating `STOP_TRADING.txt`.
5. Keep max order size small for the first live test.

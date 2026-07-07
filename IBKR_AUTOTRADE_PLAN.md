# IBKR Semi-Auto Trading Plan

This project now treats IBKR as the execution layer and keeps the strategy,
backtest, reports, and safety checks in Python.

## Phases

1. Backtest and tune the strategy.
2. Run `plan` and `watch` in dry-run mode.
3. Connect to TWS paper trading on port `7497`.
4. Create a paper order with `Transmit=false` and verify it appears in TWS.
5. Cancel and reconcile open orders.
6. Only after multiple paper sessions, consider live settings with tiny size.

## Safety Locks

Default settings in `config.ibkr.example.json`:

- `dry_run=true`
- `paper_only=true`
- `allow_order_create=false`
- `transmit_orders=false`
- `max_daily_orders=3`
- `max_order_qty=8`
- `allowed_symbols=["SOXL", "SOXS"]`
- emergency stop file: `STOP_TRADING.txt`

With these defaults the program can generate plans and tickets, but it cannot
create or transmit real orders.

## Commands

```powershell
python -m soxswing.ibkr_runner --config config.ibkr.example.json status
python -m soxswing.ibkr_runner --config config.ibkr.example.json socket-test
python -m soxswing.ibkr_runner --config config.ibkr.example.json plan --mock-price 206.07
python -m soxswing.ibkr_runner --config config.ibkr.example.json watch --iterations 3
```

After installing `ib-insync` and enabling the TWS API:

```powershell
python -m pip install -r requirements-ibkr.txt
python -m soxswing.ibkr_runner --config config.ibkr.example.json connect-test
python -m soxswing.ibkr_runner --config config.ibkr.example.json positions
```

## Paper Order Creation

Paper order creation is intentionally locked. To test creating a non-transmitted
order in TWS paper mode:

1. Keep TWS connected to paper trading on port `7497`.
2. Set `dry_run=false`.
3. Set `allow_order_create=true`.
4. Keep `transmit_orders=false`.
5. Run:

```powershell
python -m soxswing.ibkr_runner --config config.ibkr.example.json stage-order --mock-price 221 --create-order
```

That creates the order in TWS with `Transmit=false`. Review it inside TWS before
transmitting or canceling.

# IBKR Paper Forward Test

This is the one-night validation step before trusting any automated SOXL/SOXS workflow.

## What the previous backtest proves

The 1-year test was not a tick-by-tick or second-by-second replay. It used IBKR historical 15-minute bars, RTH-only data, next-bar style execution assumptions, and estimated friction. Treat it as directional filtering, not proof that live fills will behave the same.

What it is useful for:

- Avoiding strategies that only work on a tiny recent sample.
- Comparing SOXL/SOXS parameter sets under the same assumptions.
- Finding safer position sizing and lower-turnover settings.

What it does not prove:

- Exact fill price.
- Queue priority.
- Behavior during fast drops or spikes inside a 15-minute candle.
- Whether TWS paper permissions, quotes, order routing, cancels, and logs are all working.

## Tonight's run order

Start signal-only first:

```powershell
python .\run_ibkr_paper_forward_test.py --config .\config.ibkr.paper_forward.example.json --once
python .\run_ibkr_paper_forward_test.py --config .\config.ibkr.paper_forward.example.json --duration-hours 6.5
```

If the logs look sane and the account is a paper `DU*` account, test tiny paper orders only:

```powershell
python .\run_ibkr_paper_forward_test.py --config .\config.ibkr.paper_forward.example.json --create-paper-orders --fixed-qty 1 --max-notional 300 --duration-hours 6.5
```

If IBKR only supplies delayed quotes and you want to test order plumbing in paper mode, you can add this flag. Do not use this as evidence that the strategy is live-ready:

```powershell
--allow-delayed-paper-quotes
```

## Output files

The script writes one folder per UTC day:

```text
ibkr_runtime/paper_forward/YYYYMMDD/
```

Key files:

- `heartbeat.csv`: every poll, every symbol score and latest IBKR bar.
- `signals.csv`: BUY/SELL/WATCH/HOLD decisions and order status.
- `summary.md`: latest readable summary.
- `state.json`: daily counters and trailing peak memory.

## Safety defaults

- No orders are created unless `--create-paper-orders` is explicitly passed.
- Order creation requires paper-only port `7497` or `4002`.
- Order creation requires a `DU*` paper account unless disabled in config.
- Default test size is 1 share, capped at 300 USD notional.
- Only SOXL and SOXS are allowed by default.

## If connection is refused

The API needs Trader Workstation or IB Gateway, not just a visually logged-in app screen. In TWS, enable:

```text
Edit > Global Configuration > API > Settings > Enable ActiveX and Socket Clients
```

For paper mode, the usual socket port is `7497`. If TWS shows a different socket port, change `connection.port` in `config.ibkr.paper_forward.example.json` to match it.

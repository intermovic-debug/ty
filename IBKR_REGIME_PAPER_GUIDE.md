# IBKR Regime/Knee/Shoulder Paper Guide

This is a research and paper-advisory strategy. It is deliberately not a live
autotrader, and `run_ibkr_regime_paper.py` contains no order-placement path.

## Design Boundary

- The universe is a fixed, liquid whitelist. Headlines cannot add a ticker.
- News and political/economic context can only reduce size or block risk.
- Technical signals use completed 15-minute bars. Incomplete or stale bars fail closed.
- Existing SOXL and SOXS positions are protected and are never imported into the strategy.
- An IBKR position is managed only after it is explicitly recorded under the strategy ID.
- One correlation group is allowed at a time, preventing SPY and QQQ from masquerading as diversification.
- Position size is capped by stop risk, cash, gross exposure, leverage, daily loss, and a hard share cap.

## Knee Entry

The strategy does not buy simply because price has fallen. Every entry requires:

1. Fast EMA above slow EMA and price above the slow EMA.
2. A recent pullback toward the fast EMA without breaking trend structure.
3. A completed bullish bar closing above the previous completed bar high.
4. RSI inside the configured range, acceptable ATR, no excessive extension, and adequate volume.
5. A compatible broad-market regime and a fresh daily context file.

This operationalizes "buy near the knee" as a confirmed pullback in an existing
trend. It does not claim to identify the exact low.

## Shoulder Exit

- The initial stop is always honored for a newly tagged paper position.
- ATR trailing does not activate until the position first reaches at least +1R.
- After activation, an ATR trailing stop or a confirmed structure break can produce an exit review.
- A time stop can exit a position that fails to make sufficient progress.
- There is no fixed small take-profit that automatically cuts off every winner.

This operationalizes "sell near the shoulder" as exiting after confirmed trend
damage. It does not claim to identify the exact high.

## Daily Workflow

Use a TWS paper login and API port `7497`, or paper IB Gateway port `4002`.

```powershell
python -m pip install -r .\requirements-ibkr.txt
python collect_ibkr_news_context.py --config .\config.ibkr.regime_paper.example.json
python run_ibkr_regime_paper.py --config .\config.ibkr.regime_paper.example.json scan
```

IBKR API news requires provider-specific subscriptions. If no subscribed
provider is available, create a timestamped input based on current, attributable
headlines and scheduled macro events, then run:

```powershell
python build_daily_market_context.py --input .\market_context.input.example.json
```

Do not use the example headline text as live input. A missing, stale, or
undersized headline set reduces or blocks risk.

## Existing Exposure

The scanner includes all positions visible in the IBKR paper account when it
calculates gross exposure. If a large position is held at another broker, set
`account.external_gross_notional_usd` in an ignored `*.local.json` config. This
prevents a nominally separate account from hiding total portfolio concentration.

## Promotion Gate

Do not add an order-transmission layer until all of these are true:

- At least 20 US market sessions and at least 30 closed paper trades are recorded.
- No signal was created from stale news, stale bars, an incomplete bar, or a live account.
- Protected and untagged positions were never proposed for sale.
- Results remain acceptable after fees plus 10 bps and 20 bps adverse execution stress.
- Maximum drawdown and daily loss remain inside the configured limits.
- A new symbol passes a separate historical out-of-sample test before whitelist activation.

Even after passing, start with paper order creation and `transmit=false`. IBKR
recommends testing order behavior manually in TWS before relying on API order
types, and every bracket/transmit combination must be verified separately.

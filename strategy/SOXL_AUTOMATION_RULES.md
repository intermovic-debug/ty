# SOXL Automation Rules

This file is strategy guidance only. The bot executes signals; it does not decide trades by itself unless a separate signal engine is added.

## Current operating model

- Trade symbol: SOXL only.
- Do not use SOXS as a hedge while recovering a SOXL position.
- Keep the existing recovery/swing position separate from scalp trades.
- Do not average down the recovery position automatically.

## Recovery position

Use manual or broker-side reservation orders for the recovery position. Do not commit personal holdings or account data to this public repo.

Suggested concept:

- Sell part of the recovery position into strength.
- Increase scalp allowance only after recovery shares are reduced.

## Scalp exposure ladder

- Large recovery position still open: scalp max 0-2 shares.
- After first recovery sale: scalp max 5-8 shares.
- After second recovery sale: scalp max 8-12 shares.
- After recovery position is mostly closed: scalp max 10-15 shares.
- Total SOXL exposure should remain capped.

## Scalp exits

- Take profit: +1.5% partial, +2.5% to +3.0% final.
- Stop loss: -1.0% to -1.5% in weak market, -2.0% max.
- Stop trading after two consecutive failed scalp entries.

## Market filters

Block new scalp buys when:

- SOXL is down more than roughly 8% intraday and still making lower lows.
- SOXX/SOX, NVDA, and QQQ are falling together.
- Volume expands while the intraday low keeps breaking.

Prefer buys only when:

- SOXL stops breaking lows.
- 3-5 minute candles start making higher lows.
- SOXX/NVDA/QQQ confirm the rebound.

## Safety

- `dry_run` must stay true until verified on the home PC.
- Start live tests with 1 share only.
- Keep an emergency `STOP_TRADING.txt` file ready.

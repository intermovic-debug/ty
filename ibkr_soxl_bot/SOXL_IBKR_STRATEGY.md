# SOXL IBKR Strategy

The goal is not to make the bot "smarter than the market." The goal is to remove
impulsive manual decisions and make every order follow a pre-approved rule.

## Operating Mode

- Use Paper Trading until connection, quote, order, and cancellation flows are verified.
- Use limit orders only.
- Keep live trading locked until the paper workflow has at least several clean test trades.
- Treat the existing recovery position separately from any short-term scalp position.
- Do not average down automatically during a sharp selloff.

## Recovery Position

The recovery position should use a sell ladder chosen before the market opens.
The ladder is stored in `config.local.json`, not in the public example config.

Recommended structure:

- Sell a first block on a relief bounce to reduce emotional pressure.
- Sell a second block near a stronger resistance area.
- Keep the final block for a higher recovery target or manual review.

## Scalp Position

Scalp trading is allowed only after size is capped.

- While the recovery position is still large, scalp size should stay tiny.
- After each recovery sale, scalp size can increase slightly.
- Profit-taking should be pre-set before entry.
- Loss limits should be pre-set before entry.
- Two failed scalp attempts in one day should stop new entries for that day.

## No-Trade Conditions

Do not open new SOXL scalp entries when:

- SOXL is making a straight waterfall move.
- SOXX, NVDA, QQQ, and semis are all weak at the same time.
- The user is trying to recover a large unrealized loss by adding size.
- The bot cannot read quotes, positions, or open orders reliably.

## Automation Roadmap

1. Connect to IBKR Paper Trading.
2. Read SOXL quote.
3. Read account positions.
4. Dry-run planned orders.
5. Submit one-share paper limit orders.
6. Add order cancellation and open-order sync.
7. Only then consider limited live trading.

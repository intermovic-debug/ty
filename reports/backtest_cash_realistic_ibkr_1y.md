# IBKR 1Y Cash-Start Realistic Intraday Backtest

- Generated at: 2026-07-08T14:10:29
- Source CSV: C:\Users\Administrator\Documents\Codex\2026-05-28\new-chat\ibkr_runtime\historical
- Synchronized bars: 6,502
- First bar UTC: 2025-07-08T13:30:00+00:00
- Last bar UTC: 2026-07-07T19:45:00+00:00
- Available symbols: SOXL, SOXS, SQQQ, TQQQ
- Execution: commission/share=0.0035, minimum=0.35, slippage_bps=5

| Rank | Variant | Return | Max DD | Score | Trades | Win Rate | PnL | Fees | Symbols |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | semis_original_best_tiered | 13.61% | -5.90% | 7.71% | 457 | 42.23% | 408.35 | 324.30 | SOXL, SOXS |
| 2 | semis_windowed_tiered | 8.17% | -7.06% | 1.11% | 446 | 40.81% | 245.05 | 314.64 | SOXL, SOXS |
| 3 | multi_selective_tiered | 0.91% | -2.73% | -1.82% | 163 | 39.88% | 27.32 | 114.10 | SOXL, SOXS, SQQQ, TQQQ |
| 4 | semis_low_turnover_tiered | 1.59% | -3.41% | -1.82% | 214 | 41.59% | 47.60 | 151.79 | SOXL, SOXS |
| 5 | semis_trend_rider_tiered | 1.07% | -4.58% | -3.52% | 214 | 38.79% | 31.97 | 151.72 | SOXL, SOXS |

## Best Variant Recent Trades

- 2026-06-30T17:30:00+00:00 SOXL 3 264.63 -> 265.24, pnl=1.46, stop_or_trail
- 2026-06-30T19:00:00+00:00 SOXL 3 269.34 -> 269.22, pnl=-0.73, stop_or_trail
- 2026-07-01T14:15:00+00:00 SOXS 175 3.56 -> 3.62, pnl=8.94, stop_or_trail
- 2026-07-01T17:30:00+00:00 SOXS 181 3.8 -> 3.79, pnl=-3.65, stop_or_trail
- 2026-07-02T14:00:00+00:00 SOXS 216 3.89 -> 3.87, pnl=-5.38, stop_or_trail
- 2026-07-02T16:00:00+00:00 SOXS 96 4.34 -> 4.42, pnl=7.23, stop_or_trail
- 2026-07-07T13:45:00+00:00 SOXS 161 4.8 -> 4.91, pnl=16.42, stop_or_trail
- 2026-07-07T19:30:00+00:00 SOXS 160 4.96 -> 4.94, pnl=-4.52, stop_or_trail

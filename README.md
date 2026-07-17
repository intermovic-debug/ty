# Multi-Symbol Swing Bot

Advisory and paper-trading scaffold for leveraged US ETF swing/day-trade
check-ins. It prepares reports, risk numbers, and copy-ready Meritz order
tickets, but it does not place live orders.

## Quick Start

Daily advisory run:

```powershell
python -m soxswing --config config.example.json
```

Intraday paper watcher:

```powershell
python -m soxswing.intraday --config config.intraday.example.json
```

Pre-sleep plan and Meritz order ticket:

```powershell
python -m soxswing.sleep_plan --config config.intraday.example.json
.\open_sleep_plan.ps1
.\open_order_ticket.ps1
```

Read-only overnight guard for existing holdings:

```powershell
python -m soxswing.night_guard --config config.night_guard.example.json
.\run_night_guard.ps1 -IntervalSeconds 60
.\open_night_guard.ps1
```

Full morning preparation run:

```powershell
.\run_morning_check.ps1
.\open_morning_check.ps1
```

Update the account snapshot after checking `[6110]` in iMeritz:

```powershell
.\update_account_snapshot.ps1 -CashUsd 3000.00 -CashKrw 4500000 -FxRate 1500.0
```

Backtest the active intraday universe:

```powershell
python -m soxswing.backtest_intraday --config config.backtest.example.json
```

Backtest the daily swing strategy for longer history:

```powershell
python -m soxswing.backtest_daily --config config.backtest.daily.365d.json
python -m soxswing.backtest_daily --config config.backtest.daily.730d.json
```

Backtest the current recovery situation with conservative execution assumptions:

```powershell
python -m soxswing.backtest_recovery --config config.recovery_backtest.60d.json
```

Compare cash-start intraday variants with next-bar fills, fees, and slippage:

```powershell
python -m soxswing.backtest_cash_realistic --config config.cash_realistic_sweep.60d.json
```

IBKR dry-run plan and socket check:

```powershell
python -m soxswing.ibkr_runner --config config.ibkr.example.json status
python -m soxswing.ibkr_runner --config config.ibkr.example.json socket-test
python -m soxswing.ibkr_runner --config config.ibkr.example.json plan --mock-price 206.07
```

IBKR cash-start route after closing the old SOXL position:

```powershell
.\run_ibkr_cash_status.ps1
python -m soxswing.ibkr_runner --config .\config.ibkr.cash_start.example.json plan
```

Regime-aware diversified paper scan with no order-placement code path:

```powershell
python collect_ibkr_news_context.py --config .\config.ibkr.regime_paper.example.json
python run_ibkr_regime_paper.py --config .\config.ibkr.regime_paper.example.json scan
.\run_regime_paper_scan.ps1
```

If the IBKR account has no API news subscription, prepare a current headline
input instead. The example file is a schema sample and must not be treated as
current news:

```powershell
python build_daily_market_context.py --input .\market_context.input.example.json
python run_ibkr_regime_paper.py --config .\config.ibkr.regime_paper.example.json scan
```

After a paper entry is actually filled, explicitly tag only that strategy
position so later scans can evaluate the shoulder exit. Untagged broker
positions are never treated as strategy positions:

```powershell
python run_ibkr_regime_paper.py --config .\config.ibkr.regime_paper.example.json record-entry --symbol QQQ --quantity 1 --entry-price 500.00 --stop-price 492.00
python run_ibkr_regime_paper.py --config .\config.ibkr.regime_paper.example.json record-exit --symbol QQQ
```

See `IBKR_REGIME_PAPER_GUIDE.md` for the strategy rules and the paper-validation
gate.

Optimized forward paper check without touching the broker:

```powershell
.\run_forward_paper_once.ps1
.\run_forward_paper_loop.ps1 -IntervalSeconds 60
```

Optimize and test a higher-return candidate separately:

```powershell
.\run_optimize_intraday.ps1
python -m soxswing.backtest_intraday --config config.backtest.optimized.example.json
```

## Active Universe

`universe.example.json` keeps several candidate groups available:

- Semiconductors 3x: SOXL, SOXS
- Nasdaq 100 3x: TQQQ, SQQQ
- Technology 3x: TECL, TECS
- S&P 500 3x: SPXL, SPXS
- FANG+ 3x ETN: FNGU, FNGD

The current default only activates:

- Semiconductors 3x
- Nasdaq 100 3x

This keeps the door open for more symbols while the live checklist starts from
the group that tested best in the recent 30-day comparison.

`universe.professional_paper.json` is a separate lower-risk research universe:

- Broad market 1x: SPY, QQQ, IWM
- Semiconductors 1x: SOXX
- Defensive diversifiers: TLT, GLD
- Leveraged research only, disabled by default: SOXL, TQQQ

The daily context may block or reduce risk inside this fixed whitelist. It may
not add a new ticker from a headline. SOXL and SOXS are protected by default so
the new strategy cannot manage an older recovery position.

## Current Guardrails

- Paper/advisory mode only.
- Active symbols are selected through `universe_path` and `active_groups`.
- Order sizing uses `account_snapshot.json` when it is fresh.
- Uses 15-minute bars and ignores stale market data.
- Maximum position: 18% of available USD cash.
- Per-trade risk budget: 0.3% of account value.
- Daily loss stop: 1.0% of account value.
- Entry requires score 5, minimum short momentum 0.4%, and RSI 48-68.
- Stop-loss 0.7%, take-profit 1.3%, trailing stop 0.5%.
- Max 2 paper trades per US session, 60-minute cooldown.

## Optimized Candidate

The latest parameter search keeps the higher-return candidate separate in
`config.intraday.optimized_candidate.json`.

- Max position: 24% of available USD cash.
- Per-trade risk budget: 0.5% of account value.
- Entry requires score 4, minimum short momentum 0.4%, and RSI 48-68.
- Stop-loss 0.5%, take-profit 1.2%, trailing stop 0.6%.
- 30-day backtest: return 1.72%, max drawdown -0.95%, trades 26.

This candidate raises return in the recent test, but also raises drawdown and
position size. Keep it as a candidate until manually approved.

## Files

- `config.intraday.example.json`: active intraday settings.
- `universe.example.json`: available ETF/ETN candidate groups.
- `reports/sleep_plan.html`: pre-sleep plan.
- `reports/order_ticket.html`: copy-ready Meritz values.
- `reports/night_guard.html`: read-only overnight guard dashboard.
- `reports/night_guard.md`: latest overnight guard summary.
- `reports/night_guard_ticket.md`: copy-ready `[6106]` values for held positions.
- `reports/morning_check.md`: combined iMeritz/RTD/account/data check.
- `reports/intraday_latest.html`: latest paper watcher report.
- `reports/backtest_intraday.md`: latest backtest summary.
- `reports/backtest_intraday_optimized.md`: optimized candidate backtest summary.
- `reports/optimize_intraday.md`: train/test parameter search report.
- `config.ibkr.example.json`: IBKR dry-run/paper/live gated configuration.
- `IBKR_AUTOTRADE_PLAN.md`: IBKR phased setup and safety procedure.
- `requirements-ibkr.txt`: Python dependency for TWS API integration.
- `config.ibkr.regime_paper.example.json`: read-only diversified paper scanner settings.
- `universe.professional_paper.json`: fixed metadata-rich research whitelist.
- `collect_ibkr_news_context.py`: subscribed IBKR-news risk context collector.
- `build_daily_market_context.py`: manual/current-headline fallback context builder.
- `run_ibkr_regime_paper.py`: closed-bar entry/exit evaluator with no order-placement path.
- `run_regime_paper_scan.ps1`: fail-closed news collection plus one paper scan.
- `IBKR_REGIME_PAPER_GUIDE.md`: design, workflow, and validation gate.
- `reports/imeritz_excel_rtd.md`: read-only iMeritz Excel RTD inspection.
- `account_snapshot.json`: latest user-confirmed `[6110]` balance snapshot.
- `update_account_snapshot.ps1`: refreshes the local balance snapshot.
- `capture_desktop_all.ps1`: captures all monitors, including iMeritz on a left-side monitor.
- `find_imeritz_windows.ps1`: lists iMeritz windows and open screen codes.
- `read_imeritz_account_ocr.ps1`: captures `[6110]` and writes OCR candidate values without overwriting the account snapshot.
- `inspect_excel_rtd.ps1`: inspects open Excel workbooks for RTD formulas and values.
- `inspect_imeritz_rtd_registry.ps1`: records the registered iMeritz RTD server.
- `inspect_imeritz_rtd_files.ps1`: extracts read-only RTD metadata hints from local iMeritz files.
- `close_imeritz_notice.ps1`: attempts to close iMeritz notice dialogs only.
- `run_night_guard.ps1`: runs the read-only overnight guard while the PC is on.
- `open_night_guard.ps1`: opens the overnight guard dashboard.
- `open_imeritz_6106.ps1`: opens or focuses the Meritz `[6106]` screen helper.
- `AUTOMATION_CAPABILITY_REPORT.md`: what can and cannot be automated safely.
- `MERITZ_AUTOWATCH_PLAN.md`: how to use Meritz condition/watch screens.
- `OVERNIGHT_FINDINGS.md`: current iMeritz access and automation findings.
- `CHECK_ROUTINE.md`: daily check windows.

## Safety

Codex does not click live buy/sell buttons. Use Meritz's own condition/watch
order screens for real protection, and press the final submit/confirm button
yourself after reviewing account, symbol, quantity, price, stop, and validity.

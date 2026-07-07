# Company PC Handoff

Purpose: move the advisory-only stock automation workspace to another PC
without uploading private account data, screenshots, OCR output, or trade logs.

## Current Status On This PC

- This folder is not currently a Git repository.
- The workspace contains both reusable code and sensitive local artifacts.
- Do not upload the whole folder as-is.

## Safe To Sync

Core code:

- `soxswing/`
- `README.md`
- `CHECK_ROUTINE.md`
- `SCHEDULER.md`
- `MERITZ_AUTOWATCH_PLAN.md`
- `MERITZ_FINDINGS.md`
- `IMERITZ_ACCESS_NOTES.md`
- `OVERNIGHT_FINDINGS.md`
- `AUTOMATION_CAPABILITY_REPORT.md`

Example/config files:

- `account_snapshot.example.json`
- `config.example.json`
- `config.intraday.example.json`
- `config.intraday.optimized_candidate.json`
- `config.night_guard.example.json`
- `config.backtest.example.json`
- `config.backtest.optimized.example.json`
- `config.optimize.example.json`
- `universe.example.json`

Helper scripts:

- `run_*.ps1`
- `open_*.ps1`
- `capture_*.ps1`
- `find_imeritz_windows.ps1`
- `inspect_*.ps1`
- `read_imeritz_account_ocr.ps1`
- `close_imeritz_notice.ps1`
- `notify_signal.ps1`
- `ocr_image.ps1`
- `update_account_snapshot.ps1`

## Do Not Sync

These are ignored by `.gitignore`:

- `account_snapshot.json`
- `data/`
- `logs/`
- `reports/`
- `captures/`
- `*.png`
- `__pycache__/`

These may include account balance, account number fragments, screen captures,
OCR text, trade logs, or live order report output.

## Setup On Company PC

After cloning the private repository:

```powershell
cd "C:\path\to\repo"
python --version
python -m py_compile .\soxswing\sleep_plan.py
python -m soxswing.sleep_plan --config config.intraday.optimized_candidate.json
```

If market-data download is blocked by the company network, run the same command
on the home PC or use a personal network. The scripts are read-only and do not
submit live iMeritz orders.

## Recommended Git Flow

On this PC:

```powershell
git init
git add .gitignore README.md COMPANY_PC_HANDOFF.md soxswing *.md *.example.json config.intraday.optimized_candidate.json *.ps1
git status
git commit -m "Add advisory stock automation workspace"
git branch -M main
git remote add origin <PRIVATE_GITHUB_REPO_URL>
git push -u origin main
```

Before pushing, inspect `git status` carefully. If any `account_snapshot.json`,
`data/`, `logs/`, `reports/`, `captures/`, or `*.png` file appears, stop and
fix the ignore rules before pushing.

## Safety Boundary

The transferred code is for read-only analysis, report generation, and
copy-ready tickets. It must not be changed into a live order submitter on a
work PC without a separate risk review.

# Home PC Setup

Use this repo as a transfer package. Do not run live orders immediately.

## 1. Get the files on the home PC

```powershell
git clone https://github.com/intermovic-debug/ty.git
cd ty
```

If `git` is not installed, download the repository ZIP from GitHub and extract it.

## 2. Install Python dependency

```powershell
python -m pip install -r requirements.txt
```

## 3. Create a local config

Copy the template to a local ignored file:

```powershell
Copy-Item .\configs\meritz_soxl_home_config.template.json .\configs\meritz_soxl_config.local.json
```

Keep `dry_run: true` until calibration and test orders are verified.

## 4. Login to Meritz HTS

- Keep HTS open on the intended Windows desktop.
- Disable sleep mode.
- Keep display scaling and window position fixed.
- Do not change the HTS layout after calibration.

## 5. Calibrate coordinates

```powershell
python .\meritz_sox_bot.py calibrate --config .\configs\meritz_soxl_config.local.json
```

Use safe dry-run checks first:

```powershell
python .\meritz_sox_bot.py status --config .\configs\meritz_soxl_config.local.json
python .\meritz_sox_bot.py test-order --config .\configs\meritz_soxl_config.local.json --symbol SOXL --action BUY --qty 1
python .\meritz_sox_bot.py test-order --config .\configs\meritz_soxl_config.local.json --symbol SOXL --action SELL --qty 1
```

## 6. Run dry-run signal loop

```powershell
python .\meritz_sox_bot.py run --config .\configs\meritz_soxl_config.local.json
```

## 7. Live trading rule

Do not enable live clicking until dry-run logs match exactly what you expect. Start with 1 share only if live mode is ever enabled.

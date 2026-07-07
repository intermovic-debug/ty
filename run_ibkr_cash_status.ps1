$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
python -m soxswing.ibkr_runner --config .\config.ibkr.cash_start.example.json status
python -m soxswing.ibkr_runner --config .\config.ibkr.cash_start.example.json connect-test
python -m soxswing.ibkr_runner --config .\config.ibkr.cash_start.example.json account
python -m soxswing.ibkr_runner --config .\config.ibkr.cash_start.example.json positions
Pop-Location

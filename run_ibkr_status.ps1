$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
python -m soxswing.ibkr_runner --config .\config.ibkr.example.json status
Pop-Location

param(
    [double]$MockPrice = 206.07
)

$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
python -m soxswing.ibkr_runner --config .\config.ibkr.example.json plan --mock-price $MockPrice
Pop-Location

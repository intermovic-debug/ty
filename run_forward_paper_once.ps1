$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

python -m soxswing.intraday --config .\config.intraday.optimized_forward_paper.json

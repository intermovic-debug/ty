param(
    [string]$Config = ".\config.ibkr.regime_paper.example.json",
    [switch]$SkipNews
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

if (-not $SkipNews) {
    python .\collect_ibkr_news_context.py --config $Config
    if ($LASTEXITCODE -ne 0) {
        throw "IBKR news context collection failed. No scan was run."
    }
}

python .\run_ibkr_regime_paper.py --config $Config scan
if ($LASTEXITCODE -ne 0) {
    throw "The paper scan was blocked. Read reports\ibkr_regime_paper_latest.md."
}

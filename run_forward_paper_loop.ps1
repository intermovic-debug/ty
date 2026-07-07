param(
    [int]$IntervalSeconds = 60,
    [int]$Iterations = 0
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$count = 0
while ($true) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$stamp] running optimized forward paper check..."
    python -m soxswing.intraday --config .\config.intraday.optimized_forward_paper.json
    $count += 1
    if ($Iterations -gt 0 -and $count -ge $Iterations) {
        break
    }
    Start-Sleep -Seconds $IntervalSeconds
}

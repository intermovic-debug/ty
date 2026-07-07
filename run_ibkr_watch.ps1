param(
    [int]$Iterations = 0
)

$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
if ($Iterations -gt 0) {
    python -m soxswing.ibkr_runner --config .\config.ibkr.example.json watch --iterations $Iterations
} else {
    python -m soxswing.ibkr_runner --config .\config.ibkr.example.json watch
}
Pop-Location

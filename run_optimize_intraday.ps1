$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
python -m soxswing.optimize_intraday --config config.optimize.example.json

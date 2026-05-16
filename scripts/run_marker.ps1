$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    & ".\scripts\install.ps1"
}

if (-not (Test-Path "config.json")) {
    Copy-Item "config.example.json" "config.json"
}

.\.venv\Scripts\python.exe -m app.main

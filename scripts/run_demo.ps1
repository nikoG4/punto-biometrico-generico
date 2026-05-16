$ErrorActionPreference = "Stop"

if (-not (Test-Path "config.json")) {
    Copy-Item "config.example.json" "config.json"
}

$demoConfig = Join-Path $env:TEMP "punto-biometrico-demo-config.json"
$json = Get-Content "config.json" -Raw | ConvertFrom-Json
$json.offline_mode = $true
if (-not $json.recognition) {
    $json | Add-Member -MemberType NoteProperty -Name recognition -Value ([pscustomobject]@{})
}
$json.recognition.provider = "demo"
$json | ConvertTo-Json -Depth 10 | Set-Content $demoConfig -Encoding UTF8

if (-not (Test-Path ".venv")) {
    & ".\scripts\install.ps1"
}

$env:BIOMETRIC_CONFIG = $demoConfig
.\.venv\Scripts\python.exe -m app.main

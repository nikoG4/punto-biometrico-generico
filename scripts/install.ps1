$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

Write-Host "Instalando dependencias opcionales de reconocimiento..."
try {
    .\.venv\Scripts\python.exe -m pip install -r requirements-optional.txt
} catch {
    Write-Warning "No se pudieron instalar todas las dependencias opcionales. La app funcionara en modo demo/fallback."
}

if (-not (Test-Path "config.json")) {
    Copy-Item "config.example.json" "config.json"
}

Write-Host "Instalacion completa. Ejecute: .\.venv\Scripts\python.exe -m app.main"

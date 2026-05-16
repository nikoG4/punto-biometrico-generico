param(
    [switch]$IncludeLocalConfig
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$PyInstaller = Join-Path $Root ".venv\Scripts\pyinstaller.exe"
$BuildDir = Join-Path $Root "build"
$DistDir = Join-Path $Root "dist"
$ReleaseDir = Join-Path $Root "release"
$AppName = "PuntoBiometrico"
$AppDir = Join-Path $DistDir $AppName
$InsightFaceCache = Join-Path $env:USERPROFILE ".insightface\models\buffalo_l"
$PortableInsightFaceDir = Join-Path $AppDir "models\insightface\models\buffalo_l"

function Assert-InRoot {
    param([string]$PathToCheck)
    $resolvedRoot = [System.IO.Path]::GetFullPath($Root)
    $resolvedPath = [System.IO.Path]::GetFullPath($PathToCheck)
    if (-not $resolvedPath.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Ruta fuera del proyecto: $resolvedPath"
    }
}

function Remove-SafeDirectory {
    param([string]$PathToRemove)
    if (Test-Path $PathToRemove) {
        Assert-InRoot $PathToRemove
        Remove-Item -LiteralPath $PathToRemove -Recurse -Force
    }
}

Set-Location $Root

if (-not (Test-Path $Python)) {
    & (Join-Path $Root "scripts\install.ps1")
}

& $Python -m pip install --upgrade pyinstaller

Remove-SafeDirectory $BuildDir
Remove-SafeDirectory $DistDir
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null

$pyinstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--console",
    "--name", $AppName,
    "--paths", $Root,
    "--add-data", "$Root\config.example.json;.",
    "--add-data", "$Root\sql;sql",
    "--hidden-import", "pymysql",
    "--hidden-import", "sqlalchemy.dialects.mysql.pymysql",
    "--hidden-import", "sqlalchemy.dialects.sqlite",
    "--hidden-import", "faiss",
    "--collect-submodules", "insightface",
    "--collect-submodules", "onnxruntime",
    "--collect-submodules", "albumentations",
    "--collect-submodules", "sklearn",
    "--collect-data", "cv2",
    "--collect-data", "insightface",
    (Join-Path $Root "app\main.py")
)

& $PyInstaller @pyinstallerArgs

if (-not (Test-Path (Join-Path $AppDir "$AppName.exe"))) {
    throw "No se genero $AppName.exe"
}

Copy-Item -LiteralPath (Join-Path $Root "config.example.json") -Destination (Join-Path $AppDir "config.example.json") -Force
Copy-Item -LiteralPath (Join-Path $Root "README.md") -Destination (Join-Path $AppDir "README.md") -Force

if (Test-Path $InsightFaceCache) {
    New-Item -ItemType Directory -Force -Path (Split-Path $PortableInsightFaceDir -Parent) | Out-Null
    Copy-Item -LiteralPath $InsightFaceCache -Destination $PortableInsightFaceDir -Recurse -Force
}

@"
@echo off
cd /d "%~dp0"
PuntoBiometrico.exe
"@ | Set-Content -Path (Join-Path $AppDir "INICIAR_PUNTO_BIOMETRICO.bat") -Encoding ASCII

@"
@echo off
cd /d "%~dp0"
PuntoBiometrico.exe --diagnose
pause
"@ | Set-Content -Path (Join-Path $AppDir "DIAGNOSTICO.bat") -Encoding ASCII

@"
@echo off
cd /d "%~dp0"
PuntoBiometrico.exe --no-fullscreen --exit-after-ms 5000
"@ | Set-Content -Path (Join-Path $AppDir "PROBAR_UI_5_SEGUNDOS.bat") -Encoding ASCII

@"
PUNTO BIOMETRICO - PAQUETE PORTABLE

1. Ejecutar DIAGNOSTICO.bat para validar camara, base biometrica y SCT/RRHH.
2. Ejecutar INICIAR_PUNTO_BIOMETRICO.bat para abrir el kiosco.
3. Si no existe config.json, el sistema lo crea desde config.example.json.
4. Para una demo con datos reales, copiar el config.json validado junto al .exe.

Este paquete no instala servicios de Windows. Para modo kiosco de arranque automatico usar scripts/setup_windows_kiosk.ps1 desde el repositorio fuente.
"@ | Set-Content -Path (Join-Path $AppDir "README_DEMO.txt") -Encoding ASCII

$stamp = Get-Date -Format "yyyyMMdd-HHmm"
$publicZip = Join-Path $ReleaseDir "$AppName-public-$stamp.zip"
if (Test-Path $publicZip) {
    Remove-Item -LiteralPath $publicZip -Force
}
Compress-Archive -Path (Join-Path $AppDir "*") -DestinationPath $publicZip -Force

$localZip = $null
if ($IncludeLocalConfig) {
    $localConfig = Join-Path $Root "config.json"
    if (Test-Path $localConfig) {
        Copy-Item -LiteralPath $localConfig -Destination (Join-Path $AppDir "config.json") -Force
        $localCache = Join-Path $Root "local_cache.db"
        if (Test-Path $localCache) {
            Copy-Item -LiteralPath $localCache -Destination (Join-Path $AppDir "local_cache.db") -Force
        }
        $localZip = Join-Path $ReleaseDir "$AppName-local-demo-NO_SUBIR-$stamp.zip"
        if (Test-Path $localZip) {
            Remove-Item -LiteralPath $localZip -Force
        }
        Compress-Archive -Path (Join-Path $AppDir "*") -DestinationPath $localZip -Force
    }
}

Write-Host "Build listo:"
Write-Host "  Carpeta: $AppDir"
Write-Host "  ZIP publico: $publicZip"
if ($localZip) {
    Write-Host "  ZIP local con config real: $localZip"
}

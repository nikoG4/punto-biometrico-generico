$ErrorActionPreference = "Stop"

$startupDir = [Environment]::GetFolderPath("Startup")
$launcher = Join-Path $startupDir "PuntoBiometrico.cmd"

if (Test-Path $launcher) {
    Remove-Item -LiteralPath $launcher -Force
}

$winlogon = "HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Winlogon"
if (Test-Path $winlogon) {
    Remove-ItemProperty -Path $winlogon -Name "Shell" -ErrorAction SilentlyContinue
}

powercfg /change monitor-timeout-ac 15
powercfg /change standby-timeout-ac 30

Write-Host "Configuracion kiosco revertida para el usuario actual."

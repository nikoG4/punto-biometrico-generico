param(
    [string]$UserName = $env:USERNAME,
    [string]$AppRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [switch]$ReplaceExplorerShell
)

$ErrorActionPreference = "Stop"

$runScript = Join-Path $AppRoot "scripts\run_marker.ps1"
if (!(Test-Path $runScript)) {
    throw "No existe $runScript"
}

$startupDir = [Environment]::GetFolderPath("Startup")
$launcher = Join-Path $startupDir "PuntoBiometrico.cmd"

@"
@echo off
cd /d "$AppRoot"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$runScript"
"@ | Set-Content $launcher -Encoding ASCII

powercfg /change monitor-timeout-ac 0
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /hibernate off

New-Item -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer" -Force | Out-Null
Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer" -Name "NoLowDiskSpaceChecks" -Type DWord -Value 1

if ($ReplaceExplorerShell) {
    $shellCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$runScript`""
    New-Item -Path "HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Winlogon" -Force | Out-Null
    Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Winlogon" -Name "Shell" -Type String -Value $shellCommand
    Write-Warning "Explorer fue reemplazado como shell SOLO para el usuario actual. Use undo_windows_kiosk.ps1 para revertir."
}

Write-Host "Kiosco configurado para el usuario $UserName."
Write-Host "Launcher creado: $launcher"
Write-Host "Reinicie Windows para probar arranque automatico."

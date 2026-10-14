[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$ControlTestRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$BackupDirectory = Join-Path $ControlTestRoot '测试备份'
$Editor = 'H:\VisualDrone\Epic\unrealengine\UE_4.27\Engine\Binaries\Win64\UE4Editor-Cmd.exe'
$Build = 'H:\VisualDrone\Epic\unrealengine\UE_4.27\Engine\Build\BatchFiles\Build.bat'
$Project = 'H:\VisualDrone\AirSim\RMUC2026-UE4\Blocks.uproject'
$Cleanup = Join-Path $PSScriptRoot '清理RMUC2026临时防坠与调试实体.py'
$CleanupAscii = 'H:\VisualDrone\AirSim\RMUC2026-UE4\Saved\rmuc_cleanup.py'
$PreparedSettings = Join-Path $BackupDirectory 'AirSim-settings-HITL-RMUC2026-100mm-COM16.json'
$LiveSettings = Join-Path $HOME 'Documents\AirSim\settings.json'
$SettingsBackup = Join-Path $BackupDirectory 'settings-before-rmuc-100mm.json'

if (Get-Process UE4Editor -ErrorAction SilentlyContinue) {
    throw 'UE4Editor is still running. Save any intended level changes, close UE4 completely, then rerun this script.'
}

Write-Host '[1/4] Removing the temporary collision floor and making markers editor-only'
# UE4.27 parses -ExecutePythonScript through the active ANSI code page. A
# Chinese path can become mojibake and be evaluated as Python source text.
# Always stage this short script at an ASCII-only path before launching UE4.
Copy-Item -LiteralPath $Cleanup -Destination $CleanupAscii -Force
& $Editor $Project -nullrhi -nosound -unattended -nop4 "-ExecutePythonScript=$CleanupAscii"
if ($LASTEXITCODE -ne 0) { throw 'UE4 cleanup command failed' }

$LatestLog = Get-ChildItem 'H:\VisualDrone\AirSim\RMUC2026-UE4\Saved\Logs' -File |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not (Select-String -LiteralPath $LatestLog.FullName -Pattern 'RMUC_DEBUG_CLEANUP_OK' -Quiet)) {
    throw "Cleanup completion marker missing from $($LatestLog.FullName)"
}

Write-Host '[2/4] Building the RMUC2026 editor with configurable PawnScale'
& $Build BlocksEditor Win64 Development "-Project=$Project" -WaitMutex -FromMsBuild
if ($LASTEXITCODE -ne 0) { throw 'UE4/AirSim editor build failed' }

Write-Host '[3/4] Backing up and activating the 100 mm HITL settings'
if (-not (Test-Path -LiteralPath $SettingsBackup)) {
    Copy-Item -LiteralPath $LiveSettings -Destination $SettingsBackup
}
Copy-Item -LiteralPath $PreparedSettings -Destination $LiveSettings -Force

Write-Host '[4/4] Verifying active scale setting'
$Settings = Get-Content -LiteralPath $LiveSettings -Raw -Encoding UTF8 | ConvertFrom-Json
$Scale = [double]$Settings.Vehicles.PX4.PawnScale
if ([math]::Abs($Scale - 0.101865) -gt 0.000001) {
    throw "Unexpected active PawnScale: $Scale"
}

Write-Host 'RMUC2026 PATCH OK'
Write-Host "PawnScale=$Scale; expected visual/collision footprint approximately 10 x 10 cm."
Write-Host 'Reopen the project and the RMUC2026_Full_Field map, then run a collision-size check before flight.'

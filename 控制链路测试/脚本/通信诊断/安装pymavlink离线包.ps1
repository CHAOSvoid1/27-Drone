[CmdletBinding()]
param(
    [string]$NanoIp = "10.128.80.1",
    [string]$NanoUser = "root",
    [string]$RemoteWheelDir = "/root/uav/lib/wheels"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent (Split-Path -Parent $scriptDir)
$observerScript = Join-Path $scriptDir "mavlink_protocol_observer.py"
$verifyScript = Join-Path $scriptDir "verify_pymavlink.py"
$futureWheelItem = Get-ChildItem -LiteralPath $projectDir -Recurse -File -Filter "future-1.0.0-py3-none-any.whl" | Select-Object -First 1
$pymavlinkWheelItem = Get-ChildItem -LiteralPath $projectDir -Recurse -File -Filter "pymavlink-2.4.42-py3-none-any.whl" | Select-Object -First 1

if ($null -eq $futureWheelItem) {
    throw "Offline wheel was not found below the project directory: future-1.0.0-py3-none-any.whl"
}
if ($null -eq $pymavlinkWheelItem) {
    throw "Offline wheel was not found below the project directory: pymavlink-2.4.42-py3-none-any.whl"
}
if (-not (Test-Path -LiteralPath $observerScript)) {
    throw "Observer script is missing: $observerScript"
}
if (-not (Test-Path -LiteralPath $verifyScript)) {
    throw "Verification script is missing: $verifyScript"
}

$futureWheel = $futureWheelItem.FullName
$pymavlinkWheel = $pymavlinkWheelItem.FullName
Write-Host "Using $futureWheel"
Write-Host "Using $pymavlinkWheel"

Write-Host "[1/6] Checking Nano SSH"
if (-not (Test-NetConnection $NanoIp -Port 22 -InformationLevel Quiet)) {
    throw "Nano SSH is not reachable at $NanoIp`:22"
}

Write-Host "[2/6] Creating remote wheel directory"
ssh "$NanoUser@$NanoIp" "mkdir -p '$RemoteWheelDir'"
if ($LASTEXITCODE -ne 0) { throw "Remote mkdir failed" }

Write-Host "[3/6] Uploading architecture-independent wheels"
scp $futureWheel "$NanoUser@$NanoIp`:$RemoteWheelDir/future-1.0.0-py3-none-any.whl"
if ($LASTEXITCODE -ne 0) { throw "future upload failed" }
scp $pymavlinkWheel "$NanoUser@$NanoIp`:$RemoteWheelDir/pymavlink-2.4.42-py3-none-any.whl"
if ($LASTEXITCODE -ne 0) { throw "pymavlink upload failed" }

Write-Host "[4/6] Installing offline with bytecode compilation disabled"
ssh "$NanoUser@$NanoIp" "mkdir -p /root/uav/tmp/pip"
if ($LASTEXITCODE -ne 0) { throw "Remote pip temporary directory creation failed" }

$installFuture = "TMPDIR=/root/uav/tmp/pip PYTHONDONTWRITEBYTECODE=1 python3 -m pip install --no-index --no-deps --no-compile --disable-pip-version-check '$RemoteWheelDir/future-1.0.0-py3-none-any.whl'"
ssh "$NanoUser@$NanoIp" $installFuture
if ($LASTEXITCODE -ne 0) { throw "Offline future install failed" }

$installPymavlink = "TMPDIR=/root/uav/tmp/pip PYTHONDONTWRITEBYTECODE=1 MAVLINK_DIALECT=common python3 -m pip install --no-index --no-deps --no-compile --disable-pip-version-check '$RemoteWheelDir/pymavlink-2.4.42-py3-none-any.whl'"
ssh "$NanoUser@$NanoIp" $installPymavlink
if ($LASTEXITCODE -ne 0) { throw "Offline pymavlink install failed; check dmesg for an OOM kill" }

Write-Host "[5/6] Uploading the memory-limited common-dialect observer"
scp $observerScript "$NanoUser@$NanoIp`:/root/uav/bin/mavlink_protocol_observer.py"
if ($LASTEXITCODE -ne 0) { throw "Observer upload failed" }
scp $verifyScript "$NanoUser@$NanoIp`:/root/uav/bin/verify_pymavlink.py"
if ($LASTEXITCODE -ne 0) { throw "Verification script upload failed" }
ssh "$NanoUser@$NanoIp" "chmod +x /root/uav/bin/mavlink_protocol_observer.py"
if ($LASTEXITCODE -ne 0) { throw "Observer chmod failed" }

Write-Host "[6/6] Verifying imports and bundled MAVLink dialect"
$verifyCommand = "python3 -u /root/uav/bin/verify_pymavlink.py"
ssh "$NanoUser@$NanoIp" $verifyCommand
if ($LASTEXITCODE -ne 0) { throw "pymavlink import verification failed" }

Write-Host "Offline pymavlink installation verified."
Write-Host "No serial port was opened and no MAVLink message was sent."

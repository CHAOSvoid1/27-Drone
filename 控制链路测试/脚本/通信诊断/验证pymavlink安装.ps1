[CmdletBinding()]
param(
    [string]$NanoIp = "10.128.80.1",
    [string]$NanoUser = "root"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$observerScript = Join-Path $scriptDir "mavlink_protocol_observer.py"
$verifyScript = Join-Path $scriptDir "verify_pymavlink.py"

foreach ($path in @($observerScript, $verifyScript)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required file is missing: $path"
    }
}

if (-not (Test-NetConnection $NanoIp -Port 22 -InformationLevel Quiet)) {
    throw "Nano SSH is not reachable at $NanoIp`:22"
}

ssh "$NanoUser@$NanoIp" "mkdir -p /root/uav/bin"
if ($LASTEXITCODE -ne 0) { throw "Remote mkdir failed" }

scp $observerScript "$NanoUser@$NanoIp`:/root/uav/bin/mavlink_protocol_observer.py"
if ($LASTEXITCODE -ne 0) { throw "Observer upload failed" }

scp $verifyScript "$NanoUser@$NanoIp`:/root/uav/bin/verify_pymavlink.py"
if ($LASTEXITCODE -ne 0) { throw "Verification script upload failed" }

ssh "$NanoUser@$NanoIp" "chmod +x /root/uav/bin/mavlink_protocol_observer.py /root/uav/bin/verify_pymavlink.py"
if ($LASTEXITCODE -ne 0) { throw "chmod failed" }

ssh "$NanoUser@$NanoIp" "python3 -u /root/uav/bin/verify_pymavlink.py"
if ($LASTEXITCODE -ne 0) { throw "pymavlink verification failed" }

Write-Host "pymavlink installation is valid. No serial port was opened."

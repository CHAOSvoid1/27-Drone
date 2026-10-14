[CmdletBinding()]
param(
    [string]$NanoIp = "10.128.80.1",
    [string]$NanoUser = "root",
    [string]$Device = "/dev/ttyS1",
    [int]$Baud = 115200,
    [int]$Duration = 30
)

$ErrorActionPreference = "Stop"

if ($Duration -lt 10 -or $Duration -gt 120) {
    throw "Duration must be between 10 and 120 seconds"
}

Write-Host "[1/4] Checking Nano SSH"
if (-not (Test-NetConnection $NanoIp -Port 22 -InformationLevel Quiet)) {
    throw "Nano SSH is not reachable at $NanoIp`:22"
}

Write-Host "[2/4] Checking dependencies and script"
ssh "$NanoUser@$NanoIp" "python3 -u /root/uav/bin/verify_pymavlink.py"
if ($LASTEXITCODE -ne 0) { throw "pymavlink dependency check failed" }
ssh "$NanoUser@$NanoIp" "test -x /root/uav/bin/mavlink_protocol_observer.py"
if ($LASTEXITCODE -ne 0) { throw "Observer script check failed" }
ssh "$NanoUser@$NanoIp" "test -c '$Device'"
if ($LASTEXITCODE -ne 0) { throw "Serial device check failed: $Device" }

Write-Host "[3/4] Offline plan check"
ssh "$NanoUser@$NanoIp" "python3 /root/uav/bin/mavlink_protocol_observer.py --dry-run --device '$Device' --baud $Baud --request-autopilot-version"
if ($LASTEXITCODE -ne 0) { throw "Dry-run failed" }

Write-Host "[4/4] Running receive/heartbeat/version-request observation"
Write-Host "Allowed output: companion HEARTBEAT and MAV_CMD_REQUEST_MESSAGE(512) only."
$run = "python3 /root/uav/bin/mavlink_protocol_observer.py --device '$Device' --baud $Baud --duration $Duration --target-system 1 --target-component 1 --request-autopilot-version"
ssh "$NanoUser@$NanoIp" $run
if ($LASTEXITCODE -ne 0) { throw "Live non-control observation failed" }

Write-Host "Non-control MAVLink observation passed."

[CmdletBinding()]
param(
    [string]$NanoIp = "10.128.80.1",
    [string]$NanoUser = "root",
    [string]$RemoteDir = "/root/uav/bin"
)

$ErrorActionPreference = "Stop"

$localDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$files = @(
    "mavlink_protocol_observer.py",
    "check_pymavlink_environment.sh"
)

Write-Host "[1/5] Checking SSH at $NanoIp`:22"
$reachable = Test-NetConnection $NanoIp -Port 22 -InformationLevel Quiet
if (-not $reachable) {
    throw "Nano SSH is not reachable at $NanoIp`:22. Check USB-NCM and ping first."
}

Write-Host "[2/5] Ensuring remote directory exists"
ssh "$NanoUser@$NanoIp" "mkdir -p '$RemoteDir'"
if ($LASTEXITCODE -ne 0) { throw "Remote mkdir failed" }

Write-Host "[3/5] Uploading non-actuating test files"
foreach ($name in $files) {
    $source = Join-Path $localDir $name
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Missing local file: $source"
    }
    scp $source "$NanoUser@$NanoIp`:$RemoteDir/$name"
    if ($LASTEXITCODE -ne 0) { throw "Upload failed: $name" }
}

Write-Host "[4/5] Setting permissions and running offline checks"
ssh "$NanoUser@$NanoIp" "chmod +x '$RemoteDir/mavlink_protocol_observer.py' '$RemoteDir/check_pymavlink_environment.sh'"
if ($LASTEXITCODE -ne 0) { throw "chmod failed" }

ssh "$NanoUser@$NanoIp" "python3 '$RemoteDir/mavlink_protocol_observer.py' --dry-run --request-autopilot-version"
if ($LASTEXITCODE -ne 0) { throw "Observer dry-run failed" }

Write-Host "[5/5] Collecting pymavlink compatibility information"
ssh "$NanoUser@$NanoIp" "'$RemoteDir/check_pymavlink_environment.sh'"
if ($LASTEXITCODE -ne 0) { throw "Environment check failed" }

Write-Host "Deployment and offline checks completed."
Write-Host "No live MAVLink, mode, setpoint, motor, or actuator command was sent."

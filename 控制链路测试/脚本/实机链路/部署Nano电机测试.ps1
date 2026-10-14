param(
    [string]$NanoIp = "10.128.80.1",
    [string]$NanoUser = "root",
    [string]$RemoteDir = "/root/uav/bin"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$localScript = Join-Path $scriptDir "nano_motor_test.py"
$target = "$NanoUser@$NanoIp"

if (-not (Test-Path -LiteralPath $localScript -PathType Leaf)) {
    throw "Missing local script: $localScript"
}

Write-Host "[1/5] Checking SSH at $NanoIp`:22"
if (-not (Test-NetConnection -ComputerName $NanoIp -Port 22 -InformationLevel Quiet)) {
    throw "SSH is not reachable at $NanoIp`:22"
}

Write-Host "[2/5] Creating remote directory"
ssh $target "mkdir -p '$RemoteDir' /root/uav/logs"
if ($LASTEXITCODE -ne 0) { throw "Failed to create remote directory" }

Write-Host "[3/5] Uploading nano_motor_test.py"
scp $localScript "$target`:$RemoteDir/nano_motor_test.py"
if ($LASTEXITCODE -ne 0) { throw "Upload failed" }

Write-Host "[4/5] Running offline self-check on Nano"
ssh $target "chmod +x '$RemoteDir/nano_motor_test.py' && python3 '$RemoteDir/nano_motor_test.py' --self-check"
if ($LASTEXITCODE -ne 0) { throw "Remote self-check failed" }

Write-Host "[5/5] Running offline no-4S dry-run on Nano"
ssh $target "python3 '$RemoteDir/nano_motor_test.py' --dry-run --device /dev/ttyS1 --baud 115200 --motor 1 --pct 8 --secs 1 --px4-timeout 2 --no-4s-probe"
if ($LASTEXITCODE -ne 0) { throw "Remote dry-run failed" }

Write-Host "Deployment checks passed. No live MAVLink motor command was sent."
Write-Host "Continue with 07-端到端联调.md, section 4 (no-4S probe)."

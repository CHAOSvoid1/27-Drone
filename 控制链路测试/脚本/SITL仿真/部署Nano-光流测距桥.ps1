param(
    [string]$NanoHost = "10.128.80.1",
    [string]$NanoUser = "root",
    [string]$RemoteDirectory = "/root/uav/bin"
)

$ErrorActionPreference = "Stop"
$localScript = Join-Path $PSScriptRoot "nano_flow_tof_mavlink_bridge.py"

if (-not (Test-Path -LiteralPath $localScript)) {
    throw "Missing local bridge: $localScript"
}

Write-Host "[1/4] Checking Nano SSH"
$probe = Test-NetConnection -ComputerName $NanoHost -Port 22 -WarningAction SilentlyContinue
if (-not $probe.TcpTestSucceeded) {
    throw "Nano SSH is unavailable at ${NanoHost}:22"
}

Write-Host "[2/4] Creating remote directory"
& ssh "${NanoUser}@${NanoHost}" "mkdir -p '$RemoteDirectory'"
if ($LASTEXITCODE -ne 0) { throw "Remote directory creation failed" }

Write-Host "[3/4] Uploading non-control flow/ToF bridge"
& scp $localScript "${NanoUser}@${NanoHost}:${RemoteDirectory}/nano_flow_tof_mavlink_bridge.py"
if ($LASTEXITCODE -ne 0) { throw "Bridge upload failed" }
& ssh "${NanoUser}@${NanoHost}" "chmod 755 '$RemoteDirectory/nano_flow_tof_mavlink_bridge.py'"
if ($LASTEXITCODE -ne 0) { throw "Remote chmod failed" }

Write-Host "[4/4] Remote dry-run"
& ssh "${NanoUser}@${NanoHost}" "python3 '$RemoteDirectory/nano_flow_tof_mavlink_bridge.py' --dry-run"
if ($LASTEXITCODE -ne 0) { throw "Remote dry-run failed" }

Write-Host "DEPLOYMENT CHECK OK"
Write-Host "No serial port was opened and no live MAVLink message was sent."

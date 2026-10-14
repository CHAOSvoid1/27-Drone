param(
    [string]$AirSimRoot = "H:\VisualDrone\AirSim\AirSim-1.8.1-windows",
    [string]$VisualStudioRoot = "H:\VisualDrone\Visual Studio\Community",
    [string]$UnrealRoot = "H:\VisualDrone\Epic\unrealengine\UE_4.27",
    [string]$OutputRoot = "H:\VisualDrone\AirSim\builds-tof",
    [switch]$SkipLibraryBuild
)

$ErrorActionPreference = "Stop"

$paramsFile = Join-Path $AirSimRoot "AirLib\include\sensors\distance\DistanceSimpleParams.hpp"
$backupFile = "$paramsFile.before-configurable-tof.bak"
$distanceSensorCpp = Join-Path $AirSimRoot "Unreal\Plugins\AirSim\Source\UnrealSensors\UnrealDistanceSensor.cpp"
$distanceSensorBackup = "$distanceSensorCpp.before-object-trace-fallback.bak"
$vsDevCmd = Join-Path $VisualStudioRoot "Common7\Tools\VsDevCmd.bat"
$ueBatch = Join-Path $UnrealRoot "Engine\Build\BatchFiles"
$buildCmd = Join-Path $AirSimRoot "build.cmd"
$blocksRoot = Join-Path $AirSimRoot "Unreal\Environments\Blocks"
$packageBat = Join-Path $blocksRoot "package.bat"

foreach ($path in @($paramsFile, $distanceSensorCpp, $vsDevCmd, $ueBatch, $buildCmd, $packageBat)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required path is missing: $path"
    }
}

$sensorSource = Get-Content -LiteralPath $distanceSensorCpp -Raw
$originalRaycast = @'
    FHitResult dist_hit = FHitResult(ForceInit);
    bool is_hit = UAirBlueprintLib::GetObstacle(actor_, ned_transform_->fromLocalNed(start), ned_transform_->fromLocalNed(end), dist_hit);
'@
$fallbackRaycast = @'
    const FVector unreal_start = ned_transform_->fromLocalNed(start);
    const FVector unreal_end = ned_transform_->fromLocalNed(end);

    FHitResult dist_hit = FHitResult(ForceInit);
    bool is_hit = UAirBlueprintLib::GetObstacle(actor_, unreal_start, unreal_end, dist_hit);

    // Some imported/cooked environments do not block UE's Visibility trace
    // even though their meshes participate in scene collision. Fall back to
    // an object query so a downward range sensor can still see static terrain
    // and dynamic obstacles. The vehicle actor itself remains excluded.
    if (!is_hit) {
        FCollisionQueryParams trace_params;
        trace_params.AddIgnoredActor(actor_);

        FCollisionObjectQueryParams object_params;
        object_params.AddObjectTypesToQuery(ECC_WorldStatic);
        object_params.AddObjectTypesToQuery(ECC_WorldDynamic);

        is_hit = actor_->GetWorld()->LineTraceSingleByObjectType(
            dist_hit, unreal_start, unreal_end, object_params, trace_params);
    }
'@

if ($sensorSource.Contains('LineTraceSingleByObjectType')) {
    Write-Host "Distance sensor object-trace fallback is already present; patch skipped."
}
elseif (-not $sensorSource.Contains($originalRaycast.Trim())) {
    throw "Expected distance-sensor raycast was not found; source was not modified."
}
else {
    if (-not (Test-Path -LiteralPath $distanceSensorBackup)) {
        Copy-Item -LiteralPath $distanceSensorCpp -Destination $distanceSensorBackup
        Write-Host "Backup created: $distanceSensorBackup"
    }
    $patchedSensor = $sensorSource.Replace(
        $originalRaycast.Trim(),
        $fallbackRaycast.Trim()
    )
    [System.IO.File]::WriteAllText(
        $distanceSensorCpp,
        $patchedSensor,
        [System.Text.UTF8Encoding]::new($false)
    )
    Write-Host "Patched collision fallback: $distanceSensorCpp"
}

$source = Get-Content -LiteralPath $paramsFile -Raw

$externalLine = '            external_controller = settings_json.getBool("ExternalController", external_controller);'
$externalReplacement = @'
            external_controller = settings_json.getBool("ExternalController", external_controller);
            uncorrelated_noise_sigma = settings_json.getFloat("UncorrelatedNoiseSigma", uncorrelated_noise_sigma);
            update_latency = settings_json.getFloat("UpdateLatency", update_latency);
            update_frequency = settings_json.getFloat("UpdateFrequency", update_frequency);
            startup_delay = settings_json.getFloat("StartupDelay", startup_delay);

            uncorrelated_noise_sigma = std::max(0.0f, uncorrelated_noise_sigma);
            update_latency = std::max(0.0f, update_latency);
            update_frequency = std::max(1.0f, update_frequency);
            startup_delay = std::max(0.0f, startup_delay);
'@

if ($source.Contains('settings_json.getFloat("UncorrelatedNoiseSigma"')) {
    Write-Host "Configurable ToF parameters are already present; source patch skipped."
}
elseif (-not $source.Contains($externalLine)) {
    throw "Expected insertion point was not found; source was not modified."
}
else {
    if (-not (Test-Path -LiteralPath $backupFile)) {
        Copy-Item -LiteralPath $paramsFile -Destination $backupFile
        Write-Host "Backup created: $backupFile"
    }
    $patched = $source.Replace($externalLine, $externalReplacement.TrimEnd())
    [System.IO.File]::WriteAllText($paramsFile, $patched, [System.Text.UTF8Encoding]::new($false))
    Write-Host "Patched: $paramsFile"
}

if ($SkipLibraryBuild) {
    Write-Host "AirSim library build skipped by request."
}
else {
    $buildLine = 'call "{0}" -arch=x64 && cd /d "{1}" && call build.cmd --Release' -f $vsDevCmd, $AirSimRoot
    Write-Host "Building AirSim libraries..."
    & cmd.exe /d /s /c $buildLine
    if ($LASTEXITCODE -ne 0) {
        throw "AirSim library build failed with exit code $LASTEXITCODE"
    }
}

New-Item -ItemType Directory -Force $OutputRoot | Out-Null
# package.bat adds its own quotes around ToolPath. Passing quoted arguments makes
# it construct paths with doubled quotes, so these three paths must not contain
# spaces and are deliberately passed without an extra quote layer.
foreach ($path in @($blocksRoot, $OutputRoot, $ueBatch)) {
    if ($path -match '\s') {
        throw "package.bat cannot safely handle a spaced path here: $path"
    }
}
$packageLine = 'cd /d {0} && call package.bat {1} {2} 4.27' -f $blocksRoot, $OutputRoot, $ueBatch
Write-Host "Packaging Blocks to $OutputRoot ..."
& cmd.exe /d /s /c $packageLine
if ($LASTEXITCODE -ne 0) {
    throw "Blocks packaging failed with exit code $LASTEXITCODE"
}

$newExe = Join-Path $OutputRoot "Blocks\Blocks.exe"
if (-not (Test-Path -LiteralPath $newExe)) {
    throw "Packaging reported success but executable is missing: $newExe"
}

Write-Host "BUILD OK"
Write-Host "New executable: $newExe"
Write-Host "The existing packaged AirSim was not overwritten."

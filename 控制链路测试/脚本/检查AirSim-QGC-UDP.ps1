param(
    [int]$Port = 14550,
    [int]$DurationSeconds = 10
)

$ErrorActionPreference = 'Stop'
$udp = [System.Net.Sockets.UdpClient]::new($Port)
$udp.Client.ReceiveTimeout = 500
$remote = [System.Net.IPEndPoint]::new([System.Net.IPAddress]::Any, 0)
$deadline = [DateTime]::UtcNow.AddSeconds($DurationSeconds)
$packets = 0
$bytes = 0
$sources = @{}

Write-Host "Listening for AirSim MAVLink UDP on 0.0.0.0:$Port for $DurationSeconds seconds..."

try {
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $data = $udp.Receive([ref]$remote)
            $packets++
            $bytes += $data.Length
            $source = "$($remote.Address):$($remote.Port)"
            $sources[$source] = 1 + [int]($sources[$source])

            if ($packets -le 5) {
                $prefix = ($data | Select-Object -First 12 | ForEach-Object { $_.ToString('X2') }) -join ' '
                Write-Host "packet=$packets bytes=$($data.Length) from=$source prefix=$prefix"
            }
        }
        catch [System.Net.Sockets.SocketException] {
            if ($_.Exception.SocketErrorCode -ne [System.Net.Sockets.SocketError]::TimedOut) {
                throw
            }
        }
    }
}
finally {
    $udp.Dispose()
}

Write-Host "Summary: packets=$packets bytes=$bytes"
$sources.GetEnumerator() | Sort-Object Name | ForEach-Object {
    Write-Host "source=$($_.Name) packets=$($_.Value)"
}

if ($packets -eq 0) {
    Write-Error "No UDP packets received on port $Port."
}
else {
    Write-Host 'UDP FORWARDING OK'
}

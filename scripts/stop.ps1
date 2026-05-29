# trispoke-outbound — kill all components (PowerShell).
# Matches by command line so we don't accidentally kill unrelated python processes.

$patterns = @(
    'trispoke\.receiver\.imap_poller',
    'trispoke\.sender\.sender_loop',
    'src[\\/]trispoke[\\/]ui[\\/]app\.py'
)

$regex = ($patterns -join '|')

$procs = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and ($_.CommandLine -match $regex)
}

if (-not $procs) {
    Write-Host "No trispoke processes running."
    exit 0
}

foreach ($p in $procs) {
    Write-Host "Stopping PID $($p.ProcessId): $($p.CommandLine)"
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
    } catch {
        Write-Warning "Could not stop PID $($p.ProcessId): $_"
    }
}

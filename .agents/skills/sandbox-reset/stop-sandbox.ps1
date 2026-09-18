# Stops the throwaway first-run sandbox on port 8145.
#
# 8145 is the sandbox's port by construction (run-sandbox.ps1 sets MC_PORT),
# and the real journal lives on 8144 -- this script only ever looks at 8145.
# It stops python processes only: the launcher's powershell is usually the
# user's own terminal, and killing that closes their window.
param([switch]$DryRun)

$Port = 8145

function Get-Holders {
  @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique)
}

$holders = Get-Holders
if (-not $holders) {
  Write-Host "8145: nothing listening"
  exit 0
}

foreach ($id in $holders) {
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$id"
  if ($proc -and $proc.Name -notlike 'python*') {
    throw "8145 is held by $($proc.Name) (PID $id), not a python server -- refusing to stop something the sandbox did not start"
  }
}

# Ask the server what it is before stopping it, so the report can say whether
# this was the wizard, a configured journal or the demo instance.
try {
  $s = Invoke-RestMethod "http://127.0.0.1:$Port/api/status" -TimeoutSec 3
  $what = "configured=$($s.configured) seed_instance=$($s.seed_instance) entries=$($s.entries) demo_built=$($s.demo_built)"
} catch {
  $what = "status unreadable"
}
Write-Host "8145: PID $($holders -join ', ') -- $what"

if ($DryRun) {
  Write-Host "dry run: nothing stopped"
  exit 0
}

# A demo restart hands the port from one process to a freshly spawned child,
# so a stop can land mid-handoff and a new holder appear a beat later. Keep
# stopping whatever holds the port until it stays free.
$deadline = (Get-Date).AddSeconds(10)
do {
  foreach ($id in (Get-Holders)) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$id"
    if (-not $proc -or $proc.Name -notlike 'python*') { continue }
    Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
    Write-Host "stopped PID $id"
    # The venv's python.exe is a shim that spawns the real interpreter; stop
    # the shim too so nothing is left waiting on a dead child.
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($proc.ParentProcessId)"
    if ($parent -and $parent.Name -like 'python*') {
      Stop-Process -Id $parent.ProcessId -Force -ErrorAction SilentlyContinue
      Write-Host "stopped PID $($parent.ProcessId) (venv shim)"
    }
  }
  Start-Sleep -Milliseconds 1500
} while ((Get-Holders) -and (Get-Date) -lt $deadline)

$left = Get-Holders
if ($left) {
  Write-Host "8145 is STILL held by PID $($left -join ', ')"
  exit 1
}
Write-Host "8145: free"

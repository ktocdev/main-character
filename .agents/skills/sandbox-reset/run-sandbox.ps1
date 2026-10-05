# Throwaway sandbox for testing the first-run wizard and the demo flow.
# Its own data dirs, its own .env, port 8145 -- the real journal on 8144 is
# untouched and can run at the same time.
#
# Starts from a clean first run by default: no key, no journal, no demo, so
# the wizard opens and the demo build is paid for again. Pass -Keep to leave
# the last run in place, which is what you want when what you are testing
# takes two sittings (build the demo once, then test switching back to it).
param([switch]$Keep)

$ErrorActionPreference = "Stop"

$Sandbox = Join-Path $env:TEMP "mc-wizard"
# The repo is wherever this script is checked out: .claude\skills\<name>\
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path

# The sync below copies tracked files -- this script included -- into the
# sandbox. That copy would take the sandbox for the repo, so refuse to run it.
if ($Repo -eq $Sandbox -or -not (Test-Path (Join-Path $Repo ".git"))) {
  throw "run the repo's copy of this script, not the one synced into the sandbox ($Repo is not a git checkout)"
}
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "no virtualenv at $Python -- create .venv in the repo first" }

# Two guards, both learned the hard way.
#
# The path one: this script deletes journals, and the only journal it may
# ever delete is this throwaway one.
if ((Split-Path $Sandbox -Leaf) -ne "mc-wizard" -or
    -not $Sandbox.StartsWith($env:TEMP)) {
  throw "refusing to reset $Sandbox -- this script only resets the mc-wizard sandbox under TEMP"
}

if (-not $Keep) {
  # The port one: a running server holds its Chroma files open, so a wipe
  # underneath it half-succeeds -- it takes the markdown and leaves the
  # locked index, and what is left is a journal reporting 29 entries with
  # nothing behind them. Refuse rather than half-delete.
  $live = Get-NetTCPConnection -State Listen -LocalPort 8145 -ErrorAction SilentlyContinue
  if ($live) {
    throw "8145 is still serving (PID $($live.OwningProcess)). Stop it first (stop-sandbox.ps1) -- a wipe under a running server deletes the entries and leaves the locked index."
  }
  foreach ($path in @("$Sandbox\.env", "$Sandbox\_d", "$Sandbox\seed_corpus\install")) {
    if (Test-Path $path) {
      Write-Host "reset: removing $path"
      Remove-Item -Recurse -Force $path
    }
  }
  Get-ChildItem "$Sandbox\*.log" -ErrorAction SilentlyContinue | Remove-Item -Force
  Write-Host "reset: clean first run -- no key, no journal, no demo"
} else {
  Write-Host "keeping what the last run left behind (-Keep)"
}

# Bring the code forward. The sandbox is a copy rather than a checkout, and
# testing yesterday's copy of a file changed five minutes ago is a way to
# spend an afternoon. Tracked files only: .env and the data dirs are not
# tracked, which is exactly what makes this safe to run on every start -- and
# it is the same set a stranger gets when they clone. Also builds the sandbox
# from nothing if TEMP was cleaned out.
$tracked = git -C $Repo ls-files
$copied = 0
foreach ($rel in $tracked) {
  $src = Join-Path $Repo $rel
  $dst = Join-Path $Sandbox $rel
  if (-not (Test-Path $src)) { continue }
  $dir = Split-Path $dst -Parent
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
  $newer = $true
  if (Test-Path $dst) {
    $newer = (Get-Item $src).LastWriteTimeUtc -gt (Get-Item $dst).LastWriteTimeUtc
  }
  if ($newer) { Copy-Item $src $dst -Force; $copied++ }
}
Write-Host "code: $($tracked.Count) tracked file(s), $copied refreshed"

$Data = Join-Path $Sandbox "_d"
$env:MC_PORT         = "8145"
$env:MC_JOURNAL_DIR  = Join-Path $Data "journal_entries"
$env:MC_CHROMA_DIR   = Join-Path $Data "chroma_data"
$env:MC_ENTITY_DIR   = Join-Path $Data "entity_graph"
$env:MC_SUMMARY_DIR  = Join-Path $Data "summaries"
$env:MC_CATEGORY_DIR = Join-Path $Data "categories"
$env:MC_PATTERN_DIR  = Join-Path $Data "patterns"
$env:MC_DREAM_DIR    = Join-Path $Data "dreams"
$env:MC_SESSION_DIR  = Join-Path $Data "sessions"

# The whole point: no key, so the journal boots unconfigured and the wizard
# opens. Scoped to this script's own process, so nothing leaks back into the
# shell it was started from.
Remove-Item Env:\ANTHROPIC_API_KEY -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "http://127.0.0.1:8145"
Set-Location $Sandbox
& $Python server.py

# bootstrap.ps1 - fetches a private, embeddable Python for Mark 6.
#
# Same technique FreeClaw's own install.ps1 uses for itself: the embeddable
# distribution from python.org, unpacked under this install with no other
# Python on the machine touched and nothing added to PATH. Mark 6 has zero
# third-party dependencies anywhere in the app, so unlike FreeClaw's own
# installer this never has to bootstrap pip - the interpreter alone is
# everything the app needs.
#
# Called by run.bat exactly once, the first time it finds no
# runtime\python.exe here. Every run after that launches straight from the
# already-bundled copy - no network, no PowerShell, nothing to wait on.

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $Here "runtime"
# Matches the version FreeClaw's own install.ps1 already trusts.
$PythonVersion = "3.12.8"

function Die($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

if (Test-Path (Join-Path $RuntimeDir "python.exe")) { exit 0 }

if (-not [Environment]::Is64BitOperatingSystem) {
    Die "Mark 6 needs 64-bit Windows."
}

# Same fallback order FreeClaw's installer uses: PROCESSOR_ARCHITEW6432 holds
# the machine's real architecture even when this script happens to be running
# under a 32-bit PowerShell on an ARM64 box, where PROCESSOR_ARCHITECTURE
# alone would read "x86" and fetch the wrong build.
$machineArch = $env:PROCESSOR_ARCHITEW6432
if (-not $machineArch) { $machineArch = $env:PROCESSOR_ARCHITECTURE }
switch ("$machineArch".ToUpperInvariant()) {
    "AMD64" { $PyArch = "amd64" }
    "ARM64" { $PyArch = "arm64" }
    default { Die "Unsupported processor architecture: $machineArch (need x64 or ARM64)." }
}

# Windows PowerShell defaults to SSL3/TLS1.0, which python.org refuses.
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { }

Write-Host "Fetching a private Python $PythonVersion ($PyArch) for Mark 6..."
$zipName = "python-$PythonVersion-embed-$PyArch.zip"
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("mark6-py-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
$zipPath = Join-Path $tmp $zipName
try {
    Invoke-WebRequest "https://www.python.org/ftp/python/$PythonVersion/$zipName" `
        -OutFile $zipPath -UseBasicParsing
} catch {
    Die "Couldn't download Python: $($_.Exception.Message)"
}

New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $RuntimeDir)
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

# Nothing else to do: the embeddable build's ._pth restricts sys.path at
# *startup* (no site-packages, no registry lookups), but that has no bearing
# on the running interpreter - src\cli.py inserts its own directory onto
# sys.path before importing the mark6 package, which is ordinary Python and
# works the same whether the interpreter is this private copy or any other.
# Mark 6 has no third-party dependencies either, so unlike FreeClaw's own
# installer this never needs "import site" or a pip bootstrap - the stdlib
# the zip already ships is everything the app uses.

Write-Host "Done."

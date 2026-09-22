# bootstrap.ps1 - fetches a private, embeddable Python for Mark 6.
#
# Same technique FreeClaw's own install.ps1 uses for itself: the embeddable
# distribution from python.org, unpacked under this install with no other
# Python on the machine touched and nothing added to PATH.
#
# On top of the bare interpreter it adds the two things the window needs:
#
#   * tkinter, which the embeddable zip leaves out. python.org publishes it
#     as its own tcltk.msi beside the full installer; an *administrative*
#     install (msiexec /a) just unpacks those files into a folder - nothing
#     registered, nothing in Add/Remove Programs, no elevation - and they are
#     copied in from there.
#   * pip, and with it the bundled computer-use MCP server (realhands, from
#     github.com/kanishka089/computer-use-mcp). Mark 6 itself still uses
#     nothing outside the stdlib; realhands is the only thing pip is for.
#
# Called by run.bat whenever runtime\ is missing its stamp file. Every step
# checks whether it is already done, so a runtime\ left by an older version
# of this script is topped up rather than downloaded again. Every run after
# that launches straight from the private copy - no network, nothing to wait on.

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $Here "runtime"
$Python = Join-Path $RuntimeDir "python.exe"
# run.bat looks for this; bump both together when the runtime gains a step.
$Stamp = Join-Path $RuntimeDir "mark6-runtime-2.ok"
# Matches the version FreeClaw's own install.ps1 already trusts.
$PythonVersion = "3.12.8"

function Die($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

function Fetch($url, $out) {
    try {
        Invoke-WebRequest $url -OutFile $out -UseBasicParsing
    } catch {
        Die "Couldn't download $url : $($_.Exception.Message)"
    }
}

if (Test-Path $Stamp) { exit 0 }

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

$tmp = Join-Path ([IO.Path]::GetTempPath()) ("mark6-py-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null

try {
    # -- 1. The interpreter ------------------------------------------------
    if (-not (Test-Path $Python)) {
        Write-Host "Fetching a private Python $PythonVersion ($PyArch) for Mark 6..."
        $zipName = "python-$PythonVersion-embed-$PyArch.zip"
        $zipPath = Join-Path $tmp $zipName
        Fetch "https://www.python.org/ftp/python/$PythonVersion/$zipName" $zipPath
        New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $RuntimeDir)
    }

    # -- 2. tkinter, for the window -----------------------------------------
    if (-not (Test-Path (Join-Path $RuntimeDir "_tkinter.pyd"))) {
        Write-Host "Adding tkinter, for the Mark 6 window..."
        $msi = Join-Path $tmp "tcltk.msi"
        Fetch "https://www.python.org/ftp/python/$PythonVersion/$PyArch/tcltk.msi" $msi
        $unpacked = Join-Path $tmp "tcltk"
        $p = Start-Process msiexec.exe -Wait -PassThru -ArgumentList @(
            "/a", "`"$msi`"", "/qn", "TARGETDIR=`"$unpacked`"")
        if ($p.ExitCode -ne 0) { Die "Couldn't unpack tcltk.msi (msiexec exit $($p.ExitCode))." }
        # _tkinter.pyd and the Tcl/Tk DLLs go beside python.exe, where the
        # embeddable build already keeps every other extension module. The
        # tcl\ folder goes at the root because _tkinter looks for
        # <sys.prefix>\tcl\tcl8.6 on Windows before anywhere else.
        Copy-Item (Join-Path $unpacked "DLLs\*") $RuntimeDir -Force
        New-Item -ItemType Directory -Path (Join-Path $RuntimeDir "Lib") -Force | Out-Null
        Copy-Item (Join-Path $unpacked "Lib\tkinter") (Join-Path $RuntimeDir "Lib") -Recurse -Force
        Copy-Item (Join-Path $unpacked "tcl") $RuntimeDir -Recurse -Force
    }

    # -- 3. sys.path ---------------------------------------------------------
    # The embeddable build's ._pth fixes sys.path at startup. Lib\ is where
    # tkinter now lives, and "import site" turns on Lib\site-packages, which
    # is where pip puts realhands and its dependencies.
    $pth = Get-ChildItem $RuntimeDir -Filter "python*._pth" | Select-Object -First 1
    if (-not $pth) { Die "The private Python has no ._pth file; delete runtime\ and try again." }
    $zipLib = $pth.BaseName + ".zip"
    Set-Content -Path $pth.FullName -Encoding ascii -Value @($zipLib, ".", "Lib", "", "import site")

    # -- 4. pip, and the bundled computer-use server --------------------------
    if (-not (Test-Path (Join-Path $RuntimeDir "Lib\site-packages\pip"))) {
        Write-Host "Adding pip..."
        $getPip = Join-Path $tmp "get-pip.py"
        Fetch "https://bootstrap.pypa.io/get-pip.py" $getPip
        & $Python $getPip --no-warn-script-location --disable-pip-version-check -q
        if ($LASTEXITCODE -ne 0) { Die "Couldn't install pip." }
    }

    if (-not (Test-Path (Join-Path $RuntimeDir "Lib\site-packages\realhands"))) {
        Write-Host "Installing the computer-use MCP server (realhands)..."
        # A few of realhands' dependencies (pyautogui and friends) ship only
        # as source. pip's usual isolated build environment reaches its build
        # tools through PYTHONPATH, which an embeddable ._pth ignores - so
        # setuptools goes in first and the build runs without isolation.
        & $Python -m pip install -q --no-warn-script-location --disable-pip-version-check setuptools wheel
        if ($LASTEXITCODE -ne 0) { Die "Couldn't install setuptools." }
        & $Python -m pip install -q --no-warn-script-location --disable-pip-version-check `
            --no-build-isolation realhands
        if ($LASTEXITCODE -ne 0) { Die "Couldn't install realhands." }
    }

    Set-Content -Path $Stamp -Encoding ascii -Value "ok"
} finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "Done."

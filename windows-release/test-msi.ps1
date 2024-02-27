param ([string]$SetupExe)

Write-Host "##[section]Install Python"
$SetupArgs = "$SetupExe " + `
            "/passive /log ""C:\Logs\install\log.txt"" " + `
            "TargetDir=C:\Python " + `
            "Include_debug=1 " + `
            "Include_symbols=1 " + `
            "InstallAllUsers=${env:InstallAllUsers} " + `
            "${env:IncludeFreethreadedOpt}"
Write-Host "##[command]$SetupCmd"
iex $SetupCmd
if (!$?) { exit $LASTEXITCODE }

Write-Host "##[command]dir C:\Python"
dir C:\Python

$env:PATH = "C:\Python:${env:PATH}"


Write-Host "##[section]Capture Start Menu items"
Write-Host "##[command]dir -r ""${env:PROGRAMDATA}\Microsoft\Windows\Start Menu\Programs\Python*"""
dir -r "${env:PROGRAMDATA}\Microsoft\Windows\Start Menu\Programs\Python*"

Write-Host "##[command]dir -r ""${env:APPDATA}\Microsoft\Windows\Start Menu\Programs\Python*"""
dir -r "${env:APPDATA}\Microsoft\Windows\Start Menu\Programs\Python*"

Write-Host "##[section]Capture registry"
Write-Host 'Capture per-machine 32-bit registry'
Write-Host "##[command]dir -r ""HKLM:\Software\WOW6432Node\Python"""
dir -r "HKLM:\Software\WOW6432Node\Python"

Write-Host 'Capture per-machine native registry'
Write-Host "##[command]dir -r ""HKLM:\Software\Python"""
dir -r "HKLM:\Software\Python"

Write-Host 'Capture current-user registry'
Write-Host "##[command]dir -r ""HKCU:\Software\Python"""
dir -r "HKCU:\Software\Python"


if (-not $env:SkipTests) {
    Write-Host "##[section]Smoke tests"
    Write-Host "##[command]python -c ""import sys; print(sys.version)"""
    python -c "import sys; print(sys.version)"
    if (!$?) { exit $LASTEXITCODE }
    Write-Host "##[command]python -m site"
    python -m site
    if (!$?) { exit $LASTEXITCODE }

    if ($env:IncludeFreethreadedOpt) {
        $p = (gci "C:\Python\python3*t.exe" | select -First 1)
        if (-not $p) {
            Write-Host "Did not find python3*t.exe in:"
            dir "C:\Python"
            throw "Free-threaded binaries were not installed"
        }
        Write-Host "Found free threaded executable $p"
        Write-Host "##[command]$p -c ""import sys; print(sys.version)"""
        & $p -c "import sys; print(sys.version)"
        if (!$?) { exit $LASTEXITCODE }
    }

    Write-Host "##[section]Test (un)install package"
    Write-Host "##[command]python -m pip install ""azure<0.10"""
    python -m pip install "azure<0.10"
    if (!$?) { exit $LASTEXITCODE }
    Write-Host "##[command]python -m pip uninstall -y azure python-dateutil six"
    python -m pip uninstall -y azure python-dateutil six
    if (!$?) { exit $LASTEXITCODE }

    if (-not $env:SkipTkTests) {
        Write-Host "##[section]Test Tkinter and Idle"
        if (Test-Path -Type Container "C:\Python\Lib\test\test_ttk") {
            # New set of tests (3.12 and later)
            Write-Host "##[command]python -m test -uall -v test_ttk test_tkinter test_idle"
            python -m test -uall -v test_ttk test_tkinter test_idle
            if (!$?) { exit $LASTEXITCODE }
        } else {
            # Old set of tests
            Write-Host "##[command]python -m test -uall -v test_ttk_guionly test_tk test_idle"
            python -m test -uall -v test_ttk_guionly test_tk test_idle
            if (!$?) { exit $LASTEXITCODE }
        }
    }
}

Write-Host "##[section]Uninstall Python"
$UninstallCmd = "$(SetupExe) /passive /uninstall /log C:\Logs\uninstall\log.txt"
Write-Host "##[command]$UninstallCmd"
iex $UninstallCmd
if (!$?) { exit $LASTEXITCODE }

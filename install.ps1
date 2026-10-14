# Lumus demo installer for Windows (PowerShell 5.1 or 7). Safe to re-run. No admin rights, nothing is deleted, no secrets are read or written.
#   irm https://raw.githubusercontent.com/Lumus-ai/lumus-code/demo/install.ps1 | iex
# What it does:
#   1. clones (or fast-forward updates) the `demo` branch into %USERPROFILE%\lumus-code-demo
#   2. makes a private Python venv in %LOCALAPPDATA%\lumus-demo\venv and installs playwright there
#   3. creates %USERPROFILE%\.keys\hands.txt (empty, for your own Gemini API key; optional, the page can do it for you)
#   4. writes a `lumus-demo.cmd` launcher into %USERPROFILE%\.local\bin and adds that folder to YOUR user PATH (no admin)
# Needs: Git and Python 3.9+ (winget install Git.Git ; winget install Python.Python.3.12). Test hooks: LUMUS_DEMO_REPO / _DIR / _VENV / _BIN, LUMUS_DEMO_NO_PATH=1.
$ErrorActionPreference = "Continue"
$IsWin = ($env:OS -eq "Windows_NT")
$HomeDir = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
function Pick($v, $d) { if ($v) { $v } else { $d } }
$Repo = Pick $env:LUMUS_DEMO_REPO "https://github.com/Lumus-ai/lumus-code.git"
$Dir  = Pick $env:LUMUS_DEMO_DIR (Join-Path $HomeDir "lumus-code-demo")
$DataBase = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HomeDir ".local/share" }
$Venv = Pick $env:LUMUS_DEMO_VENV (Join-Path $DataBase "lumus-demo/venv")
$Bin  = Pick $env:LUMUS_DEMO_BIN (Join-Path $HomeDir ".local/bin")
$Keys = Join-Path $HomeDir ".keys"

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; throw $msg }

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "Git is required. Install it:  winget install Git.Git   (then open a new PowerShell window and run this again)" }

# find a working Python 3 (the Microsoft Store stub named python.exe does not count)
$Py = $null
foreach ($cand in @(@("py", "-3"), @("python"), @("python3"))) {
  if (-not (Get-Command $cand[0] -ErrorAction SilentlyContinue)) { continue }
  $extra = @(); if ($cand.Length -gt 1) { $extra = $cand[1..($cand.Length - 1)] }
  $out = & $cand[0] @extra -c "import sys; print(sys.version_info[0] * 100 + sys.version_info[1])" 2>$null
  if ($LASTEXITCODE -eq 0 -and $out -and ([int]$out -ge 309)) { $Py = $cand; break }
}
if (-not $Py) { Fail "Python 3.9 or newer is required. Install it:  winget install Python.Python.3.12   (then open a new PowerShell window and run this again)" }
$PyExe = $Py[0]; $PyArgs = @(); if ($Py.Length -gt 1) { $PyArgs = $Py[1..($Py.Length - 1)] }

Write-Host "==> 1/4 code: $Dir"
if (Test-Path (Join-Path $Dir ".git")) {
  git -C $Dir pull --ff-only
  if ($LASTEXITCODE -ne 0) { Write-Host "WARN: could not fast-forward $Dir (local changes?). Leaving it as is." }
} else {
  git clone --depth 1 -b demo $Repo $Dir
  if ($LASTEXITCODE -ne 0) { Fail "git clone failed" }
}

Write-Host "==> 2/4 python venv with playwright: $Venv"
$VenvPy = Join-Path $Venv "Scripts/python.exe"; if (-not (Test-Path $VenvPy)) { $VenvPy = Join-Path $Venv "bin/python" }
if (-not (Test-Path $VenvPy)) {
  & $PyExe @PyArgs -m venv $Venv
  if ($LASTEXITCODE -ne 0) { Fail "python -m venv failed" }
  $VenvPy = Join-Path $Venv "Scripts/python.exe"; if (-not (Test-Path $VenvPy)) { $VenvPy = Join-Path $Venv "bin/python" }
}
& $VenvPy -m pip install --quiet --upgrade playwright    # attaches to your own Chrome, no browser download
if ($LASTEXITCODE -ne 0) { Fail "pip install playwright failed" }

Write-Host "==> 3/4 key file: $Keys"
New-Item -ItemType Directory -Force -Path $Keys | Out-Null
$HandsKey = Join-Path $Keys "hands.txt"
if (-not (Test-Path $HandsKey)) { New-Item -ItemType File -Path $HandsKey | Out-Null }

Write-Host "==> 4/4 launcher: $Bin\lumus-demo.cmd"
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
$Launcher = @'
@echo off
chcp 65001 >nul
setlocal
rem lumus-demo launcher (written by install.ps1). Usage: lumus-demo [--choose] | ui | chrome|init|run|brain-prompt|brain-reply|step|status|report|update
set "DIR=@@DIR@@"
set "VENV=@@VENV@@"
set "PYTHONUTF8=1"
if not defined LUMUS_HANDS_URL set "LUMUS_HANDS_URL=https://generativelanguage.googleapis.com/v1beta/openai"
if not defined LUMUS_HANDS_MODEL set "LUMUS_HANDS_MODEL=gemini-3.1-flash-lite"
if not defined LUMUS_HANDS_KEY_FILE set "LUMUS_HANDS_KEY_FILE=%USERPROFILE%\.keys\hands.txt"
set "PY=%VENV%\Scripts\python.exe"
if /I "%~1"=="update" ( git -C "%DIR%" pull --ff-only & exit /b %errorlevel% )
if "%~1"=="" ( "%PY%" "%DIR%\script\lumus-loop.py" start & exit /b %errorlevel% )
if /I "%~1"=="--choose" ( "%PY%" "%DIR%\script\lumus-loop.py" start --choose & exit /b %errorlevel% )
if /I "%~1"=="run" goto :needkey
if /I "%~1"=="step" goto :needkey
if /I "%~1"=="hands" goto :needkey
goto :go
:needkey
set "KSIZE=0"
for %%F in ("%LUMUS_HANDS_KEY_FILE%") do set "KSIZE=%%~zF"
if not "%KSIZE%"=="0" goto :go
"%PY%" "%DIR%\script\lumus_providers.py" has-active
if not errorlevel 1 goto :go
echo No model for the hands yet. Run: lumus-demo ui  (the page walks you through it in a minute), or paste a Gemini key into %LUMUS_HANDS_KEY_FILE%. 1>&2
exit /b 1
:go
"%PY%" "%DIR%\script\lumus-loop.py" %*
exit /b %errorlevel%
'@
$Launcher = $Launcher.Replace("@@DIR@@", $Dir).Replace("@@VENV@@", $Venv)
$LauncherPath = Join-Path $Bin "lumus-demo.cmd"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($LauncherPath, ($Launcher -replace "`r?`n", "`r`n"), $Utf8NoBom)

$pathNote = ""
if ($IsWin -and $env:LUMUS_DEMO_NO_PATH -ne "1") {
  $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
  $parts = @(); if ($userPath) { $parts = $userPath.Split(";") | Where-Object { $_ } }
  if (-not ($parts | Where-Object { $_.TrimEnd("\") -ieq $Bin.TrimEnd("\") })) {
    [Environment]::SetEnvironmentVariable("Path", (($parts + $Bin) -join ";"), "User")
    $pathNote = "Added $Bin to your user PATH. Open a NEW terminal window for it to take effect."
  }
}

Write-Host ""
Write-Host "Done."
if ($pathNote) { Write-Host $pathNote }
Write-Host "Start:   lumus-demo        (first launch asks: local chat page or terminal; the page walks you through connecting a free model, no keys in files by hand)"
Write-Host "Update:  lumus-demo update"
Write-Host "Brain Chrome (for the terminal mode): lumus-demo chrome"

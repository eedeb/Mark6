@echo off
rem Mark 6 - run this to pair your computer and lend it MCP tools.
rem
rem No Node.js, no Python, nothing to install first. The first run fetches a
rem private copy of Python (python.org's embeddable distribution, ~15MB) into
rem runtime\ beside this file and never touches anything already on the
rem machine or PATH - the same trick FreeClaw's own installer uses for itself.
rem Every run after that starts instantly, straight from that private copy.
rem
rem %* forwards your arguments (login, add, run, ...) with their quoting intact.
rem
rem Keep this file ASCII with CRLF line endings - cmd.exe reads a batch file in
rem the OEM codepage and mis-parses a bare LF, which turns these rem lines into
rem commands it then tries to run. .gitattributes pins the line endings.

setlocal
set "HERE=%~dp0"

if not exist "%HERE%runtime\python.exe" (
    where powershell >nul 2>nul
    if errorlevel 1 (
        echo Mark 6 needs PowerShell to fetch its private Python on first run.
        echo PowerShell ships with Windows 10 and later - if this machine
        echo somehow lacks it, install Python 3.9+ yourself and run:
        echo     python src\cli.py %*
        exit /b 1
    )
    powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%bin\bootstrap.ps1"
    if errorlevel 1 exit /b 1
)

"%HERE%runtime\python.exe" "%HERE%src\cli.py" %*
set MARK6_EXIT=%ERRORLEVEL%
endlocal & exit /b %MARK6_EXIT%

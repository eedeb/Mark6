@echo off
rem Mark 6 - run this to pair your computer and lend it MCP tools.
rem
rem Double-click it (no arguments) and it opens the Mark 6 window. Give it a
rem command (login, add, run, ...) and it is the terminal build instead.
rem
rem No Node.js, no Python, nothing to install first. The first run fetches a
rem private copy of Python (python.org's embeddable distribution) plus
rem tkinter and the bundled computer-use MCP server into runtime\ beside this
rem file, and never touches anything already on the machine or PATH - the
rem same trick FreeClaw's own installer uses for itself. Every run after that
rem starts instantly, straight from that private copy.
rem
rem %* forwards your arguments (login, add, run, ...) with their quoting intact.
rem
rem Keep this file ASCII with CRLF line endings - cmd.exe reads a batch file in
rem the OEM codepage and mis-parses a bare LF, which turns these rem lines into
rem commands it then tries to run. .gitattributes pins the line endings.

setlocal
set "HERE=%~dp0"

rem The stamp name matches $Stamp in bin\bootstrap.ps1.
if not exist "%HERE%runtime\mark6-runtime-3.ok" (
    where powershell >nul 2>nul
    if errorlevel 1 (
        echo Mark 6 needs PowerShell to fetch its private Python on first run.
        echo PowerShell ships with Windows 10 and later - if this machine
        echo somehow lacks it, install Python 3.9+ yourself and run:
        echo     python src\cli.py %*
        exit /b 1
    )
    powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%bin\bootstrap.ps1"
    if errorlevel 1 (
        pause
        exit /b 1
    )
)

rem -s keeps out the user site-packages (AppData\Roaming\Python\Python312) of any
rem other Python 3.12 on this machine. The embeddable build still adds it,
rem and ignores PYTHONNOUSERSITE, so the flag is the only off switch.
rem
rem No arguments: the window. pythonw has no console, and `start` returns at
rem once, so the console this was double-clicked from closes behind it.
if "%~1"=="" (
    start "" "%HERE%runtime\pythonw.exe" -s "%HERE%src\gui.py"
    endlocal & exit /b 0
)

"%HERE%runtime\python.exe" -s "%HERE%src\cli.py" %*
set MARK6_EXIT=%ERRORLEVEL%
endlocal & exit /b %MARK6_EXIT%

# W2 — build NEXUS's MCP bundle with the repo's interpreter.
# scripts/build_bundle.py says what it does; its arguments pass through
# (--check, --twice, --write-icon; for example .\scripts\build_bundle.ps1 --twice).
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
$py = if (Test-Path '.venv\Scripts\python.exe') { '.venv\Scripts\python.exe' } else { 'python' }
& $py scripts/build_bundle.py @args
exit $LASTEXITCODE

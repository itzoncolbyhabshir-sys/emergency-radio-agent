$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$backendEnv = @"
`$env:MOCK_MODE = 'true'
`$env:NO_GPU = 'true'
Set-Location "$Backend"
python -m uvicorn server:app --host 127.0.0.1 --port 8010
"@

$frontendEnv = @"
`$env:VITE_API_TARGET = 'http://127.0.0.1:8010'
`$env:VITE_WS_TARGET = 'ws://127.0.0.1:8010'
`$env:VITE_WS_URL = 'ws://127.0.0.1:8010/ws'
Set-Location "$Frontend"
npm.cmd run dev -- --host 127.0.0.1 --port 3012
"@

$backend = Start-Process -FilePath powershell.exe -ArgumentList @("-NoProfile", "-Command", $backendEnv) -WindowStyle Hidden -RedirectStandardOutput (Join-Path $LogDir "backend.out.log") -RedirectStandardError (Join-Path $LogDir "backend.err.log") -PassThru
$frontend = Start-Process -FilePath powershell.exe -ArgumentList @("-NoProfile", "-Command", $frontendEnv) -WindowStyle Hidden -RedirectStandardOutput (Join-Path $LogDir "frontend.out.log") -RedirectStandardError (Join-Path $LogDir "frontend.err.log") -PassThru

[pscustomobject]@{
    BackendPid = $backend.Id
    FrontendPid = $frontend.Id
    BackendUrl = "http://127.0.0.1:8010"
    FrontendUrl = "http://127.0.0.1:3012"
}

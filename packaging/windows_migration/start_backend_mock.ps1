$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (Test-Path ".venv\Scripts\Activate.ps1") {
    . .\.venv\Scripts\Activate.ps1
}

$env:MOCK_MODE = "true"
$env:ASR_BACKEND = "mock"
$env:SDR_SOURCE = "mock"
if (-not $env:RTL_FREQUENCY_MHZ) { $env:RTL_FREQUENCY_MHZ = "409.7500" }

Write-Host "启动无 SDR 保底演示模式"
Write-Host "浏览器访问: http://127.0.0.1:8000/#/"
Set-Location "$Root\backend"
python -m uvicorn server:app --host 127.0.0.1 --port 8000


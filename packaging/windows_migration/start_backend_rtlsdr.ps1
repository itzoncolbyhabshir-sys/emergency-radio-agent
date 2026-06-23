$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (Test-Path ".venv\Scripts\Activate.ps1") {
    . .\.venv\Scripts\Activate.ps1
}

Remove-Item Env:MOCK_MODE -ErrorAction SilentlyContinue
Remove-Item Env:NO_GPU -ErrorAction SilentlyContinue

$env:SDR_SOURCE = "rtl"
if (-not $env:RTL_FREQUENCY_MHZ) { $env:RTL_FREQUENCY_MHZ = "409.7500" }
if (-not $env:RTL_CHANNEL_ID) { $env:RTL_CHANNEL_ID = "ch-public-test" }
if (-not $env:MODEL_SIZE) { $env:MODEL_SIZE = "medium" }

Write-Host "启动真实 RTL-SDR 输入，频率: $env:RTL_FREQUENCY_MHZ MHz"
Write-Host "浏览器访问: http://127.0.0.1:8000/#/"
Set-Location "$Root\backend"
python -m uvicorn server:app --host 127.0.0.1 --port 8000


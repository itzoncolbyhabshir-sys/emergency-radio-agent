$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "[1/4] 准备 Python 虚拟环境..."
if (-not (Test-Path ".venv\Scripts\Activate.ps1")) {
    python -m venv .venv
}
. .\.venv\Scripts\Activate.ps1

Write-Host "[2/4] 安装后端依赖..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

Write-Host "[3/4] 安装前端依赖..."
Set-Location "$Root\frontend"
npm install

Write-Host "[4/4] 构建前端..."
npm run build

Set-Location $Root
Write-Host "安装完成。真实 SDR 启动: .\start_backend_rtlsdr.ps1；无设备演示: .\start_backend_mock.ps1"


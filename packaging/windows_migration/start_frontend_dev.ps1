$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location "$Root\frontend"

if (-not (Test-Path "node_modules")) {
    npm install
}

Write-Host "启动前端开发服务器: http://127.0.0.1:3000/#/"
npm run dev -- --host 127.0.0.1 --port 3000


@echo off
setlocal
cd /d "%~dp0backend"
set MOCK_MODE=false
set SDR_SOURCE=rtl
set RTL_FREQUENCY_MHZ=438.500
set RTL_CHANNEL_ID=ch-main
set ASR_BACKEND=whisper
set MODEL_SIZE=medium
set COMPUTE_TYPE=int8_float16
set STORAGE_PATH=./data/live-demo.sqlite3
title Emergency Radio Agent - RTL-SDR Live Demo
python -m uvicorn server:app --host 127.0.0.1 --port 8000
pause

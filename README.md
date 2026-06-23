# 应急电台智能体 · Emergency Radio Agent

> SDR + Whisper + LED 大屏 | 应急通讯智能识别与指挥系统

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![Vue](https://img.shields.io/badge/Vue-3.4+-green.svg)](https://vuejs.org)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## 架构概览

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   RTL-SDR   │────▶│   GQRX/     │────▶│  虚拟声卡   │────▶│  Python VAD │
│   接收棒     │     │   SDR#      │     │ VB-CABLE   │     │   语音切片   │
└─────────────┘     └─────────────┘     └─────────────┘     └──────┬──────┘
                                                                   │
                                                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                         后端处理管道 (pipeline.py)                          │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                   │
│  │ faster-     │───▶│  规则分类器  │───▶│  WebSocket  │                   │
│  │ whisper ASR │    │  keywords   │    │  实时推送    │                   │
│  └─────────────┘    └─────────────┘    └──────┬──────┘                   │
└─────────────────────────────────────────────────┼────────────────────────┘
                                                  │
                                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                         前端 LED 大屏 (Vue3)                               │
│  ┌─────────────────────────────┐    ┌─────────────────────────────────┐  │
│  │      实时事件流 · LIVE       │    │  统计面板 | 基站状态 | 系统信息   │  │
│  │  ┌─────────────────────┐   │    │  ┌───┐ ┌───┐ ┌───┐ ┌───┐       │  │
│  │  │ [特急] 北区山体滑坡  │   │    │  │特急│ │紧急│ │一般│ │轻微│       │  │
│  │  │ 多人被困需要救援...  │   │    │  │ 1 │ │ 0 │ │ 2 │ │ 5 │       │  │
│  │  │ 立即派遣救援队伍...  │   │    │  └───┘ └───┘ └───┘ └───┘       │  │
│  │  └─────────────────────┘   │    │                                   │  │
│  └─────────────────────────────┘    └─────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| SDR 接收 | RTL-SDR Blog V4 + GQRX/SDR# | 144/430MHz 业余频段 |
| 音频路由 | VB-CABLE / PulseAudio | 虚拟声卡桥接 |
| 语音切片 | sounddevice + webrtcvad | 16kHz PCM, VAD 静音检测 |
| ASR | faster-whisper (CTranslate2) | 中文识别, int8_float16 量化 |
| 分类 | 规则快通道 + 可选 LLM 慢通道 | YAML 词典兜底，Ollama JSON 抽取长尾事件 |
| 后端 | FastAPI + WebSocket | 异步推送 |
| 前端 | Vue3 + CSS Grid | LED 墙适配 (大字号/纯黑底/纯色块) |
| 持久化 | SQLite | 事件历史与 incident 重启恢复 |
| 部署 | Docker / Docker Compose | 后端同源托管前端大屏 |

## 快速开始

### 环境要求

- Python 3.11+
- Node.js 18+
- CUDA GPU (推荐 RTX 3060 12GB+, CPU 模式可用但较慢)
- (可选) RTL-SDR Blog V4 接收棒

### 1. 克隆并安装依赖

```bash
git clone <repo-url>
cd emergency-radio-agent

# Python 后端
pip install -r requirements.txt

# Node.js 前端
cd frontend && npm install && cd ..
```

### 2. 启动 (模拟模式 - 无硬件)

```bash
# 方式 1: 一键脚本
python run.py --mock --no-gpu

# 方式 2: Docker
docker-compose up --build

# 方式 3: 手动分别启动
# 终端 1 - 后端
cd backend && MOCK_MODE=true NO_GPU=true uvicorn server:app --host 0.0.0.0 --port 8000

# 终端 2 - 前端
cd frontend && npm run dev
```

### 3. 访问

- Docker/生产大屏: http://localhost:8000
- 开发模式大屏: http://localhost:3000
- API 文档: http://localhost:8000/docs
- 健康检查: http://localhost:8000/api/health

### 4. SDR 真实模式 (有硬件)

```bash
# 1. 安装 RTL-SDR 驱动
# Windows: Zadig 替换驱动 (Bulk-In, Interface 0)
# Linux: sudo apt install rtl-sdr

# 2. 安装 GQRX 或 SDR#
# 配置音频输出到虚拟声卡 (VB-CABLE)

# 3. 启动
python run.py --model medium

# 本仓库已配置 RTL-SDR Blog V4 时，可绕过虚拟声卡直接 NFM 解调
python run.py --sdr-source rtl --frequency 438.500 --channel ch-main --model medium
```

## 项目结构

```
emergency-radio-agent/
├── backend/
│   ├── audio/
│   │   └── sdr_capture.py      # SDR 音频采集 + VAD 切片
│   ├── asr/
│   │   └── whisper_engine.py   # faster-whisper 语音识别
│   ├── classifier/
│   │   ├── keywords.yaml       # 应急关键词词典
│   │   └── engine.py           # 混合分类引擎
│   ├── sounds/
│   ├── server.py               # FastAPI WebSocket 服务
│   └── pipeline.py             # 集成处理管道
├── frontend/
│   ├── src/
│   │   └── views/
│   │       └── CommandScreen.vue   # LED 大屏主界面
│   ├── index.html
│   ├── package.json
│   └── vite.config.js
├── docs/
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── run.py                      # 一键启动脚本
└── README.md
```

## 配置说明

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MOCK_MODE` | `false` | 模拟模式(无硬件) |
| `NO_GPU` | `false` | 强制 CPU 模式 |
| `MODEL_SIZE` | `medium` | Whisper 模型: tiny/base/small/medium/large-v3 |
| `ASR_BACKEND` | `whisper` | `mock` 仅用于不加载模型的硬件/API 冒烟测试 |
| `COMPUTE_TYPE` | `int8_float16` | int8/int8_float16/float16/float32 |
| `AUDIO_DEVICE` | 自动检测 | 音频设备索引 |
| `SDR_SOURCE` | `audio` | `audio` 使用虚拟声卡；`rtl` 直接读取 RTL-SDR IQ 并做 NFM 解调 |
| `RTL_FREQUENCY_MHZ` | `438.500` | RTL-SDR 直连模式中心频率 MHz |
| `RTL_CHANNEL_ID` | `ch-main` | 直连接收事件关联的频道 ID |
| `RTL_SQUELCH_SNR_DB` | `8.0` | 直连 NFM 开启音频的估算 SNR 门限 |
| `MOCK_INTERVAL` | `5.0` | 模拟推送间隔(秒) |
| `CPU_THREADS` | `4` | CPU 推理线程数 |
| `ASR_NO_SPEECH_THRESHOLD` | `0.6` | Whisper no_speech 过滤阈值 |
| `ASR_AVG_LOGPROB_THRESHOLD` | `-1.0` | Whisper 低置信过滤阈值 |
| `ASR_CONDITION_ON_PREVIOUS_TEXT` | `false` | 噪声场景下关闭上下文续写，减少幻觉滚雪球 |
| `ALLOW_MOCK_FALLBACK` | `false` | 真实模式 Whisper 失败时是否允许退回 MockASR |
| `CORS_ORIGINS` | 空 | 逗号分隔 CORS 白名单；空为开放来源但不带 credentials |
| `ADMIN_TOKEN` | 空 | 写操作鉴权 token；为空则不启用 |
| `VITE_ADMIN_TOKEN` | 空 | 前端构建期注入的管理 token |
| `STORAGE_PATH` | `./data/emergency-radio-agent.sqlite3` | SQLite 事件/incident 数据库 |
| `RADIO_CHANNELS` | 内置 4 路 | 多频道监听配置: `id|名称|频率MHz|调制;...` |
| `RADIO_STALE_AFTER_SECONDS` | `8.0` | 频道多久无遥测/通联后标为中断 |
| `LLM_FALLBACK_ENABLED` | `false` | 低置信度/未分类事件是否调用 LLM 慢通道 |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama 服务地址 |
| `OLLAMA_MODEL` | `qwen2.5:7b` | LLM 慢通道模型名 |
| `LLM_CONFIDENCE_THRESHOLD` | `0.58` | 低于该置信度转 LLM/人工复核 |

### 无线电态势接入

大屏的频谱、RSSI、SNR、噪声底和频道状态来自后端 `radio snapshot`，前端不再自行伪造真实模式下的波形。真实 SDR 落地时，需要由接收机侧进程把遥测写入后端，例如 rtl_power/GNU Radio/SDR# 插件或自写桥接脚本。

```bash
curl -X POST http://127.0.0.1:8000/api/radio/telemetry \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: your-token" \
  -d '{"channel_id":"ch-main","rssi_dbm":-71.5,"snr_db":19.2,"noise_floor_dbm":-113.0,"squelch_open":true,"spectrum":[0.08,0.12,0.46,0.91,0.54,0.18]}'
```

读取当前多频道状态:

```bash
curl http://127.0.0.1:8000/api/radio/snapshot
```

### 自定义关键词

编辑 `backend/classifier/keywords.yaml`:

```yaml
severity:
  critical:
    火灾: 10
    爆炸: 10
    你的新词: 8
```

## LED 大屏设计规范

| 项目 | 规范 |
|------|------|
| 背景 | 纯黑 `#000000` |
| 最小字号 | 24px |
| 标题字号 | 48-72px |
| 字重 | 500+ (不用细体) |
| 线宽 | 2px+ |
| 颜色 | 纯色块,不用渐变 |
| 动画 | 慢速,避免刺眼 |

## 常见问题

### Q: SDR 听不到声音?
- Windows: 检查 Zadig 驱动是否正确替换
- GQRX: 关闭 Mute,确认 Output device 选虚拟声卡
- Linux: `sudo usermod -aG plugdev $USER && sudo udevadm trigger`

### Q: Whisper 显存不足?
- 降级到 `small` 模型: `--model small`
- 使用 CPU 模式: `--no-gpu`
- 改 `compute_type=int8`

### Q: 前端连接不上?
- 检查后端是否运行: `curl http://localhost:8000/api/health`
- Docker/生产模式直接访问后端同源页面: `http://localhost:8000`
- 开发模式访问 Vite: `http://localhost:3000`, `/api` 和 `/ws` 由 `vite.config.js` 代理到后端
- 如果开启 `ADMIN_TOKEN`, 构建前端时同步设置 `VITE_ADMIN_TOKEN`

### Q: 为什么真实模式不再自动退回 MockASR?
- 真实 SDR 落地时静默 mock 会掩盖 ASR 初始化失败, 默认 `ALLOW_MOCK_FALLBACK=false` 会 fail-fast。
- 纯演示兜底可以显式设置 `ALLOW_MOCK_FALLBACK=true`。

### Q: 如何开启 LLM 慢通道?
- 本地启动 Ollama 并拉取模型, 例如 `qwen2.5:7b`。
- 设置 `LLM_FALLBACK_ENABLED=true`。
- 高置信度规则命中仍走本地规则；低置信度或未分类事件会请求 Ollama 输出 JSON, 失败时保留规则结果。

## 后续路线图

| 功能 | v1 (当前) | v2 计划 |
|------|-----------|---------|
| SDR 多频段 | 多频道遥测面板 | 多接收机并发/自动扫频 |
| LED 真机适配 | 显示器验证 | 按拼接屏分辨率重设计 |
| 地图标注 | 离线矢量态势图 | 高德/天地图 API 或离线瓦片 |
| 历史回溯 | SQLite 事件/incident 持久化 | 时间轴检索与回放 UI |
| 语音回传 | 无 | TTS + SDR 发射 |
| 多人协作 | 无 | 角色系统 + 工单流转 |

## 许可证

MIT License

## 安全提醒

- 中国大陆使用 SDR 接收需遵守《无线电管理条例》
- 业余频段(144/430MHz)自用学习合规
- 生产环境必须申请频率许可
- 发射功能需取得业余无线电操作证

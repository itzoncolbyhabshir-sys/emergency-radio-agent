"""
FastAPI WebSocket 服务

提供:
- WebSocket 实时事件推送 (/ws)
- REST API 健康检查、统计信息
- 静态文件托管(前端构建产物)

启动:
    uvicorn server:app --host 0.0.0.0 --port 8000
或:
    python server.py
"""

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import (
    Body,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

# 导入管道
from pipeline import create_pipeline, get_pipeline


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    return float(value)


def _cors_origins() -> List[str]:
    origins = os.environ.get("CORS_ORIGINS", "").strip()
    if not origins:
        return ["*"]
    return [origin.strip() for origin in origins.split(",") if origin.strip()]


def _load_pipeline_config() -> dict:
    return {
        "mock_mode": _env_bool("MOCK_MODE"),
        "no_gpu": _env_bool("NO_GPU"),
        "allow_mock_fallback": _env_bool("ALLOW_MOCK_FALLBACK", False),
        "model_size": os.environ.get("MODEL_SIZE", "medium"),
        "asr_backend": os.environ.get("ASR_BACKEND", "whisper").strip().lower(),
        "compute_type": os.environ.get("COMPUTE_TYPE", "int8_float16"),
        "mock_audio_dir": os.environ.get("MOCK_AUDIO_DIR", "./mock_audio"),
        "mock_interval": _env_float("MOCK_INTERVAL", 5.0),
        "audio_device": os.environ.get("AUDIO_DEVICE"),
        "sdr_source": os.environ.get("SDR_SOURCE", "audio").strip().lower(),
        "rtl_dll_path": os.environ.get("RTL_DLL_PATH"),
        "rtl_frequency_mhz": _env_float("RTL_FREQUENCY_MHZ", 438.5),
        "rtl_channel_id": os.environ.get("RTL_CHANNEL_ID", "ch-main"),
        "rtl_sample_rate": int(os.environ.get("RTL_SAMPLE_RATE", "1024000")),
        "rtl_squelch_snr_db": _env_float("RTL_SQUELCH_SNR_DB", 8.0),
        "rtl_rssi_offset_db": _env_float("RTL_RSSI_OFFSET_DB", -50.0),
        "segments_dir": os.environ.get("SEGMENTS_DIR", "./segments"),
        "storage_path": os.environ.get(
            "STORAGE_PATH", "./data/emergency-radio-agent.sqlite3"
        ),
        "cpu_threads": int(os.environ.get("CPU_THREADS", "8")),
        "asr_no_speech_threshold": _env_float("ASR_NO_SPEECH_THRESHOLD", 0.6),
        "asr_avg_logprob_threshold": _env_float("ASR_AVG_LOGPROB_THRESHOLD", -1.0),
        "asr_condition_on_previous_text": _env_bool(
            "ASR_CONDITION_ON_PREVIOUS_TEXT", False
        ),
        "llm_fallback_enabled": _env_bool("LLM_FALLBACK_ENABLED", False),
        "llm_summary_enabled": _env_bool("LLM_SUMMARY_ENABLED", True),
        "ollama_base_url": os.environ.get("OLLAMA_BASE_URL"),
        "ollama_model": os.environ.get("OLLAMA_MODEL", "qwen2.5:7b"),
        "llm_timeout": _env_float("LLM_TIMEOUT", 4.0),
        "llm_confidence_threshold": _env_float("LLM_CONFIDENCE_THRESHOLD", 0.58),
        "radio_channels": os.environ.get("RADIO_CHANNELS"),
        "radio_stale_after_seconds": _env_float("RADIO_STALE_AFTER_SECONDS", 8.0),
    }


async def require_admin_token(
    x_admin_token: Optional[str] = Header(default=None),
) -> None:
    """Protect mutation/demo endpoints when ADMIN_TOKEN or API_TOKEN is configured."""
    expected = os.environ.get("ADMIN_TOKEN") or os.environ.get("API_TOKEN")
    if expected and x_admin_token != expected:
        raise HTTPException(status_code=401, detail="invalid admin token")


broadcaster_task_handle: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and clean up pipeline resources with FastAPI's lifespan API."""
    global broadcaster_task_handle
    print("=" * 60)
    print("应急电台智能体服务启动")
    print("=" * 60)

    pipe = create_pipeline(_load_pipeline_config())
    pipe.start()
    broadcaster_task_handle = asyncio.create_task(broadcaster_task())

    print("[Server] 服务地址: http://0.0.0.0:8000")
    print("[Server] WebSocket: ws://0.0.0.0:8000/ws")
    print("=" * 60)

    try:
        yield
    finally:
        if broadcaster_task_handle:
            broadcaster_task_handle.cancel()
            try:
                await broadcaster_task_handle
            except asyncio.CancelledError:
                pass
            broadcaster_task_handle = None

        pipe = get_pipeline()
        if pipe:
            pipe.stop()
        print("[Server] 服务已关闭")


# === 创建应用 ===
app = FastAPI(
    title="应急电台智能体 API",
    description="SDR + Whisper + LED 大屏 - 应急通讯智能识别系统",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
cors_origins = _cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=cors_origins != ["*"],
)


# === WebSocket 连接管理 ===
class ConnectionManager:
    def __init__(self):
        self.active_connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self.active_connections.add(ws)
        print(f"[WS] 客户端连接,当前 {len(self.active_connections)} 个")

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self.active_connections.discard(ws)
        print(f"[WS] 客户端断开,当前 {len(self.active_connections)} 个")

    async def broadcast(self, message: dict):
        """广播消息给所有连接客户端。"""
        if not self.active_connections:
            return

        text = json.dumps(message, ensure_ascii=False)
        dead = []

        for ws in self.active_connections:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)

        # 清理断开的连接
        if dead:
            async with self._lock:
                for d in dead:
                    self.active_connections.discard(d)

    async def send_personal(self, ws: WebSocket, message: dict):
        """发送给单个客户端。"""
        try:
            await ws.send_text(json.dumps(message, ensure_ascii=False))
        except Exception:
            await self.disconnect(ws)


manager = ConnectionManager()
demo_task = None


DEMO_SCENARIO = [
    (0.2, "浦东陆家嘴中心广场人流密集，电台测试正常，现场秩序平稳"),
    (1.0, "杨浦大桥附近通信基站信号异常，疑似断电影响通信，请技术人员检查"),
    (1.4, "闵行虹桥站周边积水严重，车辆无法通行，请求交通疏导和排涝设备"),
    (1.6, "徐汇龙华路发生道路塌陷，两人被困，需要紧急救援和医疗支援"),
    (1.4, "徐汇龙华路补充报告，已有三人受伤，现场仍有二次塌陷风险"),
    (1.2, "黄浦外滩商场二层起火，有浓烟，请立即派消防力量并疏散群众"),
    (1.4, "浦东张江医院急诊入口交通堵塞，救护车辆通行受阻，请协调警力疏导"),
]


# === WebSocket 路由 ===
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            # 接收客户端消息,超时则发送 ping
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=30.0)
                data = json.loads(msg)

                # 处理客户端指令
                if data.get("type") == "ping":
                    await manager.send_personal(
                        ws, {"type": "pong", "time": datetime.now().isoformat()}
                    )
                elif data.get("type") == "get_stats":
                    pipe = get_pipeline()
                    if pipe:
                        await manager.send_personal(
                            ws, {"type": "stats", "data": pipe.get_stats()}
                        )
                elif data.get("type") == "get_radio":
                    pipe = get_pipeline()
                    if pipe:
                        await manager.send_personal(
                            ws,
                            {
                                "type": "radio_telemetry",
                                "data": pipe.get_radio_snapshot(),
                            },
                        )
                elif data.get("type") == "get_transcripts":
                    pipe = get_pipeline()
                    if pipe:
                        await manager.send_personal(
                            ws,
                            {
                                "type": "transcripts",
                                "data": pipe.list_transcripts(limit=80),
                            },
                        )

            except asyncio.TimeoutError:
                await manager.send_personal(ws, {"type": "ping"})

    except WebSocketDisconnect:
        await manager.disconnect(ws)
    except Exception as e:
        print(f"[WS] 连接错误: {e}")
        await manager.disconnect(ws)


# === REST API ===
@app.get("/api/health")
async def health():
    """健康检查。"""
    pipe = get_pipeline()
    return {
        "status": "ok",
        "time": datetime.now().isoformat(),
        "pipeline_running": pipe.running if pipe else False,
        "pipeline_stats": pipe.get_stats() if pipe else None,
        "websocket_clients": len(manager.active_connections),
    }


@app.get("/api/stats")
async def stats():
    """获取管道统计。"""
    pipe = get_pipeline()
    if not pipe:
        return {"error": "管道未初始化"}
    return {
        "stats": pipe.get_stats(),
        "queue_size": pipe.get_queue_size(),
        "clients": len(manager.active_connections),
    }


@app.get("/api/config")
async def get_config():
    """获取当前配置信息。"""
    pipe = get_pipeline()
    if not pipe or not pipe.asr:
        return {"error": "未初始化"}
    return {
        "mock_mode": pipe.mock_mode,
        "asr": pipe.asr.get_model_info(),
        "classifier": pipe.classifier.__class__.__name__,
    }


@app.get("/api/incidents")
async def list_incidents():
    """Return active merged incidents."""
    pipe = get_pipeline()
    if not pipe:
        return {"incidents": []}
    return {"incidents": pipe.list_incidents()}


@app.get("/api/events/history")
async def list_event_history(limit: int = 100, offset: int = 0):
    """Return persisted event history for replay/timeline views."""
    pipe = get_pipeline()
    if not pipe:
        return {"events": []}
    return {"events": pipe.list_events(limit=limit, offset=offset)}


@app.get("/api/asr/transcripts")
async def list_asr_transcripts(limit: int = 100):
    """Return recent ASR attempts for manual operator review."""
    pipe = get_pipeline()
    if not pipe:
        return {"transcripts": []}
    return {"transcripts": pipe.list_transcripts(limit=limit)}


@app.get("/api/radio/snapshot")
async def get_radio_snapshot():
    """Return current multi-channel receiver telemetry for the dashboard."""
    pipe = get_pipeline()
    if not pipe:
        return {"active_channel_id": None, "channels": [], "mode": "offline"}
    return pipe.get_radio_snapshot()


@app.post("/api/radio/telemetry")
async def update_radio_telemetry(
    payload: dict = Body(...),
    _: None = Depends(require_admin_token),
):
    """Update RF telemetry from an SDR receiver bridge."""
    pipe = get_pipeline()
    if not pipe:
        raise HTTPException(status_code=503, detail="pipeline not initialized")
    try:
        channel = pipe.update_radio_telemetry(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    snapshot = pipe.get_radio_snapshot()
    await manager.broadcast({"type": "radio_telemetry", "data": snapshot})
    return {"channel": channel, "snapshot": snapshot}


@app.post("/api/incidents/{incident_id}/status")
async def update_incident_status(
    incident_id: str,
    payload: dict = Body(...),
    _: None = Depends(require_admin_token),
):
    """Advance an incident through the command workflow."""
    pipe = get_pipeline()
    if not pipe:
        raise HTTPException(status_code=503, detail="pipeline not initialized")
    status = payload.get("status")
    try:
        incident = pipe.update_incident_status(incident_id, status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not incident:
        raise HTTPException(status_code=404, detail="incident not found")
    await manager.broadcast({"type": "incident_status", "data": incident})
    return {"incident": incident}


@app.post("/api/events/manual")
async def ingest_manual_event(
    payload: dict = Body(...),
    _: None = Depends(require_admin_token),
):
    """Inject a text event for demos or manual operator input."""
    pipe = get_pipeline()
    if not pipe:
        raise HTTPException(status_code=503, detail="pipeline not initialized")
    text = payload.get("text", "")
    event = pipe.ingest_text(
        text,
        source=payload.get("source", "manual"),
        allow_duplicate=True,
        radio_channel=payload.get("radio_channel") or payload.get("channel_id"),
    )
    if not event:
        raise HTTPException(status_code=400, detail="empty event text")
    return {"event": event}


@app.post("/api/demo/start")
async def start_demo(_: None = Depends(require_admin_token)):
    """Run the built-in award-demo scenario."""
    global demo_task
    pipe = get_pipeline()
    if not pipe:
        raise HTTPException(status_code=503, detail="pipeline not initialized")
    if demo_task and not demo_task.done():
        return {"status": "already_running"}
    demo_task = asyncio.create_task(_run_demo_scenario(pipe))
    return {"status": "started", "steps": len(DEMO_SCENARIO)}


async def _run_demo_scenario(pipe):
    for delay, text in DEMO_SCENARIO:
        await asyncio.sleep(delay)
        pipe.ingest_text(text, source="demo", allow_duplicate=True)


# === 静态文件 ===
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount(
        "/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets"
    )

    @app.get("/{path:path}")
    async def serve_frontend(path: str):
        """兜底路由,返回前端 index.html。"""
        index_file = frontend_dist / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return {"error": "前端未构建"}


# === 事件广播任务 ===
async def broadcaster_task():
    """后台任务: 从管道队列消费事件,广播给 WebSocket 客户端。"""
    pipe = get_pipeline()
    if not pipe:
        print("[Server] 警告: 管道未初始化")
        return

    print("[Server] 事件广播任务启动")
    last_radio_broadcast = 0.0
    while True:
        try:
            event = await asyncio.wait_for(pipe.event_queue.get(), timeout=0.35)
            await manager.broadcast({"type": "event", "data": event})
            await manager.broadcast(
                {"type": "radio_telemetry", "data": pipe.get_radio_snapshot()}
            )
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            print(f"[Server] 广播错误: {e}")

        while True:
            try:
                transcript = pipe.transcript_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            except Exception as e:
                print(f"[Server] 识别复核广播错误: {e}")
                break
            await manager.broadcast({"type": "transcript", "data": transcript})

        now = time.time()
        if now - last_radio_broadcast >= 1.0:
            await manager.broadcast(
                {"type": "radio_telemetry", "data": pipe.get_radio_snapshot()}
            )
            last_radio_broadcast = now


# === 入口 ===
if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )

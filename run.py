"""
一键启动脚本

用法:
    python run.py              # 启动完整服务
    python run.py --mock       # 模拟模式(无 SDR 硬件)
    python run.py --no-gpu     # CPU 模式
    python run.py --dev        # 开发模式(前后端同时启动)
"""

import argparse
import subprocess
import os
import sys
import signal
from pathlib import Path


def run_backend(
    mock=False,
    no_gpu=False,
    model="medium",
    sdr_source="audio",
    frequency=438.5,
    channel="ch-main",
):
    """启动后端服务。"""
    env = os.environ.copy()
    if mock:
        env["MOCK_MODE"] = "true"
    if no_gpu:
        env["NO_GPU"] = "true"
    env["MODEL_SIZE"] = model
    env["SDR_SOURCE"] = sdr_source
    env["RTL_FREQUENCY_MHZ"] = str(frequency)
    env["RTL_CHANNEL_ID"] = channel

    backend_dir = Path(__file__).parent / "backend"
    cmd = [sys.executable, "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]

    print("=" * 60)
    print("启动后端服务...")
    print(f"  模式: {'模拟' if mock else '真实SDR'}")
    if not mock:
        print(f"  SDR 源: {sdr_source}, {frequency:.3f} MHz, {channel}")
    print(f"  GPU: {'禁用' if no_gpu else '启用'}")
    print(f"  模型: {model}")
    print("=" * 60)

    return subprocess.Popen(cmd, cwd=backend_dir, env=env)


def run_frontend():
    """启动前端开发服务器。"""
    frontend_dir = Path(__file__).parent / "frontend"
    cmd = ["npm", "run", "dev"]

    print("=" * 60)
    print("启动前端开发服务器...")
    print("=" * 60)

    return subprocess.Popen(cmd, cwd=frontend_dir)


def main():
    parser = argparse.ArgumentParser(description="应急电台智能体启动脚本")
    parser.add_argument("--mock", action="store_true", help="模拟模式(无硬件)")
    parser.add_argument("--no-gpu", action="store_true", help="强制 CPU 模式")
    parser.add_argument("--model", default="medium", choices=["tiny", "base", "small", "medium", "large-v3"],
                        help="Whisper 模型大小")
    parser.add_argument("--dev", action="store_true", help="开发模式(同时启动前后端)")
    parser.add_argument("--docker", action="store_true", help="使用 Docker 启动")
    parser.add_argument(
        "--sdr-source",
        default="audio",
        choices=["audio", "rtl"],
        help="真实模式输入源: audio=虚拟声卡, rtl=直接读取 RTL-SDR",
    )
    parser.add_argument("--frequency", type=float, default=438.5, help="RTL-SDR 中心频率 MHz")
    parser.add_argument("--channel", default="ch-main", help="遥测和事件关联的频道 ID")

    args = parser.parse_args()

    # Docker 模式
    if args.docker:
        subprocess.run(["docker-compose", "up", "--build"])
        return

    processes = []

    try:
        # 启动后端
        p_backend = run_backend(
            mock=args.mock,
            no_gpu=args.no_gpu,
            model=args.model,
            sdr_source=args.sdr_source,
            frequency=args.frequency,
            channel=args.channel,
        )
        processes.append(p_backend)

        # 开发模式同时启动前端
        if args.dev:
            p_frontend = run_frontend()
            processes.append(p_frontend)

        print("\n按 Ctrl+C 停止服务\n")

        # 等待任意进程结束
        for p in processes:
            p.wait()

    except KeyboardInterrupt:
        print("\n\n收到中断信号,正在停止...")
        for p in processes:
            p.terminate()
            try:
                p.wait(timeout=5)
            except:
                p.kill()
        print("已停止")


if __name__ == "__main__":
    main()

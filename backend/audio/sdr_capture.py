"""
SDR 音频采集 + VAD 语音切片模块

支持从虚拟声卡(VB-CABLE / PulseAudio loopback)读取音频流，
使用 WebRTC VAD 检测语音活动，自动切分出有效语音片段。

使用方式:
    seg = SDRSegmenter(output_dir="./segments")
    seg.on_segment_callback = lambda fn: print(f"待处理: {fn}")
    seg.run()
"""

import numpy as np
import wave
import time
import threading
from pathlib import Path
from queue import Empty, Full, Queue

sd = None
webrtcvad = None


def _lazy_audio_import():
    global sd, webrtcvad
    if sd is None:
        import sounddevice as _sd

        sd = _sd
    if webrtcvad is None:
        import webrtcvad as _webrtcvad

        webrtcvad = _webrtcvad


# === 常量配置 ===
SAMPLE_RATE = 16000  # WebRTC VAD 只支持 8000/16000/32000/48000
FRAME_DURATION_MS = 30  # VAD 帧长: 10/20/30 ms
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000)
SILENCE_THRESHOLD_FRAMES = 15  # 静音阈值帧数,约 450ms
MAX_SEGMENT_SECONDS = 30  # 单段最大时长,防止无限录制


def find_virtual_audio_device():
    """
    自动查找虚拟声卡设备索引。
    支持 VB-CABLE(Windows) / PulseAudio loopback / BlackHole(macOS) 等。
    """
    _lazy_audio_import()
    keywords = [
        "cable",
        "loopback",
        "monitor",
        "blackhole",
        "vac",
        "vb-audio",
        "virtual",
        "立体声混音",
        "stereo mix",
    ]
    devices = sd.query_devices()

    for i, dev in enumerate(devices):
        name = dev.get("name", "").lower()
        if dev.get("max_input_channels", 0) > 0:
            for kw in keywords:
                if kw in name:
                    print(f"[SDR] 找到虚拟音频输入设备 #{i}: {dev['name']}")
                    return i

    #  fallback: 返回默认输入设备
    default = sd.default.device[0]
    print(
        f"[SDR] 未找到虚拟声卡,使用默认输入设备 #{default}: {devices[default]['name']}"
    )
    return default


def find_output_device():
    """查找默认输出设备(用于监控)。"""
    _lazy_audio_import()
    return sd.default.device[1]


class SDRSegmenter:
    """
    SDR 音频流分割器。

    从指定音频设备持续读取,使用 VAD 切分有效语音段,
    每切分出一段就调用 on_segment_callback。
    """

    def __init__(self, output_dir="./segments", device=None, vad_aggressiveness=2):
        """
        Args:
            output_dir: 切分后的 wav 文件保存目录
            device: 音频设备索引,None 则自动查找
            vad_aggressiveness: VAD 灵敏度 0-3,越高越严格
        """
        _lazy_audio_import()
        self.vad = webrtcvad.Vad(vad_aggressiveness)
        self.device = device if device is not None else find_virtual_audio_device()
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 状态
        self.buffer = []
        self.silence_count = 0
        self.recording = False
        self.segment_start_time = 0
        self.running = False
        self.stream = None
        self._thread = None
        self._writer_thread = None
        self._writer_stop = threading.Event()
        self._write_queue = Queue(maxsize=16)

        # 回调: fn(wav_path) -> None
        self.on_segment_callback = None

        # 统计
        self.total_segments = 0
        self.total_duration = 0.0

    def _audio_callback(self, indata, frames, time_info, status):
        """sounddevice 回调,在独立线程中执行。"""
        if status:
            print(f"[SDR] 音频流状态: {status}")

        # 转为 16bit PCM mono
        pcm = (indata[:, 0] * 32767).astype(np.int16).tobytes()

        self._process_pcm_frame(pcm)

    def _process_pcm_frame(self, pcm):
        """Feed one 30 ms, 16 kHz, mono int16 frame into the VAD segmenter."""

        try:
            is_speech = self.vad.is_speech(pcm, SAMPLE_RATE)
        except Exception as e:
            print(f"[SDR] VAD 错误: {e}")
            return

        if is_speech:
            self.buffer.append(pcm)
            self.silence_count = 0
            if not self.recording:
                self.recording = True
                self.segment_start_time = time.time()
                print("▶ 检测到语音开始")
        else:
            if self.recording:
                self.buffer.append(pcm)
                self.silence_count += 1

                # 检查结束条件: 静音超时 或 单段过长
                elapsed = time.time() - self.segment_start_time
                if (
                    self.silence_count > SILENCE_THRESHOLD_FRAMES
                    or elapsed > MAX_SEGMENT_SECONDS
                ):
                    self._save_and_reset()

    def _save_and_reset(self):
        """Queue the current PCM segment for the writer thread and reset state."""
        if not self.buffer:
            return

        duration = len(self.buffer) * FRAME_DURATION_MS / 1000.0
        timestamp = int(time.time() * 1000)
        filename = self.output_dir / f"seg_{timestamp}_{duration:.1f}s.wav"
        try:
            self._write_queue.put_nowait((filename, list(self.buffer), duration))
        except Full:
            print("[SDR] 写盘队列已满,丢弃当前语音片段")

        self.buffer = []
        self.silence_count = 0
        self.recording = False
        self.segment_start_time = 0

    def _writer_loop(self):
        """Write completed PCM segments to wav files outside the audio callback."""
        while not self._writer_stop.is_set() or not self._write_queue.empty():
            try:
                item = self._write_queue.get(timeout=0.2)
            except Empty:
                continue
            try:
                if item is None:
                    return
                filename, pcm_frames, duration = item
                self._write_segment(filename, pcm_frames, duration)
            finally:
                self._write_queue.task_done()

    def _write_segment(self, filename, pcm_frames, duration):
        try:
            with wave.open(str(filename), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)  # 16bit
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(b"".join(pcm_frames))

            self.total_segments += 1
            self.total_duration += duration
            print(
                f"✓ 保存语音片段 [{self.total_segments}]: {filename.name} ({duration:.1f}s)"
            )

            if self.on_segment_callback:
                try:
                    self.on_segment_callback(filename)
                except Exception as e:
                    print(f"[SDR] 回调错误: {e}")

        except Exception as e:
            print(f"[SDR] 保存失败: {e}")

    def start(self):
        """在后台线程启动音频流。"""
        if self.running:
            return
        self.running = True
        self._writer_stop.clear()

        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._writer_thread.start()
        self._thread = threading.Thread(target=self._run_stream, daemon=True)
        self._thread.start()
        print("[SDR] 音频采集已启动")

    def _run_stream(self):
        """在独立线程中运行音频流。"""
        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                blocksize=FRAME_SIZE,
                device=self.device,
                channels=1,
                dtype=np.float32,
                callback=self._audio_callback,
            ) as stream:
                self.stream = stream
                print("● 开始监听 SDR 音频流...")
                while self.running:
                    time.sleep(0.1)
        except Exception as e:
            print(f"[SDR] 音频流错误: {e}")
            self.running = False
        finally:
            self.stream = None

    def stop(self):
        """停止音频采集。"""
        self.running = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
        if self._thread:
            self._thread.join(timeout=2)
        if self.buffer:
            self._save_and_reset()
        if self._writer_thread:
            self._writer_stop.set()
            self._writer_thread.join(timeout=3)
        print("[SDR] 音频采集已停止")

    def run(self):
        """阻塞运行(用于直接运行脚本)。"""
        self.start()
        try:
            while self.running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n[SDR] 收到中断信号")
        finally:
            self.stop()
            print(
                f"[SDR] 统计: 共采集 {self.total_segments} 段, "
                f"总时长 {self.total_duration:.1f}s"
            )

    @property
    def is_recording(self):
        return self.recording

    def get_stats(self):
        return {
            "total_segments": self.total_segments,
            "total_duration": round(self.total_duration, 2),
            "is_recording": self.recording,
            "device": self.device,
        }


class MockAudioSegmenter:
    """
    模拟音频采集器(无 SDR 硬件时使用)。
    从本地 wav 文件模拟推送,用于测试链路。
    """

    def __init__(self, mock_dir="./mock_audio", interval=3.0):
        self.mock_dir = Path(mock_dir)
        self.interval = interval
        self.running = False
        self.on_segment_callback = None
        self._thread = None

    def start(self):
        import random

        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print(f"[MockAudio] 模拟音频推送已启动,间隔 {self.interval}s")

    def _loop(self):
        import random

        files = list(self.mock_dir.glob("*.wav")) if self.mock_dir.exists() else []
        if not files:
            print(f"[MockAudio] 警告: {self.mock_dir} 下无 wav 文件")

        idx = 0
        while self.running:
            if files:
                fn = files[idx % len(files)]
                print(f"[MockAudio] 推送: {fn.name}")
                if self.on_segment_callback:
                    self.on_segment_callback(fn)
                idx += 1
            time.sleep(self.interval)

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=2)


# === 直接运行测试 ===
if __name__ == "__main__":
    _lazy_audio_import()
    print("=" * 50)
    print("SDR 音频采集 + VAD 切片测试")
    print("=" * 50)
    print(f"可用音频设备:")
    for i, d in enumerate(sd.query_devices()):
        marker = ""
        if i == sd.default.device[0]:
            marker = " [默认输入]"
        if i == sd.default.device[1]:
            marker += " [默认输出]"
        print(
            f"  {i}: {d['name']} (in={d['max_input_channels']}, out={d['max_output_channels']}){marker}"
        )
    print("-" * 50)

    seg = SDRSegmenter()
    seg.on_segment_callback = lambda fn: print(f"  → 待处理: {fn}")
    seg.run()

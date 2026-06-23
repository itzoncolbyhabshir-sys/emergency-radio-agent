"""
Whisper ASR 语音识别模块

使用 faster-whisper (CTranslate2 优化):
- 比官方 whisper 快 4 倍
- 显存占用减半(int8 量化)
- 支持 VAD 过滤

模型选择:
- tiny/base: 极快但中文准确率低
- small: 资源紧张时使用
- medium: 推荐,准确率与速度平衡(需 ~5GB 显存)
- large-v3: 最准确,需 8GB+ 显存
"""

import time
import os
import numpy as np
from pathlib import Path

# faster-whisper 延迟导入,避免启动时加载
WhisperModel = None


def _lazy_import():
    global WhisperModel
    if WhisperModel is None:
        from faster_whisper import WhisperModel as _WM

        WhisperModel = _WM


class WhisperASR:
    """faster-whisper 语音识别引擎。"""

    def __init__(
        self,
        model_size="medium",
        device="cuda",
        compute_type="int8_float16",
        download_root=None,
        cpu_threads=4,
        no_speech_threshold=0.6,
        avg_logprob_threshold=-1.0,
        condition_on_previous_text=False,
    ):
        """
        Args:
            model_size: tiny/base/small/medium/large-v1/large-v2/large-v3
            device: cuda / cpu
            compute_type: int8 / int8_float16 / float16 / float32
            download_root: 模型缓存目录
            cpu_threads: CPU 推理线程数
        """
        _lazy_import()

        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.no_speech_threshold = no_speech_threshold
        self.avg_logprob_threshold = avg_logprob_threshold
        self.condition_on_previous_text = condition_on_previous_text

        # 自动降级: 没 GPU 时用 CPU
        if device == "cuda":
            try:
                import torch

                if not torch.cuda.is_available():
                    print("[ASR] CUDA 不可用,自动降级到 CPU")
                    self.device = "cpu"
                    self.compute_type = "int8"
            except ImportError:
                self.device = "cpu"
                self.compute_type = "int8"

        print(f"[ASR] 加载模型: {model_size} ({self.device}/{self.compute_type})")

        model_kwargs = {}
        if self.device == "cpu":
            model_kwargs["cpu_threads"] = cpu_threads

        self.model = WhisperModel(
            model_size,
            device=self.device,
            compute_type=self.compute_type,
            download_root=download_root,
            **model_kwargs,
        )

        # 预热,避免第一次调用延迟高
        self._warmup()
        print("[ASR] 模型加载完成")

    def _warmup(self):
        """预热推理,稳定首次延迟。"""
        dummy = np.zeros(16000, dtype=np.float32)
        try:
            list(self.model.transcribe(dummy, language="zh", beam_size=1)[0])
        except Exception:
            pass

    def recognize(self, wav_path, language="zh", initial_prompt=None, vad_filter=True):
        """
        识别音频文件。

        Args:
            wav_path: wav 文件路径
            language: 语言代码,默认 zh
            initial_prompt: 热词引导,提升应急专业词汇准确率
            vad_filter: 启用 whisper 内置 VAD 过滤空白

        Returns:
            (text, latency, info_dict)
        """
        t0 = time.time()

        # 默认热词引导
        if initial_prompt is None:
            initial_prompt = (
                "以下是应急通讯录音,可能包含:"
                "火灾、爆炸、滑坡、塌方、地震、被困、紧急救援、"
                "积水、暴雨、故障、信号异常、巡逻、基站等关键词。"
            )

        segments, info = self.model.transcribe(
            str(wav_path),
            language=language,
            beam_size=5,
            best_of=5,
            vad_filter=vad_filter,
            initial_prompt=initial_prompt,
            condition_on_previous_text=self.condition_on_previous_text,
            no_speech_threshold=self.no_speech_threshold,
            log_prob_threshold=self.avg_logprob_threshold,
        )

        # 收集所有片段文本，同时过滤明显的静音/幻觉片段。
        texts = []
        segment_stats = []
        for segment in segments:
            no_speech_prob = float(getattr(segment, "no_speech_prob", 0.0) or 0.0)
            avg_logprob = float(getattr(segment, "avg_logprob", 0.0) or 0.0)
            segment_stats.append(
                {"no_speech_prob": no_speech_prob, "avg_logprob": avg_logprob}
            )
            if no_speech_prob >= self.no_speech_threshold:
                continue
            if avg_logprob < self.avg_logprob_threshold:
                continue
            texts.append(segment.text)

        text = "".join(texts).strip()
        latency = time.time() - t0

        # 识别为空时的处理
        if not text:
            text = "[未识别到语音]"

        print(
            f"[ASR] 识别耗时 {latency:.2f}s: {text[:80]}{'...' if len(text) > 80 else ''}"
        )

        return (
            text,
            latency,
            {
                "language": info.language,
                "language_probability": info.language_probability,
                "duration": info.duration,
                "segments": segment_stats,
                "max_no_speech_prob": max(
                    (item["no_speech_prob"] for item in segment_stats), default=0.0
                ),
                "min_avg_logprob": min(
                    (item["avg_logprob"] for item in segment_stats), default=0.0
                ),
            },
        )

    def recognize_stream(self, audio_buffer: np.ndarray, **kwargs):
        """
        识别内存中的音频数据(用于流式场景)。
        """
        t0 = time.time()

        segments, info = self.model.transcribe(
            audio_buffer, language="zh", beam_size=5, vad_filter=True, **kwargs
        )
        text = "".join([s.text for s in segments]).strip()
        latency = time.time() - t0

        return text, latency, info

    def get_model_info(self):
        return {
            "model_size": self.model_size,
            "device": self.device,
            "compute_type": self.compute_type,
        }


class MockASR:
    """
    模拟 ASR(无 GPU/模型时使用),用于前端联调测试。
    """

    MOCK_RESPONSES = {
        "test": "测试语音一二三",
        "fire": "黄浦外滩商场起火,请立即派消防力量",
        "landslide": "徐汇龙华路发生道路塌陷,两人被困需要紧急救援",
        "flood": "闵行虹桥站周边积水严重,交通中断",
        "fault": "杨浦大桥附近通信基站信号异常,请技术人员检查",
        "patrol": "浦东陆家嘴中心广场巡查正常",
        "default": "收到报告,情况正在核实中",
    }

    def __init__(self):
        import random

        self._idx = 0
        self._keys = list(self.MOCK_RESPONSES.keys())

    def recognize(self, wav_path, **kwargs):
        """模拟识别,轮询返回预设文本。"""
        key = self._keys[self._idx % len(self._keys)]
        self._idx += 1
        text = self.MOCK_RESPONSES[key]
        print(f"[MockASR] 模拟识别: {text}")
        return text, 0.1, {"language": "zh", "duration": 3.0}

    def get_model_info(self):
        return {"model": "mock", "device": "none"}


# === 测试 ===
if __name__ == "__main__":
    print("=" * 50)
    print("Whisper ASR 测试")
    print("=" * 50)

    # 检查是否有测试音频
    test_wav = "./segments/test.wav"
    if not os.path.exists(test_wav):
        print(f"未找到测试音频: {test_wav}")
        print("请先运行 sdr_capture.py 录制一段音频,或放置测试文件")
        exit(1)

    asr = WhisperASR(model_size="small")
    text, latency, info = asr.recognize(test_wav)
    print(f"\n结果: {text}")
    print(f"耗时: {latency:.2f}s")
    print(f"信息: {info}")

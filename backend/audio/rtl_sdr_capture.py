"""Direct RTL-SDR NFM receiver for the emergency radio pipeline.

This path avoids virtual sound cards: IQ samples are read through librtlsdr,
demodulated to 16 kHz PCM, then fed into the existing WebRTC VAD segmenter.
"""

from __future__ import annotations

import ctypes
import math
import os
import time
from pathlib import Path

import numpy as np

from audio.sdr_capture import FRAME_SIZE, SDRSegmenter


DEFAULT_IQ_SAMPLE_RATE = 1_024_000
DEFAULT_AUDIO_SAMPLE_RATE = 16_000
DEFAULT_READ_BYTES = 131_072


def demodulate_nfm(raw_iq, iq_sample_rate=DEFAULT_IQ_SAMPLE_RATE):
    """Convert unsigned interleaved RTL IQ bytes to 16 kHz float NFM audio."""
    raw = np.asarray(raw_iq, dtype=np.uint8)
    usable = raw.size - (raw.size % 2)
    if usable < 256:
        return np.empty(0, dtype=np.float32)

    pairs = raw[:usable].reshape(-1, 2).astype(np.float32)
    iq = (pairs[:, 0] - 127.5) + 1j * (pairs[:, 1] - 127.5)
    iq *= 1.0 / 128.0
    discriminator = np.angle(iq[1:] * np.conj(iq[:-1])).astype(np.float32)

    decimation = max(1, int(round(iq_sample_rate / DEFAULT_AUDIO_SAMPLE_RATE)))
    kernel = np.ones(decimation, dtype=np.float32) / decimation
    filtered = np.convolve(discriminator, kernel, mode="valid")
    audio = filtered[::decimation]
    if audio.size:
        audio -= float(np.mean(audio))
    return audio.astype(np.float32, copy=False)


def measure_rf(raw_iq, frequency_mhz, rssi_offset_db=-50.0, spectrum_bins=96):
    """Build estimated RF telemetry from one block of unsigned IQ samples."""
    raw = np.asarray(raw_iq, dtype=np.uint8)
    usable = raw.size - (raw.size % 2)
    pairs = raw[:usable].reshape(-1, 2).astype(np.float32)
    iq = ((pairs[:, 0] - 127.5) + 1j * (pairs[:, 1] - 127.5)) / 128.0
    if iq.size < 512:
        return {
            "frequency_mhz": float(frequency_mhz),
            "rssi_dbm": None,
            "snr_db": None,
            "noise_floor_dbm": None,
            "spectrum": [],
        }

    rms = float(np.sqrt(np.mean(np.abs(iq) ** 2)))
    rssi_dbm = 20.0 * math.log10(max(rms, 1e-9)) + float(rssi_offset_db)

    fft_size = min(8192, 1 << int(math.log2(iq.size)))
    windowed = iq[:fft_size] * np.hanning(fft_size)
    power = np.abs(np.fft.fftshift(np.fft.fft(windowed))) ** 2 + 1e-12
    noise_power = float(np.median(power))
    center = fft_size // 2
    half_channel = max(3, int(fft_size * 7_500 / DEFAULT_IQ_SAMPLE_RATE))
    channel_power = float(np.mean(power[center - half_channel : center + half_channel + 1]))
    snr_db = 10.0 * math.log10(max(channel_power / noise_power, 1e-9))
    noise_floor_dbm = rssi_dbm - max(0.0, snr_db)

    groups = np.array_split(10.0 * np.log10(power), spectrum_bins)
    spectrum_db = np.array([float(np.mean(group)) for group in groups])
    low, high = np.percentile(spectrum_db, [10, 99])
    scale = max(1.0, float(high - low))
    spectrum = np.clip((spectrum_db - low) / scale, 0.0, 1.0)

    return {
        "frequency_mhz": float(frequency_mhz),
        "rssi_dbm": round(rssi_dbm, 1),
        "snr_db": round(snr_db, 1),
        "noise_floor_dbm": round(noise_floor_dbm, 1),
        "spectrum": [round(float(value), 3) for value in spectrum],
    }


class RTLSDRSegmenter(SDRSegmenter):
    """Read RTL-SDR IQ directly, demodulate NFM, and emit VAD speech WAVs."""

    def __init__(
        self,
        output_dir="./segments",
        dll_path=None,
        center_frequency_mhz=438.5,
        channel_id="ch-main",
        iq_sample_rate=DEFAULT_IQ_SAMPLE_RATE,
        squelch_snr_db=8.0,
        rssi_offset_db=-50.0,
        vad_aggressiveness=2,
    ):
        super().__init__(
            output_dir=output_dir,
            device="rtl-sdr-direct",
            vad_aggressiveness=vad_aggressiveness,
        )
        self.center_frequency_mhz = float(center_frequency_mhz)
        self.channel_id = str(channel_id)
        self.iq_sample_rate = int(iq_sample_rate)
        self.squelch_snr_db = float(squelch_snr_db)
        self.rssi_offset_db = float(rssi_offset_db)
        self.telemetry_callback = None
        self._pcm_samples = np.empty(0, dtype=np.int16)
        self._device_handle = ctypes.c_void_p()
        self._last_telemetry = 0.0
        self._dll_path = Path(dll_path) if dll_path else self._default_dll_path()
        self._rtl = None

    @staticmethod
    def _default_dll_path():
        project_root = Path(__file__).resolve().parents[2]
        return project_root / "tools" / "sdr" / "sdrpp" / "sdrpp_windows_x64" / "rtlsdr.dll"

    def _load_rtl(self):
        if self._rtl is not None:
            return self._rtl
        if not self._dll_path.exists():
            raise RuntimeError(f"rtlsdr.dll not found: {self._dll_path}")
        if os.name == "nt":
            os.add_dll_directory(str(self._dll_path.parent))
        rtl = ctypes.CDLL(str(self._dll_path))
        rtl.rtlsdr_open.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32]
        rtl.rtlsdr_open.restype = ctypes.c_int
        rtl.rtlsdr_close.argtypes = [ctypes.c_void_p]
        rtl.rtlsdr_close.restype = ctypes.c_int
        rtl.rtlsdr_set_sample_rate.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        rtl.rtlsdr_set_sample_rate.restype = ctypes.c_int
        rtl.rtlsdr_set_center_freq.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        rtl.rtlsdr_set_center_freq.restype = ctypes.c_int
        rtl.rtlsdr_set_tuner_gain_mode.argtypes = [ctypes.c_void_p, ctypes.c_int]
        rtl.rtlsdr_set_tuner_gain_mode.restype = ctypes.c_int
        rtl.rtlsdr_reset_buffer.argtypes = [ctypes.c_void_p]
        rtl.rtlsdr_reset_buffer.restype = ctypes.c_int
        rtl.rtlsdr_read_sync.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
        ]
        rtl.rtlsdr_read_sync.restype = ctypes.c_int
        self._rtl = rtl
        return rtl

    def _configure_device(self, rtl):
        checks = [
            ("open", rtl.rtlsdr_open(ctypes.byref(self._device_handle), 0)),
        ]
        if checks[0][1] != 0:
            raise RuntimeError(
                f"cannot open RTL-SDR (code {checks[0][1]}). Close SDR++ or another receiver first"
            )
        checks.extend(
            [
                ("sample rate", rtl.rtlsdr_set_sample_rate(self._device_handle, self.iq_sample_rate)),
                (
                    "center frequency",
                    rtl.rtlsdr_set_center_freq(
                        self._device_handle, int(self.center_frequency_mhz * 1_000_000)
                    ),
                ),
                ("automatic gain", rtl.rtlsdr_set_tuner_gain_mode(self._device_handle, 0)),
                ("buffer reset", rtl.rtlsdr_reset_buffer(self._device_handle)),
            ]
        )
        failed = [(name, code) for name, code in checks if code != 0]
        if failed:
            raise RuntimeError(f"RTL-SDR configuration failed: {failed}")

    def _run_stream(self):
        rtl = None
        try:
            rtl = self._load_rtl()
            self._configure_device(rtl)
            print(
                f"● RTL-SDR direct NFM: {self.center_frequency_mhz:.3f} MHz, "
                f"{self.iq_sample_rate / 1_000_000:.3f} MS/s"
            )
            read_buffer = (ctypes.c_ubyte * DEFAULT_READ_BYTES)()
            n_read = ctypes.c_int()
            while self.running:
                result = rtl.rtlsdr_read_sync(
                    self._device_handle,
                    read_buffer,
                    len(read_buffer),
                    ctypes.byref(n_read),
                )
                if result != 0:
                    raise RuntimeError(f"RTL-SDR read failed: {result}")
                raw = np.ctypeslib.as_array(read_buffer)[: n_read.value].copy()
                telemetry = measure_rf(
                    raw,
                    frequency_mhz=self.center_frequency_mhz,
                    rssi_offset_db=self.rssi_offset_db,
                )
                telemetry.update(
                    {
                        "channel_id": self.channel_id,
                        "source": "rtl-sdr-estimated",
                        "squelch_open": telemetry["snr_db"] >= self.squelch_snr_db,
                    }
                )
                if time.time() - self._last_telemetry >= 0.5:
                    self._last_telemetry = time.time()
                    if self.telemetry_callback:
                        self.telemetry_callback(telemetry)

                audio = demodulate_nfm(raw, self.iq_sample_rate)
                if not telemetry["squelch_open"]:
                    audio.fill(0.0)
                elif audio.size:
                    peak = float(np.percentile(np.abs(audio), 99))
                    if peak > 1e-6:
                        audio = np.clip(audio * (0.28 / peak), -0.95, 0.95)
                pcm = (audio * 32767.0).astype(np.int16)
                self._feed_pcm(pcm)
        except Exception as exc:
            print(f"[SDR] RTL-SDR direct error: {exc}")
            self.running = False
        finally:
            if rtl is not None and self._device_handle.value:
                rtl.rtlsdr_close(self._device_handle)
                self._device_handle = ctypes.c_void_p()

    def _feed_pcm(self, samples):
        if samples.size:
            self._pcm_samples = np.concatenate((self._pcm_samples, samples))
        while self._pcm_samples.size >= FRAME_SIZE:
            frame = self._pcm_samples[:FRAME_SIZE]
            self._pcm_samples = self._pcm_samples[FRAME_SIZE:]
            self._process_pcm_frame(frame.astype(np.int16, copy=False).tobytes())

    def get_stats(self):
        stats = super().get_stats()
        stats.update(
            {
                "source": "rtl-sdr-direct",
                "frequency_mhz": self.center_frequency_mhz,
                "iq_sample_rate": self.iq_sample_rate,
                "squelch_snr_db": self.squelch_snr_db,
            }
        )
        return stats

import sys
import types
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))


class FakeVad:
    def __init__(self, *args, **kwargs):
        pass

    def is_speech(self, *args, **kwargs):
        return False


fake_sounddevice = types.SimpleNamespace(
    default=types.SimpleNamespace(device=[0, 0]),
    query_devices=lambda: [
        {"name": "Fake input", "max_input_channels": 1, "max_output_channels": 0}
    ],
)

sys.modules.setdefault("sounddevice", fake_sounddevice)
sys.modules.setdefault("webrtcvad", types.SimpleNamespace(Vad=FakeVad))

import pipeline as pipeline_module
from audio.sdr_capture import SDRSegmenter
from audio.rtl_sdr_capture import RTLSDRSegmenter, demodulate_nfm, measure_rf
from classifier.engine import HybridClassifier
from pipeline import EmergencyPipeline, UNRECOGNIZED_TEXT


class FakeASR:
    def __init__(self, text="北区发生山体滑坡，多人被困，需要紧急救援"):
        self.text = text
        self.calls = 0

    def recognize(self, wav_path):
        self.calls += 1
        return self.text, 0.05, {"language": "zh", "duration": 1.0}


class FakeClassifier:
    def classify(self, text):
        return {
            "severity": "critical",
            "event_type": "自然灾害",
            "location": "北区",
            "keywords": ["滑坡", "被困"],
            "actions": ["派遣救援队"],
            "resources": ["消防救援"],
            "confidence": 0.9,
            "source": "test",
            "summary": "北区发生自然灾害",
        }


def make_pipeline(**config):
    pipe = EmergencyPipeline({"auto_cleanup_segments": False, **config})
    pipe.asr = FakeASR()
    pipe.classifier = FakeClassifier()
    return pipe


def test_audio_callback_only_queues_segment(tmp_path):
    pipe = make_pipeline()

    pipe._on_audio_segment(tmp_path / "segment.wav")

    assert pipe.asr.calls == 0
    assert pipe.segment_queue.qsize() == 1


def test_segment_queue_drops_oldest_when_full(tmp_path):
    pipe = make_pipeline(segment_queue_max=1)

    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    pipe._on_audio_segment(first)
    pipe._on_audio_segment(second)

    queued_path, _ = pipe.segment_queue.get_nowait()
    assert queued_path == second
    assert pipe.stats["dropped_segments"] == 1


def test_processing_deduplicates_normalized_text(tmp_path):
    pipe = make_pipeline(dedup_window=60)

    pipe._process_audio_segment(tmp_path / "one.wav")
    pipe.asr.text = " 北区发生山体滑坡，多人被困，需要紧急救援！ "
    pipe._process_audio_segment(tmp_path / "two.wav")

    assert pipe.stats["total_events"] == 1


def test_unrecognized_asr_text_is_dropped_before_classification(tmp_path):
    pipe = make_pipeline()
    pipe.asr.text = UNRECOGNIZED_TEXT

    pipe._process_audio_segment(tmp_path / "silence.wav")

    assert pipe.stats["total_events"] == 0
    assert pipe.event_queue.empty()


def test_sdr_segmenter_stop_without_started_stream_does_not_crash(tmp_path):
    segmenter = SDRSegmenter(output_dir=tmp_path, device=0)

    segmenter.stop()

    assert segmenter.running is False


def test_sdr_segment_save_only_queues_pcm_for_writer(tmp_path):
    segmenter = SDRSegmenter(output_dir=tmp_path, device=0)
    segmenter.buffer = [b"\x00\x00" * 160]

    segmenter._save_and_reset()

    assert segmenter._write_queue.qsize() == 1
    assert list(tmp_path.glob("*.wav")) == []


def test_direct_rtlsdr_nfm_demodulates_audio_tone():
    sample_rate = 1_024_000
    duration = 0.12
    time_axis = np.arange(int(sample_rate * duration)) / sample_rate
    tone = np.sin(2 * np.pi * 1_000 * time_axis)
    phase = np.cumsum(2 * np.pi * 3_000 * tone / sample_rate)
    iq = np.exp(1j * phase)
    interleaved = np.empty(iq.size * 2, dtype=np.uint8)
    interleaved[0::2] = np.clip(iq.real * 100 + 127.5, 0, 255).astype(np.uint8)
    interleaved[1::2] = np.clip(iq.imag * 100 + 127.5, 0, 255).astype(np.uint8)

    audio = demodulate_nfm(interleaved, sample_rate)
    spectrum = np.abs(np.fft.rfft(audio * np.hanning(audio.size)))
    frequencies = np.fft.rfftfreq(audio.size, 1 / 16_000)
    peak_frequency = frequencies[int(np.argmax(spectrum[1:]) + 1)]

    assert audio.size > 1_500
    assert abs(peak_frequency - 1_000) < 40


def test_direct_rtlsdr_telemetry_has_bounded_spectrum():
    raw = np.tile(np.array([128, 128, 150, 110], dtype=np.uint8), 4_096)

    telemetry = measure_rf(raw, frequency_mhz=438.5)

    assert telemetry["frequency_mhz"] == 438.5
    assert len(telemetry["spectrum"]) == 96
    assert all(0.0 <= value <= 1.0 for value in telemetry["spectrum"])


def test_direct_rtlsdr_segmenter_does_not_open_hardware_during_init(tmp_path):
    segmenter = RTLSDRSegmenter(output_dir=tmp_path, center_frequency_mhz=438.5)

    assert segmenter.running is False
    assert segmenter.device == "rtl-sdr-direct"
    assert segmenter.get_stats()["frequency_mhz"] == 438.5


def test_real_mode_asr_failure_is_fail_fast_by_default(monkeypatch):
    def fail_whisper(*args, **kwargs):
        raise RuntimeError("model missing")

    monkeypatch.setattr(pipeline_module, "WhisperASR", fail_whisper)
    pipe = EmergencyPipeline({"mock_mode": False, "no_gpu": True})

    try:
        pipe._create_asr()
    except RuntimeError as exc:
        assert "ALLOW_MOCK_FALLBACK" in str(exc)
    else:
        raise AssertionError("expected fail-fast when Whisper cannot initialize")


def test_mock_fallback_requires_explicit_allowance(monkeypatch):
    def fail_whisper(*args, **kwargs):
        raise RuntimeError("model missing")

    monkeypatch.setattr(pipeline_module, "WhisperASR", fail_whisper)
    pipe = EmergencyPipeline(
        {"mock_mode": False, "no_gpu": True, "allow_mock_fallback": True}
    )

    asr = pipe._create_asr()

    assert asr.__class__.__name__ == "MockASR"


def test_mock_asr_backend_can_validate_real_capture_without_loading_whisper():
    pipe = EmergencyPipeline({"mock_mode": False, "asr_backend": "mock"})

    asr = pipe._create_asr()

    assert asr.__class__.__name__ == "MockASR"


def test_classifier_extracts_actionable_incident_fields():
    classifier = HybridClassifier()

    result = classifier.classify(
        "北区山体公路发生滑坡，多人被困，需要紧急救援和医疗支援"
    )

    assert result["severity"] == "critical"
    assert result["event_type"] == "地质灾害"
    assert result["location"] == "北区山体公路"
    assert "消防救援" in result["resources"]
    assert result["entities"]["casualty"] == "多人被困"
    assert result["review_required"] is False


def test_pipeline_merges_related_incident_updates():
    pipe = make_pipeline()
    pipe.classifier = HybridClassifier()

    first = pipe.ingest_text(
        "北区山体公路发生滑坡，多人被困，需要紧急救援", allow_duplicate=True
    )
    second = pipe.ingest_text(
        "北区山体公路补充报告，三人受伤，仍有滚石风险", allow_duplicate=True
    )

    assert first["incident_id"] == second["incident_id"]
    assert second["incident_event_count"] == 2
    assert len(pipe.list_incidents()) == 1


def test_pipeline_persists_and_restores_incidents(tmp_path):
    db_path = tmp_path / "events.sqlite3"
    pipe = make_pipeline(storage_path=db_path)

    event = pipe.ingest_text(
        "北区山体公路发生滑坡，多人被困，需要紧急救援", allow_duplicate=True
    )
    pipe.store.close()

    restored = EmergencyPipeline({"storage_path": db_path})
    restored._load_persisted_incidents()

    incidents = restored.list_incidents()
    assert len(incidents) == 1
    assert incidents[0]["incident_id"] == event["incident_id"]
    assert restored.list_events(limit=10)[0]["id"] == event["id"]
    restored.store.close()


def test_pipeline_attaches_radio_channel_to_radio_events():
    pipe = make_pipeline(mock_mode=True)

    event = pipe.ingest_text(
        "Fire command channel received a new report",
        source="radio",
        allow_duplicate=True,
        radio_channel="ch-fire",
    )

    assert event["radio_channel"]["id"] == "ch-fire"
    snapshot = pipe.get_radio_snapshot()
    active = next(channel for channel in snapshot["channels"] if channel["id"] == "ch-fire")
    assert active["status"] == "receiving"
    assert active["spectrum"]


def test_radio_telemetry_update_drives_snapshot():
    pipe = make_pipeline(radio_channels="ch-a|Alpha|438.500|NFM")

    channel = pipe.update_radio_telemetry(
        {
            "channel_id": "ch-a",
            "rssi_dbm": -72.4,
            "snr_db": 21.2,
            "noise_floor_dbm": -113.0,
            "squelch_open": True,
            "spectrum": [0, 0.5, 1.0],
        }
    )
    snapshot = pipe.get_radio_snapshot()

    assert channel["rssi_dbm"] == -72.4
    assert snapshot["active_channel_id"] == "ch-a"
    assert snapshot["channels"][0]["status"] == "receiving"
    assert snapshot["channels"][0]["spectrum"] == [0.0, 0.5, 1.0]

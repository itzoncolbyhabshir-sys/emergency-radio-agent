"""Core processing pipeline for the emergency radio agent.

Flow:
    audio segment -> ASR -> classifier -> event queue -> WebSocket broadcaster

The audio capture callback must stay fast. It only enqueues segment paths; a
dedicated worker thread performs ASR and classification so live capture does
not stall while Whisper is running.
"""

import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Lock, Thread

sys.path.insert(0, str(Path(__file__).parent))

from asr.whisper_engine import MockASR, WhisperASR
from audio.rtl_sdr_capture import RTLSDRSegmenter
from audio.sdr_capture import MockAudioSegmenter, SDRSegmenter
from classifier.engine import HybridClassifier, LLMFallbackClassifier
from radio_monitor import RadioChannelMonitor, parse_radio_channels
from storage import EventStore

UNRECOGNIZED_TEXT = "[\u672a\u8bc6\u522b\u5230\u8bed\u97f3]"


class EmergencyPipeline:
    """Coordinates audio ingestion, ASR, classification, and event dispatch."""

    def __init__(self, config=None):
        self.config = config or {}

        self.mock_mode = self.config.get("mock_mode", False)
        self.no_gpu = self.config.get("no_gpu", False)

        self.segmenter = None
        self.asr = None
        self.classifier = None

        self.event_queue = asyncio.Queue(maxsize=100)
        self.transcript_queue = asyncio.Queue(maxsize=100)
        self.segment_queue = Queue(maxsize=self.config.get("segment_queue_max", 20))
        self._loop = None
        self._worker_thread = None

        self.running = False
        self.stats = {
            "total_events": 0,
            "total_latency": 0.0,
            "avg_latency": 0.0,
            "total_transcripts": 0,
            "dropped_segments": 0,
            "start_time": None,
        }
        self._stats_lock = Lock()

        self._transcripts = []
        self._transcript_lock = Lock()
        self._max_transcripts = self.config.get("max_transcripts", 200)
        self._recent_events = []
        self._dedup_window = self.config.get("dedup_window", 3.0)
        self._max_recent = 50
        self._incident_window = self.config.get("incident_merge_window", 15 * 60)
        self.incidents = {}
        self._incident_lock = Lock()
        self.radio = RadioChannelMonitor(
            channels=parse_radio_channels(self.config.get("radio_channels")),
            mock_mode=self.mock_mode,
            stale_after_seconds=self.config.get("radio_stale_after_seconds", 8.0),
        )

        self.sounds_dir = Path(__file__).parent / "sounds"
        self.store = None
        storage_path = self.config.get("storage_path")
        if storage_path:
            self.store = EventStore(storage_path)

    def initialize(self):
        """Initialize capture, ASR, classifier, and callbacks."""
        print("=" * 60)
        print("[Pipeline] Initializing emergency radio agent")
        print("=" * 60)

        if self.mock_mode:
            mock_dir = self.config.get("mock_audio_dir", "./mock_audio")
            interval = self.config.get("mock_interval", 5.0)
            self.segmenter = MockAudioSegmenter(mock_dir=mock_dir, interval=interval)
            print("[Pipeline] Audio: mock segmenter")
        elif self.config.get("sdr_source", "audio") == "rtl":
            output_dir = self.config.get("segments_dir", "./segments")
            self.segmenter = RTLSDRSegmenter(
                output_dir=output_dir,
                dll_path=self.config.get("rtl_dll_path"),
                center_frequency_mhz=self.config.get("rtl_frequency_mhz", 438.5),
                channel_id=self.config.get("rtl_channel_id", "ch-main"),
                iq_sample_rate=self.config.get("rtl_sample_rate", 1_024_000),
                squelch_snr_db=self.config.get("rtl_squelch_snr_db", 8.0),
                rssi_offset_db=self.config.get("rtl_rssi_offset_db", -50.0),
            )
            self.segmenter.telemetry_callback = self.update_radio_telemetry
            print(
                f"[Pipeline] Audio: direct RTL-SDR at "
                f"{self.segmenter.center_frequency_mhz:.3f} MHz, output={output_dir}"
            )
        else:
            device = self.config.get("audio_device")
            output_dir = self.config.get("segments_dir", "./segments")
            self.segmenter = SDRSegmenter(output_dir=output_dir, device=device)
            print(
                f"[Pipeline] Audio: device={self.segmenter.device}, output={output_dir}"
            )

        self.asr = self._create_asr()

        rules_path = self.config.get("rules_path")
        if self.config.get("llm_fallback_enabled", False):
            self.classifier = LLMFallbackClassifier(
                rules_path=rules_path,
                enabled=True,
                base_url=self.config.get("ollama_base_url"),
                model=self.config.get("ollama_model", "qwen2.5:7b"),
                timeout=self.config.get("llm_timeout", 4.0),
                confidence_threshold=self.config.get("llm_confidence_threshold", 0.58),
                summary_enabled=self.config.get("llm_summary_enabled", True),
            )
            print("[Pipeline] Classifier loaded: rule fast path + LLM fallback")
        else:
            self.classifier = HybridClassifier(rules_path=rules_path)
            print("[Pipeline] Classifier loaded: local rules")

        self._load_persisted_incidents()

        self.segmenter.on_segment_callback = self._on_audio_segment

        print("=" * 60)
        print("[Pipeline] Initialization complete")
        print(f"  mode: {'mock' if self.mock_mode else 'sdr'}")
        print(f"  asr: {self.asr.get_model_info()}")
        print("=" * 60)

    def _create_asr(self):
        if self.mock_mode or self.config.get("asr_backend") == "mock":
            print("[Pipeline] ASR: mock")
            return MockASR()

        if self.no_gpu:
            try:
                return WhisperASR(
                    model_size=self.config.get("model_size", "small"),
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=self.config.get("cpu_threads", 8),
                    no_speech_threshold=self.config.get("asr_no_speech_threshold", 0.6),
                    avg_logprob_threshold=self.config.get(
                        "asr_avg_logprob_threshold", -1.0
                    ),
                    condition_on_previous_text=self.config.get(
                        "asr_condition_on_previous_text", False
                    ),
                )
            except Exception as exc:
                return self._mock_asr_or_raise("CPU Whisper", exc)

        try:
            return WhisperASR(
                model_size=self.config.get("model_size", "medium"),
                device="cuda",
                compute_type=self.config.get("compute_type", "int8_float16"),
                no_speech_threshold=self.config.get("asr_no_speech_threshold", 0.6),
                avg_logprob_threshold=self.config.get(
                    "asr_avg_logprob_threshold", -1.0
                ),
                condition_on_previous_text=self.config.get(
                    "asr_condition_on_previous_text", False
                ),
            )
        except Exception as exc:
            print(f"[Pipeline] GPU Whisper failed; trying CPU: {exc}")
            try:
                return WhisperASR(
                    model_size="small",
                    device="cpu",
                    compute_type="int8",
                    no_speech_threshold=self.config.get("asr_no_speech_threshold", 0.6),
                    avg_logprob_threshold=self.config.get(
                        "asr_avg_logprob_threshold", -1.0
                    ),
                    condition_on_previous_text=self.config.get(
                        "asr_condition_on_previous_text", False
                    ),
                )
            except Exception as cpu_exc:
                return self._mock_asr_or_raise("CPU Whisper", cpu_exc)

    def _mock_asr_or_raise(self, context, exc):
        if self.config.get("allow_mock_fallback", False):
            print(f"[Pipeline] {context} failed; falling back to mock ASR: {exc}")
            return MockASR()
        raise RuntimeError(
            f"{context} initialization failed and ALLOW_MOCK_FALLBACK is false"
        ) from exc

    def _on_audio_segment(self, wav_path):
        """Queue a finished audio segment without blocking audio capture."""
        try:
            self.segment_queue.put_nowait((Path(wav_path), time.time()))
            return
        except Full:
            pass

        try:
            dropped_path, _ = self.segment_queue.get_nowait()
            self._cleanup_segment(dropped_path)
        except Empty:
            pass

        try:
            self.segment_queue.put_nowait((Path(wav_path), time.time()))
        except Full:
            self._cleanup_segment(wav_path)

        with self._stats_lock:
            self.stats["dropped_segments"] += 1

    def _worker_loop(self):
        while self.running or not self.segment_queue.empty():
            try:
                wav_path, queued_at = self.segment_queue.get(timeout=0.2)
            except Empty:
                continue

            try:
                self._process_audio_segment(wav_path, queued_at)
            finally:
                self.segment_queue.task_done()

    def _process_audio_segment(self, wav_path, queued_at=None):
        """Run ASR and classification outside the audio callback thread."""
        started_at = time.time()

        try:
            text, asr_latency, asr_info = self.asr.recognize(str(wav_path))
            total_latency = time.time() - started_at
            transcript = self._record_transcript(
                text=text,
                wav_path=wav_path,
                asr_latency=asr_latency,
                asr_info=asr_info,
                pipeline_latency=total_latency,
                queue_wait=max(0.0, started_at - queued_at) if queued_at else 0.0,
                source="radio",
            )
            if not text or text == UNRECOGNIZED_TEXT:
                print(
                    f"[Pipeline] Transcript #{transcript['sequence']} "
                    f"needs review: {transcript['text']}"
                )
                return

            event = self.ingest_text(
                text,
                source="radio",
                asr_latency=asr_latency,
                pipeline_latency=total_latency,
                queue_wait=max(0.0, started_at - queued_at) if queued_at else 0.0,
            )
            if not event:
                return

            print(
                f"[Pipeline] Event #{self.stats['total_events']} "
                f"[{event['severity'].upper()}] {text[:40]}{'...' if len(text) > 40 else ''}"
            )

        except Exception as exc:
            print(f"[Pipeline] Segment processing error: {exc}")
        finally:
            self._cleanup_segment(wav_path)

    def ingest_text(
        self,
        text,
        source="manual",
        asr_latency=0.0,
        pipeline_latency=0.0,
        queue_wait=0.0,
        allow_duplicate=False,
        radio_channel=None,
    ):
        """Classify raw text and publish an incident event."""
        text = str(text or "").strip()
        if not text:
            return None
        if not allow_duplicate and self._is_duplicate(text):
            print(f"[Pipeline] Duplicate skipped: {text[:30]}...")
            return None

        radio_state = None
        if source in {"radio", "demo"}:
            radio_state = self.radio.mark_received(text=text, channel_id=radio_channel)

        classification = self.classifier.classify(text)
        event = {
            "id": time.time_ns(),
            "timestamp": datetime.now().isoformat(),
            "raw_text": text,
            "source_channel": source,
            "asr_latency": round(asr_latency, 3),
            "pipeline_latency": round(pipeline_latency, 3),
            "queue_wait": round(queue_wait, 3),
            **classification,
        }
        if radio_state:
            event["radio_channel"] = {
                "id": radio_state["id"],
                "name": radio_state["name"],
                "frequency_mhz": radio_state["frequency_mhz"],
                "status": radio_state["status"],
            }
        event = self._attach_incident(event)
        self._save_event(event)
        self._publish_event(event)
        self._record_event_latency(pipeline_latency)
        return event

    def _record_transcript(
        self,
        text,
        wav_path,
        asr_latency=0.0,
        asr_info=None,
        pipeline_latency=0.0,
        queue_wait=0.0,
        source="radio",
    ):
        """Record every ASR attempt for operator review before classification."""
        asr_info = asr_info or {}
        text = str(text or "").strip()
        usable = bool(text and text != UNRECOGNIZED_TEXT)
        display_text = text if text else UNRECOGNIZED_TEXT
        with self._stats_lock:
            self.stats["total_transcripts"] = self.stats.get("total_transcripts", 0) + 1
            sequence = self.stats["total_transcripts"]

        transcript = {
            "id": f"TR-{time.time_ns()}",
            "sequence": sequence,
            "timestamp": datetime.now().isoformat(),
            "text": display_text,
            "source": source,
            "usable": usable,
            "review_status": "ready" if usable else "needs_audio_review",
            "asr_latency": round(float(asr_latency or 0.0), 3),
            "pipeline_latency": round(float(pipeline_latency or 0.0), 3),
            "queue_wait": round(float(queue_wait or 0.0), 3),
            "audio_file": Path(str(wav_path)).name if wav_path else "",
            "language": asr_info.get("language"),
            "language_probability": round(
                float(asr_info.get("language_probability") or 0.0), 3
            ),
            "duration": round(float(asr_info.get("duration") or 0.0), 2),
            "max_no_speech_prob": round(
                float(asr_info.get("max_no_speech_prob") or 0.0), 3
            ),
            "min_avg_logprob": round(
                float(asr_info.get("min_avg_logprob") or 0.0), 3
            ),
        }

        with self._transcript_lock:
            self._transcripts.insert(0, transcript)
            self._transcripts = self._transcripts[: self._max_transcripts]

        self._publish_transcript(transcript)
        return transcript

    def _publish_transcript(self, transcript):
        if not self._loop or not self._loop.is_running():
            try:
                self.transcript_queue.put_nowait(transcript)
            except asyncio.QueueFull:
                pass
            return

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is self._loop:
            asyncio.create_task(self._enqueue_transcript(transcript))
        else:
            asyncio.run_coroutine_threadsafe(
                self._enqueue_transcript(transcript), self._loop
            )

    def _publish_event(self, event):
        if not self._loop or not self._loop.is_running():
            try:
                self.event_queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
            return

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is self._loop:
            asyncio.create_task(self._enqueue_event(event))
        else:
            asyncio.run_coroutine_threadsafe(self._enqueue_event(event), self._loop)

    def _record_event_latency(self, latency):
        with self._stats_lock:
            self.stats["total_events"] += 1
            self.stats["total_latency"] += latency
            self.stats["avg_latency"] = (
                self.stats["total_latency"] / self.stats["total_events"]
            )

    def _attach_incident(self, event):
        now = time.time()
        with self._incident_lock:
            incident = self._find_matching_incident(event, now)
            if incident is None:
                incident_id = f"INC-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{len(self.incidents) + 1:03d}"
                incident = {
                    "incident_id": incident_id,
                    "first_seen": event["timestamp"],
                    "last_seen": event["timestamp"],
                    "location": event["location"],
                    "event_type": event["event_type"],
                    "severity": event["severity"],
                    "status": "new",
                    "event_count": 0,
                    "summary": event.get("summary", ""),
                    "resources": [],
                    "actions": [],
                    "latest_text": "",
                    "history": [],
                    "updated_at_monotonic": now,
                }
                self.incidents[incident_id] = incident
                event["is_update"] = False
            else:
                event["is_update"] = True

            incident["last_seen"] = event["timestamp"]
            incident["updated_at_monotonic"] = now
            incident["event_count"] += 1
            incident["latest_text"] = event["raw_text"]
            incident["history"].append(
                {
                    "timestamp": event["timestamp"],
                    "text": event["raw_text"],
                    "severity": event["severity"],
                    "event_type": event["event_type"],
                    "location": event["location"],
                }
            )
            incident["history"] = incident["history"][-12:]
            incident["summary"] = event.get("summary") or incident["summary"]
            incident["severity"] = self._max_severity(
                incident["severity"], event["severity"]
            )
            incident["resources"] = self._merge_unique(
                incident["resources"], event.get("resources", [])
            )
            incident["actions"] = self._merge_unique(
                incident["actions"], event.get("actions", [])
            )[:6]
            incident["summary"] = self._summarize_incident(incident)

            event["incident_id"] = incident["incident_id"]
            event["incident_status"] = incident["status"]
            event["incident_event_count"] = incident["event_count"]
            event["incident_summary"] = incident["summary"]
            self._save_incident(incident)
            return event

    def _find_matching_incident(self, event, now):
        for incident in self.incidents.values():
            if now - incident.get("updated_at_monotonic", 0) > self._incident_window:
                continue
            same_type = incident["event_type"] == event["event_type"]
            same_location = (
                event["location"] != "未指明"
                and incident["location"] != "未指明"
                and event["location"] == incident["location"]
            )
            unknown_location_match = (
                event["location"] == "未指明"
                and incident["location"] == "未指明"
                and same_type
            )
            if same_type and (same_location or unknown_location_match):
                return incident
        return None

    def _max_severity(self, left, right):
        ranks = {"minor": 0, "normal": 1, "urgent": 2, "critical": 3}
        return left if ranks.get(left, 0) >= ranks.get(right, 0) else right

    def _merge_unique(self, left, right):
        merged = list(left)
        for item in right:
            if item not in merged:
                merged.append(item)
        return merged

    def list_incidents(self):
        with self._incident_lock:
            return sorted(
                [dict(incident) for incident in self.incidents.values()],
                key=lambda item: item["last_seen"],
                reverse=True,
            )

    def update_incident_status(self, incident_id, status):
        allowed = {"new", "confirmed", "dispatched", "handling", "resolved"}
        if status not in allowed:
            raise ValueError(f"unsupported status: {status}")
        with self._incident_lock:
            incident = self.incidents.get(incident_id)
            if not incident:
                return None
            incident["status"] = status
            incident["last_seen"] = datetime.now().isoformat()
            incident["updated_at_monotonic"] = time.time()
            self._save_incident(incident)
            return dict(incident)

    def list_events(self, limit=100, offset=0):
        if not self.store:
            return []
        return self.store.list_events(limit=limit, offset=offset)

    def list_transcripts(self, limit=100):
        with self._transcript_lock:
            return [dict(item) for item in self._transcripts[: int(limit or 100)]]

    def get_radio_snapshot(self):
        return self.radio.snapshot()

    def update_radio_telemetry(self, payload):
        return self.radio.update_telemetry(payload)

    def _load_persisted_incidents(self):
        if not self.store:
            return
        incidents = self.store.load_incidents()
        with self._incident_lock:
            self.incidents = {
                incident["incident_id"]: incident
                for incident in incidents
                if incident.get("incident_id")
            }
        if incidents:
            print(f"[Pipeline] Restored {len(incidents)} incidents from SQLite")

    def _save_event(self, event):
        if not self.store:
            return
        try:
            self.store.save_event(event)
        except Exception as exc:
            print(f"[Pipeline] Failed to persist event: {exc}")

    def _save_incident(self, incident):
        if not self.store:
            return
        try:
            self.store.save_incident(dict(incident))
        except Exception as exc:
            print(f"[Pipeline] Failed to persist incident: {exc}")

    def _summarize_incident(self, incident):
        summarizer = getattr(self.classifier, "summarize_incident", None)
        if not summarizer:
            return incident.get("summary", "")
        try:
            return summarizer(incident)
        except Exception as exc:
            print(f"[Pipeline] Incident summary failed: {exc}")
            return incident.get("summary", "")

    def _is_duplicate(self, text):
        key = self._dedup_key(text)
        now = time.time()
        with self._stats_lock:
            self._recent_events = [
                (event_key, ts)
                for event_key, ts in self._recent_events
                if now - ts < self._dedup_window
            ]
            if any(event_key == key for event_key, _ in self._recent_events):
                return True
            self._recent_events.append((key, now))
            if len(self._recent_events) > self._max_recent:
                self._recent_events.pop(0)
            return False

    def _dedup_key(self, text):
        normalized = "".join(ch for ch in str(text).strip().lower() if ch.isalnum())
        return hashlib.sha1(normalized[:40].encode("utf-8")).hexdigest()

    def _cleanup_segment(self, wav_path):
        if not self.config.get("auto_cleanup_segments", True):
            return
        if self.mock_mode:
            return
        try:
            os.remove(wav_path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f"[Pipeline] Failed to remove segment {wav_path}: {exc}")

    async def _enqueue_event(self, event):
        """Put an event into the async queue, dropping the oldest if full."""
        try:
            self.event_queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                self.event_queue.get_nowait()
                self.event_queue.put_nowait(event)
            except asyncio.QueueEmpty:
                pass

    async def _enqueue_transcript(self, transcript):
        """Put a transcript into the async queue, dropping the oldest if full."""
        try:
            self.transcript_queue.put_nowait(transcript)
        except asyncio.QueueFull:
            try:
                self.transcript_queue.get_nowait()
                self.transcript_queue.put_nowait(transcript)
            except asyncio.QueueEmpty:
                pass

    def start(self, loop: asyncio.AbstractEventLoop = None):
        """Start worker and audio capture."""
        self._loop = loop or asyncio.get_event_loop()
        self.running = True
        self.stats["start_time"] = datetime.now().isoformat()
        self._worker_thread = Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        self.segmenter.start()
        print("[Pipeline] Started")

    def stop(self):
        """Stop capture and drain queued segments."""
        self.running = False
        if self.segmenter:
            self.segmenter.stop()
        if self._worker_thread:
            self._worker_thread.join(timeout=5)
        if self.store:
            self.store.close()
        print("[Pipeline] Stopped")

    def get_stats(self):
        """Return runtime counters."""
        with self._stats_lock:
            return dict(self.stats)

    def get_queue_size(self):
        """Return async event queue and audio segment backlog sizes."""
        return {
            "events": self.event_queue.qsize(),
            "transcripts": self.transcript_queue.qsize(),
            "segments": self.segment_queue.qsize(),
        }


_pipeline = None


def get_pipeline():
    return _pipeline


def create_pipeline(config=None):
    global _pipeline
    _pipeline = EmergencyPipeline(config)
    _pipeline.initialize()
    return _pipeline


if __name__ == "__main__":
    print("Standalone pipeline smoke test (mock mode)...")
    config = {"mock_mode": True, "mock_interval": 3.0}
    pipe = create_pipeline(config)

    async def consume():
        while True:
            event = await pipe.event_queue.get()
            print("\n--- Event ---")
            print(json.dumps(event, ensure_ascii=False, indent=2))

    loop = asyncio.new_event_loop()
    pipe.start(loop)
    loop.run_until_complete(consume())

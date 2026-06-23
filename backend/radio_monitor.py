"""Radio channel telemetry state for the dashboard.

The audio pipeline only receives demodulated audio. Real RF telemetry such as
RSSI, SNR, and spectrum bins must come from the receiver side (SDR driver,
rtl_power, GNU Radio, SDR#, or another bridge). This module keeps that receiver
telemetry as first-class state and only synthesizes values in mock mode.
"""

from __future__ import annotations

import math
import time
from threading import Lock


DEFAULT_CHANNELS = [
    {
        "id": "ch-public-test",
        "name": "\u516c\u4f17\u6d4b\u8bd5",
        "frequency_mhz": 409.7500,
        "modulation": "NFM",
    },
    {
        "id": "ch-main",
        "name": "\u5e94\u6025\u4e3b\u4fe1\u9053",
        "frequency_mhz": 438.500,
        "modulation": "NFM",
    },
    {
        "id": "ch-fire",
        "name": "\u6d88\u9632\u8054\u52a8",
        "frequency_mhz": 438.650,
        "modulation": "NFM",
    },
    {
        "id": "ch-rescue",
        "name": "\u6551\u63f4\u4fdd\u969c",
        "frequency_mhz": 439.125,
        "modulation": "NFM",
    },
    {
        "id": "ch-command",
        "name": "\u6307\u6325\u5907\u9891",
        "frequency_mhz": 439.350,
        "modulation": "NFM",
    },
]


CHANNEL_HINTS = [
    ("\u6d88\u9632", "ch-fire"),
    ("\u8d77\u706b", "ch-fire"),
    ("\u706b", "ch-fire"),
    ("\u6551\u63f4", "ch-rescue"),
    ("\u88ab\u56f0", "ch-rescue"),
    ("\u6307\u6325", "ch-command"),
    ("\u57fa\u7ad9", "ch-command"),
    ("\u901a\u4fe1", "ch-command"),
]


MOCK_BOOST_WORDS = [
    "\u706b",
    "\u6ed1\u5761",
    "\u88ab\u56f0",
    "\u79ef\u6c34",
    "\u4e2d\u65ad",
]


def parse_radio_channels(value):
    """Parse RADIO_CHANNELS.

    Format:
        id|name|frequency_mhz|modulation;id|name|frequency_mhz|modulation
    """
    if not value:
        return [dict(channel) for channel in DEFAULT_CHANNELS]

    channels = []
    for raw in str(value).split(";"):
        parts = [part.strip() for part in raw.split("|")]
        if len(parts) < 3:
            continue
        try:
            frequency_mhz = float(parts[2])
        except ValueError:
            continue
        channels.append(
            {
                "id": parts[0] or f"ch-{len(channels) + 1}",
                "name": parts[1] or parts[0] or f"channel-{len(channels) + 1}",
                "frequency_mhz": frequency_mhz,
                "modulation": parts[3] if len(parts) > 3 and parts[3] else "NFM",
            }
        )
    return channels or [dict(channel) for channel in DEFAULT_CHANNELS]


class RadioChannelMonitor:
    """Keeps multi-channel RF telemetry for the frontend."""

    def __init__(self, channels=None, mock_mode=False, stale_after_seconds=8.0):
        self.mock_mode = mock_mode
        self.stale_after_seconds = float(stale_after_seconds)
        self._lock = Lock()
        self._scan_index = 0
        self.active_channel_id = None
        self.channels = {}

        for channel in channels or DEFAULT_CHANNELS:
            self._ensure_channel(channel)
        if self.channels:
            self.active_channel_id = next(iter(self.channels))

    def snapshot(self):
        now = time.time()
        with self._lock:
            channels = [self._public_channel(channel, now) for channel in self.channels.values()]
            return {
                "active_channel_id": self.active_channel_id,
                "channels": channels,
                "mode": "mock" if self.mock_mode else "receiver",
                "updated_at": now,
            }

    def update_telemetry(self, payload, source="receiver"):
        """Update channel telemetry from a real receiver bridge."""
        if not isinstance(payload, dict):
            raise ValueError("radio telemetry payload must be an object")

        channel_id = str(payload.get("channel_id") or payload.get("id") or "").strip()
        if not channel_id:
            raise ValueError("channel_id is required")

        with self._lock:
            channel = self.channels.get(channel_id)
            if channel is None:
                channel = self._ensure_channel(
                    {
                        "id": channel_id,
                        "name": payload.get("name") or channel_id,
                        "frequency_mhz": payload.get("frequency_mhz", 0),
                        "modulation": payload.get("modulation", "NFM"),
                    }
                )
            self._apply_payload(channel, payload, source)
            self.active_channel_id = channel_id
            return self._public_channel(channel, time.time())

    def mark_received(self, text="", channel_id=None):
        """Mark a channel as having received audio.

        In mock mode this also generates demonstrative telemetry. In real mode
        it only updates last text/time unless receiver telemetry is posted.
        """
        with self._lock:
            channel = self._select_channel(text=text, channel_id=channel_id)
            now = time.time()
            channel["last_seen"] = now
            channel["last_text"] = str(text or "")[:120]
            channel["squelch_open"] = True
            channel["sample_count"] += 1
            self.active_channel_id = channel["id"]

            if self.mock_mode:
                strength = 0.45 + (channel["sample_count"] % 7) * 0.055
                if any(word in text for word in MOCK_BOOST_WORDS):
                    strength += 0.18
                rssi_dbm = -112 + min(strength, 0.95) * 58
                snr_db = 7 + min(strength, 0.95) * 24
                channel.update(
                    {
                        "rssi_dbm": round(rssi_dbm, 1),
                        "snr_db": round(snr_db, 1),
                        "noise_floor_dbm": round(-116 + math.sin(now / 3) * 3, 1),
                        "spectrum": self._mock_spectrum(channel, strength, now),
                        "telemetry_source": "mock-receiver",
                    }
                )

            return self._public_channel(channel, now)

    def _ensure_channel(self, channel):
        channel_id = str(channel.get("id") or f"ch-{len(self.channels) + 1}").strip()
        state = {
            "id": channel_id,
            "name": str(channel.get("name") or channel_id),
            "frequency_mhz": self._float(channel.get("frequency_mhz"), 0.0),
            "modulation": str(channel.get("modulation") or "NFM"),
            "enabled": bool(channel.get("enabled", True)),
            "rssi_dbm": None,
            "snr_db": None,
            "noise_floor_dbm": None,
            "spectrum": [],
            "squelch_open": False,
            "last_seen": None,
            "last_text": "",
            "sample_count": 0,
            "telemetry_source": "none",
        }
        self.channels[channel_id] = state
        return state

    def _apply_payload(self, channel, payload, source):
        now = time.time()
        for key in ["name", "modulation"]:
            if payload.get(key):
                channel[key] = str(payload[key])
        if "frequency_mhz" in payload:
            channel["frequency_mhz"] = self._float(payload.get("frequency_mhz"), channel["frequency_mhz"])
        for key in ["rssi_dbm", "snr_db", "noise_floor_dbm"]:
            if key in payload and payload.get(key) is not None:
                channel[key] = round(self._float(payload.get(key), channel.get(key) or 0.0), 1)
        if "spectrum" in payload:
            channel["spectrum"] = self._clean_spectrum(payload.get("spectrum"))
        if "squelch_open" in payload:
            channel["squelch_open"] = bool(payload.get("squelch_open"))
        if "last_text" in payload:
            channel["last_text"] = str(payload.get("last_text") or "")[:120]
        channel["last_seen"] = now
        channel["sample_count"] += 1
        channel["telemetry_source"] = str(payload.get("source") or source)

    def _select_channel(self, text="", channel_id=None):
        if channel_id and channel_id in self.channels:
            return self.channels[channel_id]
        text = str(text or "")
        for keyword, preferred in CHANNEL_HINTS:
            if keyword in text and preferred in self.channels:
                return self.channels[preferred]

        enabled = [channel for channel in self.channels.values() if channel.get("enabled")]
        if not enabled:
            enabled = list(self.channels.values())
        channel = enabled[self._scan_index % len(enabled)]
        self._scan_index += 1
        return channel

    def _public_channel(self, channel, now):
        last_seen = channel.get("last_seen")
        age = None if last_seen is None else max(0.0, now - last_seen)
        if last_seen is None:
            status = "waiting"
        elif age > self.stale_after_seconds:
            status = "stale"
        elif channel.get("squelch_open"):
            status = "receiving"
        else:
            status = "scanning"
        return {
            "id": channel["id"],
            "name": channel["name"],
            "frequency_mhz": channel["frequency_mhz"],
            "modulation": channel["modulation"],
            "enabled": channel["enabled"],
            "status": status,
            "rssi_dbm": channel.get("rssi_dbm"),
            "snr_db": channel.get("snr_db"),
            "noise_floor_dbm": channel.get("noise_floor_dbm"),
            "spectrum": channel.get("spectrum") or [],
            "squelch_open": bool(channel.get("squelch_open")),
            "last_seen": last_seen,
            "age_seconds": None if age is None else round(age, 2),
            "last_text": channel.get("last_text", ""),
            "sample_count": channel.get("sample_count", 0),
            "telemetry_source": channel.get("telemetry_source", "none"),
        }

    def _mock_spectrum(self, channel, strength, now):
        bins = []
        center = 0.48 + math.sin(now / 6 + channel["frequency_mhz"]) * 0.08
        width = 0.055 + strength * 0.035
        for index in range(96):
            ratio = index / 95
            carrier = math.exp(-((ratio - center) ** 2) / (2 * width * width))
            lobe = math.exp(-((ratio - center - 0.16) ** 2) / 0.004) * 0.26
            lobe += math.exp(-((ratio - center + 0.15) ** 2) / 0.004) * 0.24
            ripple = math.sin(now * 4.2 + index * 0.91) * 0.045
            floor = 0.12 + math.sin(index * 1.7 + now) * 0.025
            bins.append(round(max(0.03, min(1.0, floor + carrier * strength + lobe + ripple)), 3))
        return bins

    def _clean_spectrum(self, values):
        if not isinstance(values, list):
            return []
        cleaned = []
        for value in values[:160]:
            cleaned.append(round(max(0.0, min(1.0, self._float(value, 0.0))), 3))
        return cleaned

    def _float(self, value, default):
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

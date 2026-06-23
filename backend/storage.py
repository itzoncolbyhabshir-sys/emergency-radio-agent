"""SQLite persistence for events and merged incidents."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from threading import Lock


class EventStore:
    """Small JSON-on-SQLite store for replay and restart recovery."""

    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def _init_schema(self):
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    incident_id TEXT,
                    payload TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    incident_id TEXT PRIMARY KEY,
                    last_seen TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)"
            )

    def save_event(self, event):
        payload = json.dumps(event, ensure_ascii=False)
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO events(id, timestamp, incident_id, payload)
                VALUES (?, ?, ?, ?)
                """,
                (
                    str(event.get("id")),
                    event.get("timestamp", ""),
                    event.get("incident_id"),
                    payload,
                ),
            )

    def save_incident(self, incident):
        payload = json.dumps(incident, ensure_ascii=False)
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO incidents(incident_id, last_seen, payload)
                VALUES (?, ?, ?)
                """,
                (
                    incident.get("incident_id"),
                    incident.get("last_seen", ""),
                    payload,
                ),
            )

    def list_events(self, limit=100, offset=0):
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT payload FROM events
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def load_incidents(self):
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM incidents ORDER BY last_seen DESC"
            ).fetchall()
        now = time.time()
        incidents = []
        for row in rows:
            incident = json.loads(row[0])
            incident["updated_at_monotonic"] = now
            incident.setdefault("history", [])
            incident.setdefault("resources", [])
            incident.setdefault("actions", [])
            incidents.append(incident)
        return incidents

    def close(self):
        with self._lock:
            self._conn.close()

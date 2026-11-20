"""SQLite evidence. No prompts, leads, tool arguments, or tool results belong here."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from contracts.schemas import RunSummary


class EvidenceStore:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self.durable = self.path != ":memory:"
        if self.durable:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self.connection = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=10000")
        self.connection.execute("PRAGMA foreign_keys=ON")
        if self.durable:
            self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, summary_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS actions (
                event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
                fingerprint TEXT NOT NULL, verdict_json TEXT NOT NULL, execution_state TEXT
            );
            CREATE TABLE IF NOT EXISTS violations (
                event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS timeline (
                event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
                step INTEGER NOT NULL, tool_name TEXT NOT NULL, decision TEXT NOT NULL,
                execution_status TEXT NOT NULL, rule_id TEXT NOT NULL, timestamp TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS timeline_run ON timeline(run_id, step);
            CREATE TABLE IF NOT EXISTS model_usage (
                call_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
                payload_json TEXT NOT NULL, timestamp TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS capability_evidence (
                evidence_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
                payload_json TEXT NOT NULL, timestamp TEXT NOT NULL
            );
        """)

    @contextmanager
    def transaction(self):
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise

    @staticmethod
    def get_summary(conn, run_id) -> RunSummary | None:
        row = conn.execute(
            "SELECT summary_json FROM runs WHERE run_id=?", (str(run_id),)
        ).fetchone()
        return RunSummary.model_validate_json(row[0]) if row else None

    @staticmethod
    def save_summary(conn, summary: RunSummary):
        conn.execute(
            "INSERT INTO runs VALUES (?, ?, ?) ON CONFLICT(run_id) DO UPDATE SET summary_json=excluded.summary_json",
            (
                str(summary.run_id),
                summary.started_at.isoformat(),
                summary.model_dump_json(),
            ),
        )

    def list_runs(self, limit: int = 50) -> list[RunSummary]:
        with self.transaction() as conn:
            return [
                RunSummary.model_validate_json(row[0])
                for row in conn.execute(
                    "SELECT summary_json FROM runs ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                )
            ]

    def timeline(self, run_id) -> list[dict]:
        with self.transaction() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM timeline WHERE run_id=? ORDER BY step, timestamp",
                    (str(run_id),),
                )
            ]

    def usage(self, run_id) -> list[dict]:
        with self.transaction() as conn:
            return [
                json.loads(row[0])
                for row in conn.execute(
                    "SELECT payload_json FROM model_usage WHERE run_id=? ORDER BY timestamp",
                    (str(run_id),),
                )
            ]

    def close(self):
        with self._lock:
            self.connection.close()

    def capabilities(self, run_id) -> list[dict]:
        with self.transaction() as conn:
            return [
                json.loads(row[0])
                for row in conn.execute(
                    "SELECT payload_json FROM capability_evidence WHERE run_id=? ORDER BY timestamp",
                    (str(run_id),),
                )
            ]

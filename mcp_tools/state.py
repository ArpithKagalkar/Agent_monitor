"""Synthetic execution payloads, deliberately isolated from redacted evidence."""

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from contracts.schemas import utc_now


class ToolState:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        evidence_path = Path(os.getenv("DB_PATH", "store/agent_monitor.db")).resolve()
        if self.path == evidence_path:
            raise ValueError("TOOL_STATE_PATH and DB_PATH must be different files")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS tool_actions (
                execution_id TEXT PRIMARY KEY, event_id TEXT UNIQUE NOT NULL,
                run_id TEXT, tool_name TEXT NOT NULL, fingerprint TEXT NOT NULL,
                payload_json TEXT NOT NULL, timestamp TEXT NOT NULL
            )""")

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def append(
        self, tool_name: str, payload: dict, *, event_id=None, run_id=None
    ) -> dict:
        event_id = str(event_id or uuid4())
        run_id = str(run_id) if run_id else None
        serialized = json.dumps(payload, sort_keys=True)
        fingerprint = hashlib.sha256(
            f"{tool_name}:{run_id}:{serialized}".encode()
        ).hexdigest()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                "SELECT execution_id, fingerprint FROM tool_actions WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ValueError(
                        "Execution ID was reused for a different synthetic action"
                    )
                return {"execution_id": previous["execution_id"], "synthetic": True}
            execution_id = str(uuid4())
            conn.execute(
                "INSERT INTO tool_actions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    execution_id,
                    event_id,
                    run_id,
                    tool_name,
                    fingerprint,
                    serialized,
                    utc_now().isoformat(),
                ),
            )
            conn.commit()
            return {"execution_id": execution_id, "synthetic": True}
        finally:
            conn.close()

    @staticmethod
    def list_writes(path: str | Path, run_id) -> list[dict]:
        path = Path(path).resolve()
        if not path.exists():
            return []
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM tool_actions WHERE run_id=? ORDER BY timestamp",
                (str(run_id),),
            ).fetchall()
            results = []
            for row in rows:
                payload = json.loads(row["payload_json"])
                results.append(
                    {
                        "execution_id": row["execution_id"],
                        "event_id": row["event_id"],
                        "tool_name": row["tool_name"],
                        "timestamp": row["timestamp"],
                        "destination": payload.get("destination"),
                        "fields": list(payload.get("data", {})),
                    }
                )
            return results
        finally:
            conn.close()

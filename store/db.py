"""SQLite evidence store for runs, findings, the redacted timeline and Otari usage.

Privacy rule (spec section 10): never persist raw lead values, prompts or tool
arguments. Callers pass redacted summaries (field *names*, destinations, ids);
this module also strips anything that is not a plain scalar or list of scalars.

`violations.event_id` is unique, so recording the same gateway decision twice
is idempotent.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL CHECK (mode IN ('protected', 'unprotected_test')),
    kind TEXT NOT NULL DEFAULT 'agent',          -- 'agent' or 'enforcement_test'
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',      -- running | completed | failed
    attempted_count INTEGER NOT NULL DEFAULT 0,  -- mutating tool calls the agent tried
    checked_count INTEGER NOT NULL DEFAULT 0,
    allow_count INTEGER NOT NULL DEFAULT 0,
    flag_count INTEGER NOT NULL DEFAULT 0,
    block_count INTEGER NOT NULL DEFAULT 0,
    approval_count INTEGER NOT NULL DEFAULT 0,
    executed_count INTEGER NOT NULL DEFAULT 0,   -- verified from the tool server's own log
    final_answer TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS violations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL,
    step INTEGER,
    tool_name TEXT,
    decision TEXT,
    rule_id TEXT,
    severity TEXT,
    reason TEXT,
    regulatory_context_json TEXT,
    redacted_summary TEXT,
    timestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    kind TEXT NOT NULL,              -- llm | tool | info
    tool_name TEXT,
    outcome TEXT,                    -- executed | blocked | pending_approval | error
    decision TEXT,
    rule_id TEXT,
    summary_json TEXT,
    timestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    otari_request_id TEXT,
    model TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    cost_usd TEXT,                   -- exactly as Otari reported it; NULL if not priced
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_timeline_run ON timeline(run_id, id);
CREATE INDEX IF NOT EXISTS ix_violations_run ON violations(run_id);
"""

_COUNTERS = {"attempted_count", "checked_count", "allow_count", "flag_count",
             "block_count", "approval_count", "executed_count"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _scrub(value: Any) -> Any:
    """Keep only scalars and flat lists of scalars; truncate long strings."""
    if isinstance(value, str):
        return value[:120]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value if isinstance(v, (str, int, float, bool))]
    return None


def _scrub_dict(d: dict[str, Any] | None) -> dict[str, Any]:
    return {str(k): _scrub(v) for k, v in (d or {}).items()}


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- runs -------------------------------------------------------------

    def start_run(self, mode: str, kind: str = "agent", run_id: str | None = None) -> str:
        run_id = run_id or str(uuid.uuid4())
        with self._conn() as c:
            c.execute("INSERT INTO runs (run_id, mode, kind, started_at) VALUES (?, ?, ?, ?)",
                      (run_id, mode, kind, now()))
        return run_id

    def bump(self, run_id: str, **deltas: int) -> None:
        bad = set(deltas) - _COUNTERS
        if bad:
            raise ValueError(f"unknown counters {bad}")
        if not deltas:
            return
        sets = ", ".join(f"{k} = {k} + ?" for k in deltas)
        with self._conn() as c:
            c.execute(f"UPDATE runs SET {sets} WHERE run_id = ?", (*deltas.values(), run_id))

    def end_run(self, run_id: str, status: str, executed_count: int | None = None,
                final_answer: str | None = None, error: str | None = None) -> None:
        with self._conn() as c:
            c.execute("""UPDATE runs SET ended_at = ?, status = ?, final_answer = ?, error = ?,
                         executed_count = COALESCE(?, executed_count) WHERE run_id = ?""",
                      (now(), status, (final_answer or "")[:2000] or None, (error or "")[:500] or None,
                       executed_count, run_id))

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ---- evidence ---------------------------------------------------------

    def add_timeline(self, run_id: str, step: int, kind: str, *, tool_name: str | None = None,
                     outcome: str | None = None, decision: str | None = None, rule_id: str | None = None,
                     summary: dict[str, Any] | None = None) -> None:
        with self._conn() as c:
            c.execute("""INSERT INTO timeline (run_id, step, kind, tool_name, outcome, decision, rule_id,
                         summary_json, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                      (run_id, step, kind, tool_name, outcome, decision, rule_id,
                       json.dumps(_scrub_dict(summary)), now()))

    def add_violation(self, run_id: str, step: int, *, tool_name: str, decision: str, rule_id: str | None,
                      severity: str | None, reason: str | None, regulatory_context: list[str] | None,
                      redacted_summary: str, event_id: str | None = None) -> bool:
        """Returns False if this event_id was already recorded."""
        with self._conn() as c:
            cur = c.execute("""INSERT OR IGNORE INTO violations (event_id, run_id, step, tool_name, decision,
                               rule_id, severity, reason, regulatory_context_json, redacted_summary, timestamp)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (event_id or str(uuid.uuid4()), run_id, step, tool_name, decision, rule_id, severity,
                             (reason or "")[:300], json.dumps(list(regulatory_context or [])),
                             redacted_summary[:300], now()))
            return cur.rowcount == 1

    def add_llm_call(self, run_id: str, step: int, *, otari_request_id: str | None, model: str | None,
                     prompt_tokens: int | None, completion_tokens: int | None, cost_usd: str | None) -> None:
        with self._conn() as c:
            c.execute("""INSERT INTO llm_calls (run_id, step, otari_request_id, model, prompt_tokens,
                         completion_tokens, cost_usd, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                      (run_id, step, otari_request_id, model, prompt_tokens, completion_tokens,
                       None if cost_usd is None else str(cost_usd), now()))

    def timeline(self, run_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM timeline WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["summary"] = json.loads(d.pop("summary_json") or "{}")
            out.append(d)
        return out

    def violations(self, run_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM violations WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["regulatory_context"] = json.loads(d.pop("regulatory_context_json") or "[]")
            out.append(d)
        return out

    def llm_calls(self, run_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM llm_calls WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        return [dict(r) for r in rows]

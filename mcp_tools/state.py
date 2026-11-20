"""Append-only record of what the synthetic tools actually executed.

The tool server only ever sees calls that reached it, so every line here is an
*executed* action. Attempts that the gateway blocked never show up in this file,
which is exactly what the demo needs to prove.

Only lead IDs and field names are stored, never field values.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DEFAULT_DIR = Path(__file__).resolve().parent / "_runtime"
_lock = threading.Lock()


def state_file() -> Path:
    path = Path(os.environ.get("MCP_STATE_FILE", _DEFAULT_DIR / "actions.jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def record(tool: str, details: dict[str, Any], run_id: str | None = None) -> dict[str, Any]:
    entry = {
        "execution_id": f"X-{uuid.uuid4().hex[:12]}",
        "tool": tool,
        "run_id": run_id,
        "details": details,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }
    with _lock, state_file().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    return entry


def read_all(tool: str | None = None, run_id: str | None = None) -> list[dict[str, Any]]:
    path = state_file()
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if tool and row["tool"] != tool:
                continue
            if run_id and row.get("run_id") != run_id:
                continue
            rows.append(row)
    return rows


def count(tool: str | None = None, run_id: str | None = None) -> int:
    return len(read_all(tool, run_id))


def reset() -> None:
    """Start a clean demo. Archives the old log instead of deleting it."""
    path = state_file()
    with _lock:
        if path.exists() and path.stat().st_size:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            path.rename(path.with_name(f"actions-{stamp}.jsonl"))
        path.touch()

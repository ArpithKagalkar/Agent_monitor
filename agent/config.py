"""Environment-driven settings for the demo agent. No secrets or model names are hard-coded."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MODES = ("protected", "unprotected_test")
RUN_ID_HEADER = "X-Agent-Monitor-Run-Id"


def _load_dotenv() -> None:
    """Minimal .env loader so the demo runs without extra dependencies. Real env vars win."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


@dataclass(frozen=True)
class Settings:
    otari_base_url: str
    otari_key: str
    otari_model: str
    upstream_url: str
    gateway_url: str
    engine_url: str
    db_path: Path

    def mcp_url(self, mode: str) -> str:
        """Protected runs go through the gateway only. The baseline goes straight to the tools."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        base = self.gateway_url if mode == "protected" else self.upstream_url
        return base.rstrip("/") + "/mcp"

    def require_otari(self) -> None:
        missing = [name for name, val in (("OTARI_BASE_URL", self.otari_base_url),
                                          ("OTARI_AGENT_KEY", self.otari_key),
                                          ("OTARI_AGENT_MODEL", self.otari_model)) if not val]
        if missing:
            raise RuntimeError(f"Missing Otari settings: {', '.join(missing)}. Set them in .env (see .env.example).")


def load() -> Settings:
    db = Path(os.environ.get("DB_PATH", "store/agent_monitor.db"))
    return Settings(
        otari_base_url=os.environ.get("OTARI_BASE_URL", ""),
        otari_key=os.environ.get("OTARI_AGENT_KEY", ""),
        otari_model=os.environ.get("OTARI_AGENT_MODEL", ""),
        upstream_url=os.environ.get("MCP_UPSTREAM_URL", "http://localhost:8100"),
        gateway_url=os.environ.get("MCP_GATEWAY_URL", "http://localhost:8200"),
        engine_url=os.environ.get("ENGINE_URL", "http://localhost:8000"),
        db_path=db if db.is_absolute() else ROOT / db,
    )

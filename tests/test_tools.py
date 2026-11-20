"""Tests for the synthetic MCP tool server (Dev 2).

Run from the repo root:  python -m pytest tests/test_tools.py -q
"""

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_STATE_FILE", str(tmp_path / "actions.jsonl"))
    yield tmp_path / "actions.jsonl"


def test_fixtures_have_special_category_leads():
    from mcp_tools.server import _leads

    leads = _leads()
    assert len(leads) == 10
    assert "health_note" in leads["L-003"]
    assert "religion_note" in leads["L-007"]


def test_get_lead_labels_fields():
    from mcp_tools.server import get_lead_impl

    out = get_lead_impl("L-003")
    assert out["field_classification"]["health_note"] == "special_category"
    assert out["field_classification"]["business_email"] == "personal_data"


def test_search_filters():
    from mcp_tools.server import search_leads_impl

    assert len(search_leads_impl("")) == 10
    hits = search_leads_impl("observability")
    assert {h["lead_id"] for h in hits} == {"L-001", "L-005", "L-008"}


def test_send_to_crm_records_one_execution_without_values():
    from mcp_tools import state
    from mcp_tools.server import send_to_crm_impl

    out = send_to_crm_impl("L-003", ["name", "business_email", "health_note"], "unapproved-crm", run_id="r1")
    assert out["status"] == "exported"
    rows = state.read_all("send_to_crm", "r1")
    assert len(rows) == 1
    # field names are logged, the sensitive value itself is not
    assert "surgery" not in str(rows)


def test_unknown_lead_writes_nothing():
    from mcp_tools import state
    from mcp_tools.server import send_to_crm_impl

    with pytest.raises(ValueError):
        send_to_crm_impl("L-999", ["name"], "approved-crm")
    assert state.count() == 0


def test_reset_archives_log(isolated_state):
    from mcp_tools import state
    from mcp_tools.server import send_email_impl

    send_email_impl("a@b.example", "hi", "body")
    state.reset()
    assert state.count() == 0
    assert list(isolated_state.parent.glob("actions-*.jsonl"))


# ---- live smoke test: real server, real MCP client -----------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(isolated_state):
    port = _free_port()
    env = {**os.environ, "MCP_TOOLS_PORT": str(port), "MCP_STATE_FILE": str(isolated_state)}
    proc = subprocess.Popen([sys.executable, "-m", "mcp_tools.server"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            if httpx.get(f"{base}/health", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("tool server did not start")
    yield base
    proc.terminate()
    proc.wait(timeout=5)


def test_mcp_list_and_call(live_server):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def run():
        async with streamablehttp_client(f"{live_server}/mcp") as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {t.name for t in (await session.list_tools()).tools}
                assert tools == {"search_leads", "get_lead", "send_to_crm", "decide_on_lead", "send_email"}
                res = await session.call_tool("send_to_crm", {"lead_id": "L-001", "fields": ["name", "business_email"],
                                                              "destination": "approved-crm", "run_id": "smoke"})
                assert not res.isError

    asyncio.run(run())
    st = httpx.get(f"{live_server}/state", params={"run_id": "smoke"}).json()
    assert st["executed_count"] == 1
    assert st["by_tool"] == {"send_to_crm": 1}

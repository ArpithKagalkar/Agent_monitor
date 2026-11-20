"""Tests for the demo agent, result classification and evidence store (Dev 2).

No Otari calls here: a scripted fake model drives the real LangGraph loop, the
real MCP tool server and an in-test *stub* gateway. The stub only stands in for
Dev 1's gateway until it lands; it is not the enforcement implementation.

Run from the repo root:  python -m pytest tests/test_agent.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from langchain_core.messages import AIMessage, ToolMessage
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from agent import config
from agent.run import COMPLIANT_CALL, VIOLATING_CALL, run_agent, run_enforcement_test
from agent.tools import BLOCKED, EXECUTED, PENDING_APPROVAL, TOOL_ERROR, classify
from store.db import Store

ROOT = Path(__file__).resolve().parent.parent

BLOCK_VERDICT = {
    "event_id": "ev-1", "decision": "block", "severity": "high", "rule_id": "DESTINATION_NOT_APPROVED",
    "policy_reference": "company_policy:crm_recipient_allowlist",
    "regulatory_context": ["GDPR Art. 5(1)(b)", "GDPR Art. 5(1)(c)"],
    "reason": "Recipient is not approved for lead export",
    "safe_alternative": {"tool_name": "send_to_crm", "destination": "approved-crm",
                         "fields": ["name", "business_email"]},
    "executed": False,
}


# ---- classification -------------------------------------------------------

def _text(s: str, is_error: bool = False, structured=None) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=s)], isError=is_error,
                          structuredContent=structured)


def test_classify_block_inside_error_text():
    o = classify("send_to_crm", _text("Error executing tool send_to_crm: " + json.dumps(BLOCK_VERDICT), True))
    assert o.outcome == BLOCKED
    assert o.rule_id == "DESTINATION_NOT_APPROVED"
    assert o.safe_alternative["destination"] == "approved-crm"


def test_classify_structured_require_approval_is_not_executed():
    v = {**BLOCK_VERDICT, "decision": "require_approval", "rule_id": "CONSEQUENTIAL_ACTION"}
    o = classify("decide_on_lead", _text(json.dumps(v), False, structured=v))
    assert o.outcome == PENDING_APPROVAL


def test_classify_executed_and_plain_error():
    ok = {"status": "exported", "execution_id": "X-abc"}
    o = classify("send_to_crm", _text(json.dumps(ok), structured=ok))
    assert (o.outcome, o.execution_id) == (EXECUTED, "X-abc")
    assert classify("get_lead", _text("Unknown lead_id 'L-999'", True)).outcome == TOOL_ERROR


def test_mode_routing():
    s = config.load()
    s = replace(s, upstream_url="http://u:8100", gateway_url="http://g:8200")
    assert s.mcp_url("protected") == "http://g:8200/mcp"
    assert s.mcp_url("unprotected_test") == "http://u:8100/mcp"
    with pytest.raises(ValueError):
        s.mcp_url("anything")


# ---- store ----------------------------------------------------------------

def test_store_is_idempotent_and_scrubs(tmp_path):
    st = Store(tmp_path / "e.db")
    rid = st.start_run("protected")
    kw = dict(tool_name="send_to_crm", decision="block", rule_id="R", severity="high", reason="x",
              regulatory_context=[], redacted_summary="send_to_crm fields=['health_note']", event_id="ev-1")
    assert st.add_violation(rid, 1, **kw) is True
    assert st.add_violation(rid, 2, **kw) is False
    st.add_timeline(rid, 3, "tool", summary={"fields": ["health_note"], "nested": {"value": "surgery"}})
    assert st.timeline(rid)[0]["summary"] == {"fields": ["health_note"], "nested": None}
    assert len(st.violations(rid)) == 1


# ---- live servers ---------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url: str) -> None:
    for _ in range(60):
        try:
            if httpx.get(url, timeout=0.5).status_code < 500:
                return
        except httpx.HTTPError:
            time.sleep(0.2)
    pytest.fail(f"{url} did not come up")


@pytest.fixture
def tool_server(tmp_path):
    port = _free_port()
    env = {**os.environ, "MCP_TOOLS_PORT": str(port), "MCP_STATE_FILE": str(tmp_path / "actions.jsonl")}
    proc = subprocess.Popen([sys.executable, "-m", "mcp_tools.server"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        _wait(f"{base}/health")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.fixture
def stub_gateway(tool_server):
    """Minimal stand-in for Dev 1's gateway: blocks non-approved CRM exports, forwards the rest."""
    port = _free_port()
    gw = FastMCP("stub-gateway", stateless_http=True, json_response=True)
    forwarded: list[dict] = []

    async def forward(name: str, args: dict, ctx: Context):
        run_id = ctx.request_context.request.headers.get(config.RUN_ID_HEADER, "")
        async with httpx.AsyncClient(headers={config.RUN_ID_HEADER: run_id}) as http, \
                streamable_http_client(f"{tool_server}/mcp", http_client=http) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                forwarded.append({"tool": name, **args})
                res = await s.call_tool(name, args)
                return res.structuredContent.get("result", res.structuredContent)

    @gw.tool()
    async def search_leads(ctx: Context, query: str = "") -> list:
        return await forward("search_leads", {"query": query}, ctx)

    @gw.tool()
    async def send_to_crm(lead_id: str, fields: list[str], destination: str, ctx: Context) -> dict:
        if destination != "approved-crm" or set(fields) - {"name", "business_email"}:
            raise ToolError(json.dumps(BLOCK_VERDICT))
        return await forward("send_to_crm", {"lead_id": lead_id, "fields": fields, "destination": destination}, ctx)

    server = uvicorn.Server(uvicorn.Config(gw.streamable_http_app(), host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    _wait(f"{base}/mcp")
    yield base, forwarded
    server.should_exit = True
    t.join(timeout=5)


class ScriptedLLM:
    """Stands in for the Otari-backed ChatOpenAI. Each step is a function of the conversation so far."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.tool_names: list[str] = []

    def bind_tools(self, tools):
        self.tool_names = [t["function"]["name"] for t in tools]
        return self

    async def ainvoke(self, messages):
        return self.steps.pop(0)(messages)


def _call(name, args, cid):
    return lambda _m: AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": cid}])


def _settings(tmp_path, upstream, gateway="http://127.0.0.1:1"):
    return replace(config.load(), upstream_url=upstream, gateway_url=gateway, db_path=tmp_path / "e.db")


def test_unprotected_baseline_executes_violating_export(tmp_path, tool_server):
    s = _settings(tmp_path, tool_server)
    llm = ScriptedLLM([_call("send_to_crm", VIOLATING_CALL, "c1"), lambda m: AIMessage(content="done")])
    run = asyncio.run(run_agent("task", "unprotected_test", settings=s, llm=llm))
    assert run["status"] == "completed", run
    assert run["mode"] == "unprotected_test"
    assert run["executed_count"] == 1  # verified via the tool server log, attributed through the header
    assert run["block_count"] == 0
    assert "send_to_crm" in llm.tool_names


def test_protected_blocks_then_safe_retry(tmp_path, tool_server, stub_gateway):
    gw_url, forwarded = stub_gateway
    s = _settings(tmp_path, tool_server, gw_url)

    def retry(messages):
        last = messages[-1]
        assert isinstance(last, ToolMessage) and "DENIED" in last.content and "approved-crm" in last.content
        return AIMessage(content="", tool_calls=[{"name": "send_to_crm", "args": COMPLIANT_CALL, "id": "c2"}])

    llm = ScriptedLLM([_call("send_to_crm", VIOLATING_CALL, "c1"), retry,
                       lambda m: AIMessage(content="exported minimal profile")])
    run = asyncio.run(run_agent("task", "protected", settings=s, llm=llm))
    assert run["status"] == "completed", run
    assert (run["attempted_count"], run["block_count"], run["executed_count"]) == (2, 1, 1)
    assert [f["destination"] for f in forwarded] == ["approved-crm"]  # blocked call never forwarded

    st = Store(s.db_path)
    outcomes = [e["outcome"] for e in st.timeline(run["run_id"]) if e["kind"] == "tool"]
    assert outcomes == [BLOCKED, EXECUTED]
    assert st.violations(run["run_id"])[0]["rule_id"] == "DESTINATION_NOT_APPROVED"
    dump = "\n".join(str(r) for r in sqlite3.connect(s.db_path).iterdump())
    assert "surgery" not in dump and "+49" not in dump  # no raw lead values in evidence


def test_enforcement_test_zero_then_one_execution(tmp_path, tool_server, stub_gateway):
    gw_url, _ = stub_gateway
    run = asyncio.run(run_enforcement_test("protected", settings=_settings(tmp_path, tool_server, gw_url)))
    assert run["kind"] == "enforcement_test"
    assert (run["block_count"], run["executed_count"]) == (1, 1)


def test_gateway_down_fails_closed(tmp_path, tool_server):
    s = _settings(tmp_path, tool_server, gateway=f"http://127.0.0.1:{_free_port()}")
    llm = ScriptedLLM([_call("send_to_crm", COMPLIANT_CALL, "c1")])
    run = asyncio.run(run_agent("task", "protected", settings=s, llm=llm))
    assert run["status"] == "failed"
    assert run["executed_count"] == 0

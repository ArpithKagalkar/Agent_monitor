"""Gateway unit tests and a real Streamable HTTP proof against a test-only stub.

The stub belongs to Developer One's tests; it does not implement or modify
Developer Two's mcp_tools server. No model credentials are needed.
"""

import asyncio
import json
import socket
import threading
import time
from contextlib import ExitStack, contextmanager
from uuid import uuid4

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer

from contracts.schemas import Event, ExecutionReport
from engine.main import create_app
from engine.policy import PolicySettings
from engine.runs import RunManager
from gateway.server import GuardedGateway, MCPUpstream, PolicyHTTP, create_server


@pytest.fixture
def gateway_case():
    manager = RunManager(PolicySettings())
    run = manager.start("protected")
    calls = []
    order = []

    class Policy:
        async def check(self, event):
            order.append("check")
            return manager.check(event)

        async def report(self, event, state):
            order.append(state)
            return manager.report(event.run_id, ExecutionReport(event_id=event.event_id, state=state))

    class Upstream:
        async def call(self, name, arguments, *, meta=None):
            order.append("upstream")
            calls.append((name, arguments))
            return {"content": [{"type": "text", "text": "synthetic write confirmed"}], "is_error": False}

    event = Event(
        event_id=uuid4(), run_id=run.run_id, step=1, source="mcp_gateway",
        tool_name="send_to_crm", arguments={"lead_id": "L-001", "fields": ["name"], "destination": "approved-crm"},
        context={"purpose": "qualified_lead_outreach"},
    )
    return GuardedGateway(Policy(), Upstream()), event, calls, order, manager


def test_allowed_call_is_checked_claimed_executed_then_counted(gateway_case):
    gateway, event, calls, order, manager = gateway_case
    result = asyncio.run(gateway.invoke(event))
    assert result["executed"] is True
    assert order == ["check", "started", "upstream", "succeeded"]
    assert len(calls) == 1
    assert manager.summary(event.run_id).executed_count == 1
    assert asyncio.run(gateway.invoke(event))["executed"] is False
    assert len(calls) == 1


def test_concurrent_duplicate_calls_do_not_execute_twice(gateway_case):
    gateway, event, calls, order, manager = gateway_case
    original_call = gateway.upstream.call

    async def slow_call(name, arguments, *, meta=None):
        await asyncio.sleep(0)
        return await original_call(name, arguments, meta=meta)

    gateway.upstream.call = slow_call

    async def race():
        return await asyncio.gather(gateway.invoke(event), gateway.invoke(event))

    results = asyncio.run(race())
    assert sorted(result["executed"] for result in results) == [False, True]
    assert len(calls) == 1
    assert manager.summary(event.run_id).executed_count == 1


@pytest.mark.parametrize("case", ["destination", "health", "religion", "approval"])
def test_denied_calls_make_zero_downstream_writes(gateway_case, case):
    gateway, event, calls, order, manager = gateway_case
    if case == "destination":
        event.arguments["destination"] = "unapproved-crm"
    elif case in {"health", "religion"}:
        event.arguments["fields"] = [f"{case}_note"]
    else:
        event.tool_name = "decide_on_lead"
        event.arguments = {"lead_id": "L-001", "decision": "qualify", "reason": "Synthetic"}
        event.context.human_approved = True
    result = asyncio.run(gateway.invoke(event))
    assert result["executed"] is False
    assert result["verdict"]["decision"] in {"block", "require_approval"}
    assert calls == []
    assert order == ["check"]
    assert manager.summary(event.run_id).executed_count == 0


@pytest.mark.parametrize("failure", ["offline", "mismatch", "claim"])
def test_policy_or_claim_failure_fails_closed(gateway_case, failure):
    gateway, event, calls, order, manager = gateway_case
    if failure == "offline":
        async def check(event):
            raise httpx.ConnectError("PRIVATE_ERROR")
        gateway.policy.check = check
    elif failure == "mismatch":
        async def check(event):
            return manager.check(event).model_copy(update={"event_id": uuid4()})
        gateway.policy.check = check
    else:
        async def report(event, state):
            raise httpx.ConnectError("PRIVATE_ERROR")
        gateway.policy.report = report
    result = asyncio.run(gateway.invoke(event))
    assert result["executed"] is False
    assert calls == []
    assert "PRIVATE_ERROR" not in json.dumps(result)


def test_flag_is_forwarded_and_recorded(gateway_case):
    gateway, event, calls, order, manager = gateway_case
    async def check(event):
        verdict = manager.check(event)
        verdict.decision = "flag"
        return verdict
    gateway.policy.check = check
    assert asyncio.run(gateway.invoke(event))["executed"] is True
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["timeout", "tool_error", "malformed"])
def test_uncertain_downstream_outcomes_are_not_retried_or_claimed_as_zero(gateway_case, failure):
    gateway, event, calls, order, manager = gateway_case
    async def call(name, arguments, *, meta=None):
        calls.append((name, arguments))
        if failure == "timeout":
            raise TimeoutError("Private downstream detail")
        if failure == "malformed":
            return {}
        return {"content": [], "is_error": True}
    gateway.upstream.call = call
    result = asyncio.run(gateway.invoke(event))
    assert result["execution_status"] == "unknown"
    assert result["executed"] is None
    assert "Private downstream detail" not in json.dumps(result)
    assert len(calls) == 1
    assert asyncio.run(gateway.invoke(event))["executed"] is False
    assert len(calls) == 1
    assert manager.summary(event.run_id).executed_count == 0


def test_lost_receipt_never_retries_a_successful_business_action(gateway_case):
    gateway, event, calls, order, manager = gateway_case
    original = gateway.policy.report
    async def report(event, state):
        if state == "succeeded":
            raise httpx.ConnectError("Receipt connection lost")
        return await original(event, state)
    gateway.policy.report = report
    result = asyncio.run(gateway.invoke(event))
    assert result["executed"] is True
    assert result["accounting_confirmed"] is False
    assert asyncio.run(gateway.invoke(event))["executed"] is False
    assert len(calls) == 1


@contextmanager
def running_server(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", log_level="critical", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("Local test server did not start")
            time.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()


def test_real_mcp_discovery_block_safe_retry_and_execution_count():
    writes = []
    stub = MCPServer("Developer One Test Stub", log_level="WARNING")

    @stub.tool()
    async def send_to_crm(lead_id: str, fields: list[str], destination: str) -> dict:
        writes.append({"lead_id": lead_id, "fields": fields, "destination": destination})
        return {"execution_id": f"synthetic-{len(writes)}"}

    with ExitStack() as stack:
        engine_url = stack.enter_context(running_server(create_app(PolicySettings())))
        upstream_url = stack.enter_context(running_server(stub.streamable_http_app(json_response=True))) + "/mcp"
        service = GuardedGateway(PolicyHTTP(engine_url), MCPUpstream(upstream_url))
        gateway_url = stack.enter_context(running_server(create_server(service).streamable_http_app(json_response=True))) + "/mcp"
        with httpx.Client(timeout=5) as http:
            run_id = http.post(f"{engine_url}/runs/start", json={"mode": "protected"}).json()["run_id"]

            async def smoke():
                async with streamable_http_client(upstream_url) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=5) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        assert tools.tools[0].name == "send_to_crm"
                async with streamable_http_client(gateway_url) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=5) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        assert {tool.name for tool in tools.tools} == {"search_leads", "get_lead", "send_to_crm", "decide_on_lead", "send_email"}
                        meta = {"agent_monitor": {"run_id": run_id, "event_id": str(uuid4()), "step": 1}}
                        unsafe = {"lead_id": "L-001", "fields": ["name", "health_note"], "destination": "unapproved-crm"}
                        blocked = await session.call_tool("send_to_crm", unsafe, meta=meta)
                        blocked_data = blocked.structured_content
                        assert blocked_data["verdict"]["rule_id"] == "DESTINATION_NOT_APPROVED"
                        assert blocked_data["executed"] is False
                        assert writes == []
                        safe = {**unsafe, "fields": ["name", "business_email"], "destination": "approved-crm"}
                        meta["agent_monitor"].update(event_id=str(uuid4()), step=2)
                        allowed = await session.call_tool("send_to_crm", safe, meta=meta)
                        allowed_data = allowed.structured_content
                        assert allowed_data["executed"] is True
                        assert allowed_data["accounting_confirmed"] is True
                        assert len(writes) == 1
                        # Same action ID cannot produce a second write.
                        duplicate = await session.call_tool("send_to_crm", safe, meta=meta)
                        assert duplicate.structured_content["executed"] is False
                        assert len(writes) == 1
                        no_meta = await session.call_tool("send_to_crm", safe)
                        assert no_meta.structured_content["executed"] is False
                        assert len(writes) == 1
                        # Actual HTTP connection failure, exercised through MCP.
                        with socket.socket() as offline:
                            offline.bind(("127.0.0.1", 0))
                            original_policy = service.policy
                            service.policy = PolicyHTTP(f"http://127.0.0.1:{offline.getsockname()[1]}")
                            try:
                                meta["agent_monitor"].update(event_id=str(uuid4()), step=3)
                                denied = await session.call_tool("send_to_crm", safe, meta=meta)
                                assert denied.structured_content["verdict"]["rule_id"] == "POLICY_UNAVAILABLE"
                                assert denied.structured_content["executed"] is False
                                assert len(writes) == 1
                            finally:
                                service.policy = original_policy
            asyncio.run(smoke())
            summary = http.get(f"{engine_url}/runs/{run_id}").json()
            assert (summary["total_checked"], summary["blocked"], summary["allowed"], summary["executed_count"]) == (2, 1, 1, 1)
            assert http.post(f"{engine_url}/runs/{run_id}/end").status_code == 200

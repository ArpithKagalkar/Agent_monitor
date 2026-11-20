"""Real local services and SQLite. Mocked inference is explicitly test-only."""

import asyncio
import time
from contextlib import ExitStack
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from agent.run import DemoRunner
from contracts.schemas import DemoRequest
from engine.main import create_app
from engine.otari import OtariClient, OtariConfig
from engine.policy import PolicySettings
from gateway.server import (
    GuardedGateway,
    MCPUpstream,
    PolicyHTTP,
    create_server as gateway_server,
)
from mcp_tools.server import SyntheticTools, create_server as tool_server
from mcp_tools.state import ToolState
from tests.test_gateway import running_server


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    directory = tmp_path_factory.mktemp("actual-stack")
    tool_path = directory / "tools.db"
    tools = SyntheticTools(ToolState(tool_path))
    app = create_app(
        PolicySettings(), db_path=directory / "evidence.db", tool_state_path=tool_path
    )
    with ExitStack() as contexts:
        engine = contexts.enter_context(running_server(app))
        upstream = (
            contexts.enter_context(
                running_server(
                    tool_server(tools).streamable_http_app(json_response=True)
                )
            )
            + "/mcp"
        )
        service = GuardedGateway(PolicyHTTP(engine), MCPUpstream(upstream))
        gateway = (
            contexts.enter_context(
                running_server(
                    gateway_server(service).streamable_http_app(json_response=True)
                )
            )
            + "/mcp"
        )
        runner = DemoRunner(engine, gateway, upstream)
        app.state.runner = runner
        yield engine, runner, app.state.runs


@pytest.mark.parametrize("scenario", ["recipient", "sensitive", "approval"])
@pytest.mark.parametrize("mode", ["protected", "unprotected_test"])
def test_all_scenarios_use_real_mcp_and_actual_local_writes(stack, mode, scenario):
    engine, runner, manager = stack
    with httpx.Client(timeout=10) as http:
        response = http.post(
            f"{engine}/demo/run",
            json={"mode": mode, "driver": "enforcement", "scenario": scenario},
        )
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        deadline = time.monotonic() + 15
        while True:
            summary = http.get(f"{engine}/runs/{run_id}").json()
            if summary["status"] != "running":
                break
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert summary["status"] == "completed", summary
        writes = http.get(f"{engine}/runs/{run_id}/tool-writes").json()
        timeline = http.get(f"{engine}/runs/{run_id}/timeline").json()
        if mode == "unprotected_test":
            assert len(writes) == 1
            assert summary["total_checked"] == 0
            assert timeline[0]["decision"] == "unprotected"
            if scenario == "recipient":
                assert writes[0]["destination"] == "unapproved-crm"
        elif scenario == "approval":
            assert writes == []
            assert summary["approval_required"] == 1
        else:
            assert summary["blocked"] == 1
            assert len(writes) == 1
            assert writes[0]["destination"] == "approved-crm"
            assert set(writes[0]["fields"]) == {"name", "business_email"}
        assert summary["executed_count"] == len(writes)
        assert http.get(f"{engine}/runs/{run_id}/usage").json() == []


def test_langgraph_loop_uses_otari_protocol_and_real_guarded_tools(stack):
    engine, runner, manager = stack
    requests = []
    run = manager.start("protected", driver="otari")

    def infer(request):
        requests.append(request)
        assert request.headers["Otari-Conversation-Id"] == str(run.run_id)
        assert str(request.url) == "https://api.otari.ai/api/v1/chat/completions"
        index = len(requests)
        if index < 3:
            fields = (
                '["name","business_email","health_note"]'
                if index == 1
                else '["name","business_email"]'
            )
            destination = "unapproved-crm" if index == 1 else "approved-crm"
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call-{index}",
                        "type": "function",
                        "function": {
                            "name": "send_to_crm",
                            "arguments": '{"lead_id":"L-003","fields":'
                            + fields
                            + ',"destination":"'
                            + destination
                            + '"}',
                        },
                    }
                ],
            }
        else:
            message = {
                "role": "assistant",
                "content": "Synthetic export completed with restricted fields.",
            }
        return httpx.Response(
            200,
            headers={"Otari-Request-ID": f"test-request-{index}"},
            json={
                "id": f"test-completion-{index}",
                "model": "test-workspace-model",
                "choices": [{"message": message}],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                },
            },
        )

    config = OtariConfig(
        "https://api.otari.ai/api/v1",
        SecretStr("test-only-key"),
        "test-workspace-model",
    )
    with OtariClient(config, transport=httpx.MockTransport(infer)) as client:
        assert asyncio.run(
            runner.execute(run.run_id, DemoRequest(driver="otari"), model_client=client)
        )
    summary = manager.summary(run.run_id)
    assert (summary.blocked, summary.executed_count) == (1, 1)
    assert len(requests) == 3
    usage = manager.store.usage(run.run_id)
    assert len(usage) == 3
    assert all(item["cost_usd"] is None for item in usage)
    assert summary.status == "completed"

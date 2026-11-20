import asyncio

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from contracts.schemas import DemoRequest
from engine.main import create_app
from engine.otari import OtariClient, OtariConfig, OtariError
from tests.test_end_to_end import stack


def client_for(handler):
    return OtariClient(
        OtariConfig(
            "https://api.otari.ai/api/v1", SecretStr("offline-test-key"), "test-model"
        ),
        transport=httpx.MockTransport(handler),
    )


def test_agent_stops_at_approval_even_when_model_proposes_more_actions(stack):
    engine, runner, manager = stack
    run = manager.start("protected", driver="otari")
    calls = []

    def infer(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "decision",
                                    "type": "function",
                                    "function": {
                                        "name": "decide_on_lead",
                                        "arguments": '{"lead_id":"L-001","decision":"qualify","reason":"Synthetic"}',
                                    },
                                },
                                {
                                    "id": "export",
                                    "type": "function",
                                    "function": {
                                        "name": "send_to_crm",
                                        "arguments": '{"lead_id":"L-001","fields":["name"],"destination":"approved-crm"}',
                                    },
                                },
                            ],
                        }
                    }
                ]
            },
        )

    with client_for(infer) as client:
        assert asyncio.run(
            runner.execute(run.run_id, DemoRequest(driver="otari"), model_client=client)
        )
    summary = manager.summary(run.run_id)
    assert summary.approval_required == 1
    assert summary.executed_count == 0
    assert len(manager.store.timeline(run.run_id)) == 1
    assert len(calls) == 1


def test_model_turn_limit_stops_a_nonterminating_agent(stack):
    engine, runner, manager = stack
    run = manager.start("protected", driver="otari")
    calls = []

    def infer(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "read",
                                    "type": "function",
                                    "function": {
                                        "name": "get_lead",
                                        "arguments": '{"lead_id":"L-003"}',
                                    },
                                },
                            ],
                        }
                    }
                ]
            },
        )

    with client_for(infer) as client:
        assert not asyncio.run(
            runner.execute(
                run.run_id,
                DemoRequest(driver="otari", max_steps=1),
                model_client=client,
            )
        )
    summary = manager.summary(run.run_id)
    assert summary.status == "failed"
    assert summary.failure_reason == "agent_turn_limit"
    assert summary.executed_count == 1
    assert len(calls) == 1


def test_model_outage_is_failed_without_fake_inference_or_tool_fallback(stack):
    engine, runner, manager = stack
    run = manager.start("protected", driver="otari")
    calls = []

    def infer(request):
        calls.append(request)
        return httpx.Response(503, text="Private provider error detail")

    with client_for(infer) as client:
        assert not asyncio.run(
            runner.execute(run.run_id, DemoRequest(driver="otari"), model_client=client)
        )
    summary = manager.summary(run.run_id)
    assert summary.status == "failed"
    assert summary.failure_reason == "model_request_failed"
    assert summary.executed_count == 0
    assert len(calls) == 1
    assert manager.store.timeline(run.run_id) == []
    assert manager.store.usage(run.run_id) == []


def test_unconfigured_live_run_is_rejected_before_creating_evidence(monkeypatch):
    def unconfigured():
        raise OtariError("Key not configured")

    monkeypatch.setattr(OtariConfig, "from_env", unconfigured)
    with TestClient(create_app()) as client:
        assert client.post("/demo/run", json={"driver": "otari"}).status_code == 503
        assert client.get("/runs").json() == []

import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from contracts.schemas import DemoRequest, ReviewResult
from engine.capabilities import (
    CapabilityWorkflow,
    ProfileSettings,
    lead_card,
    validate_review_facts,
)
from engine.coverage import coverage
from engine.otari import OtariClient, OtariConfig
from tests.test_end_to_end import stack


def test_native_workflow_records_proofs_and_keeps_business_enforcement(
    stack, monkeypatch
):
    engine, runner, manager = stack
    settings = ProfileSettings()
    config = OtariConfig(
        "https://api.otari.ai/api/v1", SecretStr("offline-agent"), settings.actor_model
    )
    monkeypatch.setattr(OtariConfig, "from_env", lambda: config)
    monkeypatch.setattr(
        "engine.capabilities.settings_values",
        lambda: {"OTARI_BUDGET_PROOF_KEY": "offline-budget"},
    )
    actor_calls = []
    native_paths = []
    private_page = "PUBLIC_PAGE_TEXT_MUST_NOT_BE_PERSISTED"
    docs_server = str(uuid4())

    def respond(request):
        native_paths.append(request.url.path)
        body = json.loads(request.content) if request.content else {}
        if request.url.path.endswith("/mcp/servers"):
            return httpx.Response(
                200,
                json={"servers": [{"id": docs_server, "name": "agent-monitor-docs"}]},
            )
        if request.url.path.endswith("/tools"):
            return httpx.Response(
                200,
                json={
                    "server_revision": "offline-revision",
                    "tools": [{"name": "search_cloudflare_documentation"}],
                },
            )
        if request.url.path.endswith("/mcp/execute"):
            assert body["mcp_server_id"] == docs_server
            assert body["server_revision"] == "offline-revision"
            assert body["tool_name"] == "search_cloudflare_documentation"
            return httpx.Response(
                200,
                headers={"Otari-Request-ID": "docs-search-request"},
                json={
                    "isError": False,
                    "content": [
                        {"type": "text", "text": "PUBLIC_DOCS_MUST_NOT_BE_PERSISTED"}
                    ],
                },
            )
        if request.headers["Authorization"] == "Bearer offline-budget":
            return httpx.Response(
                403,
                headers={
                    "Otari-Error-Code": "budget_exceeded",
                    "Otari-Request-ID": "proof-refusal-request",
                },
                json={"code": "budget_exceeded"},
            )
        headers = {"Otari-Request-ID": str(uuid4())}
        if request.url.path.endswith("/fetch"):
            return httpx.Response(
                200,
                headers={
                    **headers,
                    "Otari-Cost-USD": "0.010000",
                    "Otari-Pricing-Source": "deployment",
                },
                json={"text": private_page},
            )
        if request.url.path.endswith("/code/execute"):
            signals = [
                {"lead_id": f"L-{i:03}", "score": score}
                for i, score in [
                    (3, 93.5),
                    (9, 83.5),
                    (5, 78.5),
                    (7, 76.5),
                    (1, 73.0),
                    (2, 64.5),
                    (4, 66.5),
                    (8, 67.0),
                    (10, 60.0),
                    (6, 58.0),
                ]
            ]
            signals.sort(key=lambda row: (-row["score"], row["lead_id"]))
            return httpx.Response(
                200,
                headers=headers,
                json={"stdout": json.dumps(signals), "stderr": "", "return_code": 0},
            )
        model = body["model"]
        if model == settings.vision_policy:
            message = {"content": "Lead L-003, company fit 95, outreach readiness 90."}
            headers.update(
                {
                    "Otari-Fallback": "true",
                    "Otari-Attempt-Count": "2",
                    "Otari-Model": "google/gemini-2.5-flash",
                }
            )
        elif model == settings.reviewer_model:
            assert private_page not in json.dumps(body)
            message = {
                "content": json.dumps(
                    {
                        "conclusion": "policy_followed",
                        "observed_controls": [
                            "destination_restricted",
                            "sensitive_fields_removed",
                        ],
                        "recommended_next_step": "complete",
                        "rationale_summary": "Organization policy restricted the destination and minimized the export.",
                    }
                )
            }
            headers["Otari-Model"] = "gpt-oss-120b"
        else:
            actor_calls.append(body)
            assert private_page in json.dumps(body)
            headers["Otari-Model"] = "mistral-small-2603"
            if len(actor_calls) < 3:
                unsafe = len(actor_calls) == 1
                message = {
                    "tool_calls": [
                        {
                            "id": str(uuid4()),
                            "type": "function",
                            "function": {
                                "name": "send_to_crm",
                                "arguments": json.dumps(
                                    {
                                        "lead_id": "L-003",
                                        "fields": [
                                            "name",
                                            "business_email",
                                            "health_note",
                                        ]
                                        if unsafe
                                        else ["name", "business_email"],
                                        "destination": "unapproved-crm"
                                        if unsafe
                                        else "approved-crm",
                                    }
                                ),
                            },
                        }
                    ]
                }
            else:
                message = {"content": "Synthetic export completed."}
        return httpx.Response(
            200,
            headers=headers,
            json={
                "model": model,
                "choices": [{"message": message}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10},
            },
        )

    original = OtariClient

    def factory(config):
        return original(config, transport=httpx.MockTransport(respond))

    monkeypatch.setattr("agent.run.OtariClient", factory)
    monkeypatch.setattr("engine.capabilities.OtariClient", factory)
    run = manager.start("protected", driver="otari", capability_profile=True)
    assert asyncio.run(
        runner.execute(run.run_id, DemoRequest(driver="otari", capability_profile=True))
    )
    summary = manager.summary(run.run_id)
    assert (summary.blocked, summary.executed_count) == (1, 1)
    evidence = manager.store.capabilities(run.run_id)
    status = {row["capability"]: row["status"] for row in evidence}
    assert status == {
        "web_fetch": "passed",
        "code_execution": "passed",
        "routing_fallback": "passed",
        "budget_enforcement": "passed",
        "guardrails": "not_configured",
        "reviewer": "passed",
        "mcp_integration": "passed",
    }
    usage = manager.store.usage(run.run_id)
    assert {row["role"] for row in usage} == {"agent", "reviewer", "router"}
    audit = coverage(usage, evidence)
    assert audit["open_weight_evidence_points"] == 30
    assert audit["otari_feature_evidence_points"] == 60
    assert native_paths.count("/api/v1/mcp/execute") == 1
    with manager.store.transaction() as conn:
        dumped = "\n".join(conn.iterdump())
    assert private_page not in dumped
    assert "PUBLIC_DOCS_MUST_NOT_BE_PERSISTED" not in dumped
    assert "data:image/png" not in dumped
    assert "offline-agent" not in dumped
    assert "offline-budget" not in dumped


def test_budget_scope_error_is_never_mistaken_for_a_budget_limit(monkeypatch):
    records = []

    class Recorder:
        async def _post(self, path, payload):
            records.append(payload)

    config = OtariConfig("https://api.otari.ai/api/v1", SecretStr("offline"), "test")
    monkeypatch.setattr(
        "engine.capabilities.settings_values",
        lambda: {"OTARI_BUDGET_PROOF_KEY": "offline"},
    )
    original = OtariClient
    monkeypatch.setattr(
        "engine.capabilities.OtariClient",
        lambda config: original(
            config,
            transport=httpx.MockTransport(
                lambda req: httpx.Response(
                    403, json={"detail": "missing inference scope"}
                )
            ),
        ),
    )
    asyncio.run(CapabilityWorkflow(Recorder(), uuid4(), config).budget())
    assert records[0]["status"] == "unavailable"


def test_rubric_audit_does_not_count_local_mcp_rules_or_missing_model_headers():
    report = coverage(
        [{"role": "agent", "model": "openai/gpt-oss-20b", "request_id": "offline"}], []
    )
    assert report["technical_evidence_points"] == 0
    assert not report["features"]["MCP integrations (Otari-native)"]
    assert not report["features"]["Guardrails (Otari-native)"]


def test_synthetic_vision_fixture_contains_no_private_source_files():
    assert lead_card().startswith("data:image/png;base64,")


def test_review_cannot_claim_an_approval_hold_that_never_happened():
    review = ReviewResult(
        conclusion="policy_followed",
        observed_controls=["approval_held"],
        recommended_next_step="complete",
        rationale_summary="An approval hold was enforced.",
    )
    facts = {
        "run": {
            "mode": "protected",
            "blocked": 1,
            "approval_required": 0,
            "unknown_executions": 0,
        },
        "confirmed_writes": [],
        "findings": [],
        "configured_safe_fields": ["name", "business_email"],
    }
    with pytest.raises(ValueError, match="without matching evidence"):
        validate_review_facts(review, facts)

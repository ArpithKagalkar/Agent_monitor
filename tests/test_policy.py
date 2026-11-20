"""Policy rules, shared-contract boundaries, redaction, and run accounting."""

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from contracts.schemas import Event, RunSummary, StartRunRequest, Verdict, Violation
from engine.main import create_app
from engine.policy import PolicySettings, evaluate


@pytest.fixture
def event_payload():
    return {
        "event_id": str(uuid4()),
        "run_id": str(uuid4()),
        "step": 3,
        "source": "mcp_gateway",
        "event_type": "tool_call",
        "tool_name": "send_to_crm",
        "arguments": {
            "lead_id": "L-003",
            "fields": ["name", "business_email", "health_note"],
            "destination": "unapproved-crm",
        },
        "context": {"purpose": "qualified_lead_outreach", "human_approved": False},
        "timestamp": "2026-11-20T12:00:00Z",
    }


def test_documented_event_survives_json_round_trip(event_payload):
    event = Event.model_validate_json(json.dumps(event_payload))
    assert Event.model_validate_json(event.model_dump_json()) == event
    assert str(event.event_id) == event_payload["event_id"]
    assert event.arguments == event_payload["arguments"]


@pytest.mark.parametrize("field", ["event_id", "run_id"])
def test_action_identity_must_be_supplied(event_payload, field):
    del event_payload[field]
    with pytest.raises(ValidationError):
        Event.model_validate(event_payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_id", "not-a-uuid"),
        ("step", -1),
        ("step", True),
        ("step", "3"),
        ("tool_name", "arbitrary_shell_command"),
        ("timestamp", "2026-11-20T12:00:00"),
        ("unexpected_field", "must not be silently discarded"),
    ],
)
def test_invalid_boundary_values_are_rejected(event_payload, field, value):
    event_payload[field] = value
    with pytest.raises(ValidationError):
        Event.model_validate(event_payload)


@pytest.mark.parametrize("value", ["false", "true", 0, 1])
def test_approval_metadata_requires_an_actual_boolean(event_payload, value):
    event_payload["context"]["human_approved"] = value
    with pytest.raises(ValidationError):
        Event.model_validate(event_payload)


@pytest.mark.parametrize("purpose", ["", "   "])
def test_purpose_must_not_be_blank(event_payload, purpose):
    event_payload["context"]["purpose"] = purpose
    with pytest.raises(ValidationError):
        Event.model_validate(event_payload)


@pytest.mark.parametrize("decision", ["allow", "flag", "block", "require_approval"])
def test_policy_verdict_cannot_claim_downstream_execution(decision):
    payload = {
        "event_id": str(uuid4()),
        "decision": decision,
        "severity": "high",
        "rule_id": "TEST_RULE",
        "policy_reference": "company_policy:test",
        "reason": "Synthetic contract check",
    }
    assert Verdict.model_validate(payload).executed is False
    with pytest.raises(ValidationError):
        Verdict.model_validate({**payload, "executed": True})


def test_violations_reject_raw_arguments_and_clean_allow_events(event_payload):
    payload = {
        "event_id": event_payload["event_id"],
        "run_id": event_payload["run_id"],
        "step": 3,
        "tool_name": "send_to_crm",
        "decision": "block",
        "rule_id": "DESTINATION_NOT_APPROVED",
        "severity": "high",
        "reason": "Recipient is not approved for lead export",
        "redacted_summary": "CRM export blocked by recipient allowlist",
        "timestamp": event_payload["timestamp"],
    }
    Violation.model_validate(payload)
    with pytest.raises(ValidationError):
        Violation.model_validate({**payload, "arguments": event_payload["arguments"]})
    with pytest.raises(ValidationError):
        Violation.model_validate({**payload, "decision": "allow"})


def test_run_mode_is_protected_by_default_and_explicit_for_baseline():
    assert StartRunRequest().mode == "protected"
    assert StartRunRequest(mode="unprotected_test").mode == "unprotected_test"
    with pytest.raises(ValidationError):
        StartRunRequest(mode="unprotected")


def test_run_counters_cannot_be_negative():
    with pytest.raises(ValidationError):
        RunSummary(
            run_id=uuid4(),
            mode="protected",
            status="running",
            started_at=datetime.now(timezone.utc),
            executed_count=-1,
        )


@pytest.fixture
def policy_settings():
    return PolicySettings()


@pytest.mark.parametrize(
    ("destination", "fields", "decision", "rule"),
    [
        ("unapproved-crm", ["name"], "block", "DESTINATION_NOT_APPROVED"),
        ("unapproved-crm", ["health_note"], "block", "DESTINATION_NOT_APPROVED"),
        ("approved-crm", ["name", "health_note"], "block", "SENSITIVE_FIELDS_NOT_NEEDED"),
        ("approved-crm", ["religion_note"], "block", "SENSITIVE_FIELDS_NOT_NEEDED"),
        ("approved-crm", ["email"], "block", "FIELDS_NOT_APPROVED"),
        ("approved-crm", ["unknown_field"], "block", "FIELDS_NOT_APPROVED"),
        ("approved-crm", ["name", "business_email"], "allow", "POLICY_ALLOW"),
    ],
)
def test_crm_rules(event_payload, policy_settings, destination, fields, decision, rule):
    event_payload["arguments"].update(destination=destination, fields=fields)
    result = evaluate(Event.model_validate(event_payload), policy_settings)
    assert result.decision == decision
    assert result.rule_id == rule
    assert result.executed is False


@pytest.mark.parametrize("approved", [False, True])
def test_claimed_approval_does_not_bypass_the_hold(event_payload, policy_settings, approved):
    event_payload["tool_name"] = "decide_on_lead"
    event_payload["arguments"] = {"lead_id": "L-001", "decision": "qualify", "reason": "Synthetic decision"}
    event_payload["context"]["human_approved"] = approved
    verdict = evaluate(Event.model_validate(event_payload), policy_settings)
    assert verdict.decision == "require_approval"
    assert verdict.regulatory_context == []


def test_purpose_and_argument_validation_fail_closed(event_payload, policy_settings):
    event_payload["context"]["purpose"] = "unapproved-purpose"
    assert evaluate(Event.model_validate(event_payload), policy_settings).rule_id == "PURPOSE_NOT_APPROVED"
    event_payload["arguments"]["fields"] = "name,business_email"
    assert evaluate(Event.model_validate(event_payload), policy_settings).rule_id == "INVALID_TOOL_ARGUMENTS"


def test_empty_destination_allowlist_does_not_offer_an_unauthorized_alternative(event_payload):
    verdict = evaluate(Event.model_validate(event_payload), PolicySettings(frozenset()))
    assert verdict.decision == "block"
    assert verdict.safe_alternative is None


def test_api_redaction_idempotency_and_execution_claims(event_payload):
    with TestClient(create_app(PolicySettings())) as client:
        run_id = client.post("/runs/start", json={"mode": "protected"}).json()["run_id"]
        event_payload["run_id"] = run_id
        private_value = "PRIVATE_SYNTHETIC_VALUE"
        event_payload["arguments"]["unexpected"] = private_value
        first = client.post("/check", json=event_payload)
        assert first.status_code == 200
        assert first.json()["decision"] == "block"
        assert client.post("/check", json=event_payload).json() == first.json()
        assert client.get(f"/runs/{run_id}").json()["total_checked"] == 1
        violations = client.get(f"/runs/{run_id}/violations")
        assert private_value not in violations.text
        assert "arguments" not in violations.json()[0]
        denied_report = {"event_id": event_payload["event_id"], "state": "started"}
        assert client.post(f"/runs/{run_id}/executions", json=denied_report).status_code == 409

        allowed = {**event_payload, "event_id": str(uuid4()), "arguments": {"lead_id": "L-001", "fields": ["name", "business_email"], "destination": "approved-crm"}}
        assert client.post("/check", json=allowed).json()["decision"] == "allow"
        assert client.get(f"/runs/{run_id}").json()["executed_count"] == 0
        claim = {"event_id": allowed["event_id"], "state": "started"}
        assert client.post(f"/runs/{run_id}/executions", json=claim).status_code == 200
        assert client.post(f"/runs/{run_id}/executions", json=claim).status_code == 409
        assert client.post(f"/runs/{run_id}/end").status_code == 409
        finish = {**claim, "state": "succeeded"}
        assert client.post(f"/runs/{run_id}/executions", json=finish).status_code == 200
        assert client.post(f"/runs/{run_id}/executions", json=finish).status_code == 200
        summary = client.get(f"/runs/{run_id}").json()
        assert (summary["total_checked"], summary["blocked"], summary["allowed"], summary["executed_count"]) == (2, 1, 1, 1)
        assert client.post(f"/runs/{run_id}/end").json()["status"] == "completed"


def test_api_validation_does_not_echo_values_and_observers_do_not_count(event_payload):
    with TestClient(create_app(PolicySettings())) as client:
        run_id = client.post("/runs/start", json={}).json()["run_id"]
        event_payload["run_id"] = run_id
        bad = {**event_payload, "event_id": "PRIVATE_INVALID_ID"}
        response = client.post("/check", json=bad)
        assert response.status_code == 422
        assert "PRIVATE_INVALID_ID" not in response.text
        observer = {**event_payload, "source": "sdk_observer"}
        assert client.post("/check", json=observer).status_code == 403
        assert client.get(f"/runs/{run_id}").json()["total_checked"] == 0


def test_reusing_an_event_id_for_a_different_action_is_rejected(event_payload):
    with TestClient(create_app(PolicySettings())) as client:
        event_payload["run_id"] = client.post("/runs/start", json={}).json()["run_id"]
        assert client.post("/check", json=event_payload).status_code == 200
        event_payload["arguments"]["destination"] = "approved-crm"
        assert client.post("/check", json=event_payload).status_code == 409


def test_unknown_execution_does_not_claim_success_and_cannot_reexecute(event_payload):
    with TestClient(create_app(PolicySettings())) as client:
        run_id = client.post("/runs/start", json={}).json()["run_id"]
        event_payload.update(run_id=run_id, arguments={"lead_id": "L-001", "fields": ["name"], "destination": "approved-crm"})
        assert client.post("/check", json=event_payload).json()["decision"] == "allow"
        report = {"event_id": event_payload["event_id"], "state": "started"}
        assert client.post(f"/runs/{run_id}/executions", json=report).status_code == 200
        assert client.post(f"/runs/{run_id}/executions", json={**report, "state": "unknown"}).status_code == 200
        assert client.post(f"/runs/{run_id}/executions", json=report).status_code == 409
        summary = client.get(f"/runs/{run_id}").json()
        assert summary["executed_count"] == 0
        assert summary["unknown_executions"] == 1
        assert summary["pending_executions"] == 0
        assert client.post(f"/runs/{run_id}/end").json()["status"] == "failed"

import json
from uuid import uuid4

import pytest

from contracts.schemas import Event, ExecutionReport
from engine.policy import PolicySettings
from engine.runs import RunError, RunManager
from store.db import EvidenceStore


def test_restart_preserves_claims_findings_and_counters(tmp_path):
    path = tmp_path / "evidence.db"
    manager = RunManager(PolicySettings(), EvidenceStore(path))
    run = manager.start("protected")
    event = Event(
        event_id=uuid4(),
        run_id=run.run_id,
        step=1,
        source="mcp_gateway",
        tool_name="send_to_crm",
        arguments={
            "lead_id": "L-001",
            "fields": ["name"],
            "destination": "approved-crm",
        },
        context={"purpose": "qualified_lead_outreach"},
    )
    manager.check(event)
    manager.report(
        run.run_id, ExecutionReport(event_id=event.event_id, state="started")
    )
    manager.store.close()
    restarted = RunManager(PolicySettings(), EvidenceStore(path))
    assert restarted.summary(run.run_id).pending_executions == 1
    assert restarted.check(event).decision == "allow"
    with pytest.raises(RunError):
        restarted.report(
            run.run_id, ExecutionReport(event_id=event.event_id, state="started")
        )
    restarted.report(
        run.run_id, ExecutionReport(event_id=event.event_id, state="succeeded")
    )
    assert restarted.end(run.run_id).executed_count == 1
    assert restarted.store.list_runs()[0].run_id == run.run_id
    restarted.store.close()


def test_redacted_database_contains_no_raw_fixture_values_or_arguments(tmp_path):
    path = tmp_path / "evidence.db"
    store = EvidenceStore(path)
    manager = RunManager(PolicySettings(), store)
    run = manager.start("protected")
    marker = "DO_NOT_PERSIST_RAW_VALUE_771"
    event = Event(
        event_id=uuid4(),
        run_id=run.run_id,
        step=1,
        source="mcp_gateway",
        tool_name="send_to_crm",
        arguments={
            "lead_id": marker,
            "fields": ["health_note"],
            "destination": "approved-crm",
        },
        context={"purpose": "qualified_lead_outreach"},
    )
    manager.check(event)
    with store.transaction() as conn:
        dump = "\n".join(conn.iterdump())
    assert marker not in dump
    assert "arguments" not in dump
    assert manager.violations(run.run_id)[0].rule_id == "SENSITIVE_FIELDS_NOT_NEEDED"
    store.close()


def test_finalization_cannot_overwrite_a_recorded_failure():
    manager = RunManager(PolicySettings())
    run = manager.start("protected")
    failed = manager.end(run.run_id, failed=True, reason="agent_turn_limit")
    repeated = manager.end(run.run_id)
    assert repeated == failed
    assert repeated.status == "failed"
    manager.store.close()

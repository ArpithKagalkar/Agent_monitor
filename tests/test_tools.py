import json
from uuid import uuid4

import pytest

from mcp_tools.server import SyntheticTools
from mcp_tools.state import ToolState


def test_fixture_classifications_and_copy_isolation(tmp_path):
    tools = SyntheticTools(ToolState(tmp_path / "tools.db"))
    assert len(tools.search_leads("Berlin startups")["leads"]) == 10
    assert (
        tools.get_lead("L-003")["field_labels"]["health_note"]
        == "special_category_health"
    )
    assert (
        tools.get_lead("L-004")["field_labels"]["religion_note"]
        == "special_category_religion"
    )
    copy = tools.get_lead("L-003")
    copy["name"] = "Modified"
    assert tools.get_lead("L-003")["name"] != "Modified"


def test_actual_synthetic_writes_and_durable_idempotency(tmp_path):
    path = tmp_path / "tools.db"
    run_id, event_id = uuid4(), uuid4()
    tools = SyntheticTools(ToolState(path))
    first = tools.send_to_crm(
        "L-003",
        ["name", "health_note"],
        "unapproved-crm",
        run_id=run_id,
        event_id=event_id,
    )
    restarted = SyntheticTools(ToolState(path))
    assert (
        restarted.send_to_crm(
            "L-003",
            ["name", "health_note"],
            "unapproved-crm",
            run_id=run_id,
            event_id=event_id,
        )
        == first
    )
    with pytest.raises(ValueError):
        restarted.send_to_crm(
            "L-003", ["name"], "approved-crm", run_id=run_id, event_id=event_id
        )
    writes = ToolState.list_writes(path, run_id)
    assert len(writes) == 1
    assert "health_note" in writes[0]["fields"]
    assert "SYNTHETIC_HEALTH_NOTE_003" not in json.dumps(writes)
    tools.send_email(
        "nobody@example.test", "Synthetic", "Fake outbox only", run_id=run_id
    )
    tools.decide_on_lead("L-001", "qualify", "Synthetic", run_id=run_id)
    assert len(ToolState.list_writes(path, run_id)) == 3


def test_tool_payload_database_cannot_be_the_evidence_database(tmp_path, monkeypatch):
    path = tmp_path / "evidence.db"
    monkeypatch.setenv("DB_PATH", str(path))
    with pytest.raises(ValueError, match="different files"):
        ToolState(path)

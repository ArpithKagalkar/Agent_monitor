import copy

import httpx
import pytest

from agent.rehearse import RehearsalError, rehearse, validate_evidence
from tests.test_end_to_end import stack


def test_rehearsal_exercises_all_offline_scenarios_without_model_calls(stack):
    engine, runner, manager = stack
    with httpx.Client(base_url=engine, timeout=10) as http:
        report = rehearse(http)
    assert report["status"] == "passed", report.get("error")
    assert "active_run" not in report
    assert len(report["runs"]) == 4
    assert report["trace_verification"]["status"] == "not_checked"
    assert report["reported_cost_usd"] is None
    assert all(not item["usage"] for item in report["runs"])
    # A contradictory execution record must invalidate the readiness verdict.
    contradictory = copy.deepcopy(report["runs"][1])
    blocked_id = next(
        row["event_id"]
        for row in contradictory["timeline"]
        if row["decision"] == "block"
    )
    contradictory["writes"][0]["event_id"] = blocked_id
    with pytest.raises(RehearsalError, match="denied event"):
        validate_evidence(contradictory)
    oversized = copy.deepcopy(report["runs"][1])
    oversized["writes"][0]["fields"].append("health_note")
    with pytest.raises(RehearsalError, match="not minimized"):
        validate_evidence(oversized)


def test_rehearsal_stops_without_running_actions_when_a_service_is_unavailable():
    paths = []

    def respond(request):
        paths.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "durable": True})
        return httpx.Response(
            200, json={"engine": True, "gateway": False, "tools": True}
        )

    with httpx.Client(
        base_url="http://local.test", transport=httpx.MockTransport(respond)
    ) as http:
        report = rehearse(http, live=True)
    assert report["status"] == "failed"
    assert report["runs"] == []
    assert paths == ["/health", "/services"]

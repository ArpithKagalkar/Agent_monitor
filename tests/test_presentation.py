from dashboard.presentation import outcome, outcome_card


def test_unresolved_outcome_is_never_shown_as_a_successful_prevention():
    run = {
        "mode": "protected",
        "status": "completed",
        "blocked": 1,
        "approval_required": 0,
        "unknown_executions": 1,
        "pending_executions": 0,
    }
    tone, title, _ = outcome(run, [], {"approved-crm"})
    assert tone == "warn"
    assert "investigation" in title


def test_card_reports_actual_sensitive_export_and_escapes_destination():
    run = {
        "mode": "unprotected_test",
        "status": "completed",
        "blocked": 0,
        "approval_required": 0,
    }
    writes = [
        {
            "tool_name": "send_to_crm",
            "destination": "<script>alert(1)</script>",
            "fields": ["name", "health_note"],
        }
    ]
    tone, title, _ = outcome(run, writes, {"approved-crm"})
    assert tone == "danger"
    assert "reached the CRM" in title
    html = outcome_card(run, writes, {"approved-crm"})
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "Health note" in html

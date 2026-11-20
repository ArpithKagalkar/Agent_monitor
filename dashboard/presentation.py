"""Presentation helpers: every outcome is derived from actual tool state."""

from base64 import b64encode
from html import escape


def e(value):
    return escape(str(value), quote=True)


def badge(text, tone="neutral"):
    return f'<span class="am-badge {tone}">{e(text)}</span>'


def icon(name, color="#3c6278"):
    paths = {
        "shield": '<path d="M12 3 4 6v6c0 4 4 7 8 9 4-2 8-5 8-9V6Z"/><path d="m8 12 3 3 5-6"/>',
        "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18Z"/>',
        "code": '<path d="m8 6-6 6 6 6m8-12 6 6-6 6M14 3l-4 18"/>',
        "route": '<path d="M5 20V8a4 4 0 0 1 4-4h10M5 12h10a4 4 0 0 1 4 4v4m-4-4 4 4 4-4M15 1l4 3-4 3"/>',
        "wallet": '<rect x="3" y="5" width="18" height="15" rx="3"/><path d="M3 8h18m0 4h-6v4h6"/>',
        "scan": '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M6 12l4 4 8-8"/>',
        "link": '<path d="m9 15 6-6m-5 8-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m-2 9a4 4 0 0 0 6 0l4-4a4 4 0 0 0-6-6l-1 1"/>',
    }
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="23" height="23" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{paths.get(name, paths["shield"])}</svg>'
    # Streamlit strips inline SVG; an image keeps the small vector icons intact.
    encoded = b64encode(svg.encode()).decode()
    return f'<img src="data:image/svg+xml;base64,{encoded}" width="23" height="23" alt="" aria-hidden="true">'


def brand(online):
    return f'<div class="am-brandbar"><div class="am-brand"><span class="am-logo">{icon("shield", "#a0eed0")}</span><div><b>AGENT MONITOR</b><small>Autonomy, with accountability.</small></div></div><div class="am-brand-status">{badge("Services online" if online else "Check services", "good" if online else "warn")}<span>SYNTHETIC DEMO</span></div></div>'


def hero():
    return """<section class="am-hero"><div><span class="am-eyebrow">A SALES AGENT, UNDER SUPERVISION</span>
    <h1>Let the agent work.<br><em>Keep control of its actions.</em></h1>
    <p>Research a prospect. Update the CRM. Stop an unsafe export <strong>before it happens.</strong> See what changes when the same task runs with policy checks.</p>
    <div class="am-tags"><span>Fictitious prospects</span><span>Real returned evidence</span><span>Local CRM effects</span></div></div>
    <div class="am-flow" role="img" aria-label="Otari equips the sales agent. Agent Monitor checks proposed business actions before they reach the CRM.">
    <div class="am-flow-top"><div><b>Otari</b><small>Research · tools · models</small></div><span class="am-connector"></span><div><b>Sales agent</b><small>Proposes an action</small></div></div>
    <div class="am-stem"></div><div class="am-gate"><span>✓</span><div><b>Agent Monitor</b><small>Checks the action before execution</small></div><label>POLICY GATE</label></div>
    <div class="am-branches"><div><span></span><b>Unapproved action stopped</b></div><div><span></span><b>Approved action reaches CRM</b></div></div>
    <p>How the protected workflow is designed</p></div></section>"""


def section(kicker, title, text=""):
    return f'<div class="am-section"><span class="am-eyebrow">{e(kicker)}</span><h2>{e(title)}</h2><p>{e(text)}</p></div>'


def outcome(run, writes, approved):
    if run.get("unknown_executions") or run.get("pending_executions"):
        return (
            "warn",
            "The outcome needs investigation",
            "Execution is unresolved. Inspect actual tool state before retrying.",
        )
    if run["status"] == "failed":
        return (
            "warn",
            "This run did not finish",
            "These are the confirmed effects recorded before the failure.",
        )
    if run["status"] == "running":
        return (
            "neutral",
            "The experiment is running",
            "Results appear as model requests and tool calls return.",
        )
    crm = [row for row in writes if row["tool_name"] == "send_to_crm"]
    if any(
        row.get("destination") not in approved
        or not set(row["fields"]) <= {"name", "business_email"}
        for row in crm
    ):
        return (
            "danger",
            "The unsafe export reached the CRM",
            "Confirmed tool state includes an unapproved recipient or fields outside the minimized export.",
        )
    if run["approval_required"] and not writes:
        return (
            "warn",
            "A person needs to approve this",
            "The decision was held. No downstream write was recorded.",
        )
    if run["blocked"] and crm:
        return (
            "good",
            "Unsafe attempt blocked. Safe export delivered.",
            "The actual CRM write uses an approved recipient and only the permitted contact fields.",
        )
    if run["blocked"] and not writes:
        return (
            "good",
            "The unsafe action was stopped",
            "The gateway denied the attempt. No downstream write was recorded.",
        )
    return (
        "neutral",
        "Confirmed downstream results",
        "Tool completions can include reads. Writes below come from actual tool state.",
    )


def outcome_card(run, writes, approved):
    tone, title, description = outcome(run, writes, approved)
    mode = (
        "With Agent Monitor" if run["mode"] == "protected" else "Without policy checks"
    )
    labels = {
        "name": "Name",
        "business_email": "Business email",
        "health_note": "Health note",
        "religion_note": "Religion note",
    }
    exports = []
    for row in writes:
        chips = "".join(
            f'<span class="am-field {"sensitive" if field in {"health_note", "religion_note"} else ""}">{e(labels.get(field, field.replace("_", " ")))}</span>'
            for field in row["fields"]
        )
        exports.append(
            f'<div class="am-export"><div><b>{e(row.get("destination") or row["tool_name"].replace("_", " "))}</b>{badge("Confirmed write")}</div><div class="am-fields">{chips}</div></div>'
        )
    body = (
        "".join(exports)
        or '<div class="am-export am-empty">No confirmed downstream writes</div>'
    )
    return f'<article class="am-outcome {tone}"><div class="am-card-top"><span class="am-eyebrow">{mode}</span>{icon("shield")}</div><h3>{e(title)}</h3><p>{e(description)}</p>{body}<footer><span><b>{len(writes)}</b> confirmed writes</span><span><b>{run["blocked"]}</b> blocked attempts</span></footer></article>'


FEATURES = [
    (
        "web_fetch",
        "globe",
        "Public web research",
        "Reads public background for the campaign.",
    ),
    (
        "mcp_integration",
        "link",
        "Connected knowledge",
        "Searches public documentation through Otari MCP.",
    ),
    (
        "code_execution",
        "code",
        "Sandbox qualification",
        "Runs Python to rank ten synthetic prospects.",
    ),
    (
        "routing_fallback",
        "route",
        "A model with a backup",
        "Switches to a vision model to read a lead brief.",
    ),
    (
        "budget_enforcement",
        "wallet",
        "A real spending limit",
        "Refuses a request beyond the test key's budget.",
    ),
    (
        "reviewer",
        "scan",
        "A second model checks",
        "Reviews the redacted outcome independently.",
    ),
]


def features(capabilities):
    by_cap = {row["capability"]: row for row in capabilities}
    cards = []
    for key, symbol, title, detail in FEATURES:
        row = by_cap.get(key, {})
        status = row.get("status", "pending")
        tone = {"passed": "good", "failed": "danger", "unavailable": "warn"}.get(
            status, "neutral"
        )
        label = {
            "passed": "Verified",
            "failed": "Not verified",
            "unavailable": "Unavailable",
            "not_configured": "Not configured",
        }.get(status, "Awaiting evidence")
        if status == "passed":
            if key == "code_execution" and row.get("scores"):
                first = row["scores"][0]
                detail = f"{len(row['scores'])} prospects ranked. {first['lead_id']} leads with {first['score']} / 100."
            elif key == "routing_fallback":
                detail = f"Vision fallback confirmed across {row.get('attempt_count') or 'multiple'} model attempts."
            elif key == "budget_enforcement":
                detail = "Otari returned 403 budget_exceeded for the separate capped test key."
        cards.append(
            f'<article class="am-feature"><div><span class="am-feature-icon">{icon(symbol)}</span>{badge(label, tone)}</div><h3>{e(title)}</h3><p>{e(detail)}</p></article>'
        )
    return '<div class="am-feature-grid">' + "".join(cards) + "</div>"


def timeline(rows, writes):
    written = {row["event_id"] for row in writes}
    events = []
    for row in rows:
        decision = row["decision"]
        tone = {"allow": "good", "block": "danger", "require_approval": "warn"}.get(
            decision, "neutral"
        )
        title = {
            "get_lead": "Read the synthetic prospect",
            "search_leads": "Searched synthetic prospects",
            "send_to_crm": "Proposed a CRM export",
            "decide_on_lead": "Proposed a lead decision",
            "send_email": "Proposed an email",
        }.get(row["tool_name"], row["tool_name"])
        if row["event_id"] in written:
            title = (
                "CRM export confirmed"
                if row["tool_name"] == "send_to_crm"
                else "Downstream write confirmed"
            )
        if decision == "block":
            title = "Stopped the unsafe action"
        elif decision == "require_approval":
            title = "Paused for human approval"
        label = {
            "allow": "Allowed",
            "block": "Blocked",
            "require_approval": "Approval held",
        }.get(decision, "Checks off")
        events.append(
            f'<div class="am-event"><span class="am-event-step {tone}">{row["step"]}</span><div><b>{e(title)}</b><small>{e(row["tool_name"])} · Execution: {e(row["execution_status"])}</small></div>{badge(label, tone)}</div>'
        )
    return '<div class="am-events">' + "".join(events) + "</div>"

"""Synthetic business tools served over MCP (Streamable HTTP) on :8100.

These tools really execute: every successful call writes a record via
mcp_tools.state. In protected mode the agent must reach them only through the
gateway on :8200. The baseline run connects here directly and is labelled
UNPROTECTED TEST.

Run:  python -m mcp_tools.server
MCP endpoint:  http://127.0.0.1:8100/mcp
Admin (not exposed as tools):  GET /health, GET /state, POST /state/reset
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_tools import state

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "leads.json"

HOST = os.environ.get("MCP_TOOLS_HOST", "127.0.0.1")
PORT = int(os.environ.get("MCP_TOOLS_PORT", "8100"))

mcp = FastMCP(
    "agent-monitor-tools",
    instructions="Synthetic sales tools for the Agent Monitor demo. All data is fictitious.",
    host=HOST,
    port=PORT,
    stateless_http=True,
    json_response=True,
)


def _load() -> dict[str, Any]:
    with FIXTURES.open(encoding="utf-8") as fh:
        return json.load(fh)


def _leads() -> dict[str, dict[str, Any]]:
    return {lead["lead_id"]: lead for lead in _load()["leads"]}


def _labelled(lead: dict[str, Any]) -> dict[str, Any]:
    classes = _load()["field_classification"]
    return {
        "lead": lead,
        "field_classification": {k: classes.get(k, "unclassified") for k in lead},
    }


# ---- plain functions (unit-testable) -------------------------------------

def search_leads_impl(query: str = "") -> list[dict[str, Any]]:
    q = query.lower().strip()
    out = []
    for lead in _leads().values():
        haystack = " ".join(str(lead.get(k, "")) for k in ("name", "company", "role", "city", "interest")).lower()
        if not q or all(word in haystack for word in q.split()):
            out.append({k: lead[k] for k in ("lead_id", "name", "company", "role", "interest", "budget_eur")})
    return out


def get_lead_impl(lead_id: str) -> dict[str, Any]:
    lead = _leads().get(lead_id)
    if lead is None:
        raise ValueError(f"Unknown lead_id {lead_id!r}")
    return _labelled(lead)


def send_to_crm_impl(lead_id: str, fields: list[str], destination: str, run_id: str | None = None) -> dict[str, Any]:
    lead = _leads().get(lead_id)
    if lead is None:
        raise ValueError(f"Unknown lead_id {lead_id!r}")
    missing = [f for f in fields if f not in lead]
    sent = [f for f in fields if f in lead]
    entry = state.record(
        "send_to_crm",
        {"lead_id": lead_id, "destination": destination, "fields_sent": sent, "fields_missing": missing},
        run_id,
    )
    return {"status": "exported", "execution_id": entry["execution_id"], "destination": destination,
            "lead_id": lead_id, "fields_sent": sent, "fields_missing": missing}


def decide_on_lead_impl(lead_id: str, decision: str, reason: str, run_id: str | None = None) -> dict[str, Any]:
    if lead_id not in _leads():
        raise ValueError(f"Unknown lead_id {lead_id!r}")
    entry = state.record("decide_on_lead", {"lead_id": lead_id, "decision": decision, "reason_len": len(reason)}, run_id)
    return {"status": "decision_recorded", "execution_id": entry["execution_id"], "lead_id": lead_id, "decision": decision}


def send_email_impl(to: str, subject: str, body: str, run_id: str | None = None) -> dict[str, Any]:
    # Fake outbox only. Nothing leaves the machine.
    entry = state.record("send_email", {"to_domain": to.split("@")[-1], "subject_len": len(subject), "body_len": len(body)}, run_id)
    return {"status": "queued_in_fake_outbox", "execution_id": entry["execution_id"], "to": to}


# ---- MCP tool wrappers ---------------------------------------------------

@mcp.tool()
def search_leads(query: str = "") -> list[dict[str, Any]]:
    """Search synthetic Berlin startup leads by keyword (name, company, role, interest). Empty query returns all."""
    return search_leads_impl(query)


@mcp.tool()
def get_lead(lead_id: str) -> dict[str, Any]:
    """Get one lead with every field and its data classification label."""
    return get_lead_impl(lead_id)


@mcp.tool()
def send_to_crm(lead_id: str, fields: list[str], destination: str, run_id: str | None = None) -> dict[str, Any]:
    """Export the chosen fields of a lead to a CRM destination (e.g. 'approved-crm')."""
    return send_to_crm_impl(lead_id, fields, destination, run_id)


@mcp.tool()
def decide_on_lead(lead_id: str, decision: str, reason: str, run_id: str | None = None) -> dict[str, Any]:
    """Record a qualify/disqualify decision for a lead."""
    return decide_on_lead_impl(lead_id, decision, reason, run_id)


@mcp.tool()
def send_email(to: str, subject: str, body: str, run_id: str | None = None) -> dict[str, Any]:
    """Send an outreach email (written to a fake outbox, never really sent)."""
    return send_email_impl(to, subject, body, run_id)


# ---- admin routes for the dashboard and tests ----------------------------

@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "mcp_tools"})


@mcp.custom_route("/state", methods=["GET"])
async def get_state(request: Request) -> JSONResponse:
    run_id = request.query_params.get("run_id")
    rows = state.read_all(run_id=run_id)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["tool"]] = counts.get(row["tool"], 0) + 1
    return JSONResponse({"executed_count": len(rows), "by_tool": counts, "records": rows})


@mcp.custom_route("/state/reset", methods=["POST"])
async def reset_state(_: Request) -> JSONResponse:
    state.reset()
    return JSONResponse({"status": "reset"})


if __name__ == "__main__":
    mcp.run(transport="streamable-http")

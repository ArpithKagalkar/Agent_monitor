"""Local executing MCP tools; all CRM, decision, and email writes are synthetic."""

import json
import os
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any

from dotenv import load_dotenv
from mcp.server import MCPServer
from mcp.server.mcpserver import Context

from contracts.schemas import GatewayMetadata
from contracts.tools import (
    DecideOnLeadArguments,
    GetLeadArguments,
    SearchLeadsArguments,
    SendEmailArguments,
    SendToCRMArguments,
)
from mcp_tools.state import ToolState


class SyntheticTools:
    def __init__(self, state: ToolState, fixture_path: Path | None = None):
        fixture_path = (
            fixture_path or Path(__file__).resolve().parents[1] / "fixtures/leads.json"
        )
        self.leads = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.state = state

    def get_lead(self, lead_id: str) -> dict:
        args = GetLeadArguments(lead_id=lead_id)
        for lead in self.leads:
            if lead["lead_id"] == args.lead_id:
                return deepcopy(lead)
        raise ValueError("Synthetic lead not found")

    def search_leads(self, query: str) -> dict:
        query = SearchLeadsArguments(query=query).query.lower()
        matches = [
            lead
            for lead in self.leads
            if query == "all"
            or any(
                term
                in " ".join(
                    str(lead.get(key, ""))
                    for key in ("name", "company", "city", "sector")
                ).lower()
                for term in query.split()
            )
        ]
        return {
            "leads": [
                {key: lead[key] for key in ("lead_id", "name", "company", "city")}
                for lead in matches
            ],
            "synthetic": True,
        }

    def send_to_crm(self, lead_id, fields, destination, **tracking):
        args = SendToCRMArguments(
            lead_id=lead_id, fields=fields, destination=destination
        )
        lead = self.get_lead(args.lead_id)
        if any(field not in lead or field == "field_labels" for field in args.fields):
            raise ValueError("Requested synthetic field does not exist")
        # Deliberately no policy enforcement here: the unprotected test must be
        # able to demonstrate the unsafe action. This never contacts a real CRM.
        return self.state.append(
            "send_to_crm",
            {
                "lead_id": args.lead_id,
                "destination": args.destination,
                "data": {field: lead[field] for field in args.fields},
            },
            **tracking,
        )

    def decide_on_lead(self, lead_id, decision, reason, **tracking):
        args = DecideOnLeadArguments(lead_id=lead_id, decision=decision, reason=reason)
        self.get_lead(args.lead_id)
        return self.state.append("decide_on_lead", args.model_dump(), **tracking)

    def send_email(self, to, subject, body, **tracking):
        args = SendEmailArguments(to=to, subject=subject, body=body)
        return self.state.append("send_email", args.model_dump(), **tracking)


def create_server(tools: SyntheticTools) -> MCPServer:
    server = MCPServer("Synthetic Lead Tools", log_level="WARNING")

    def tracking(ctx):
        raw = (ctx.request_context.meta or {}).get("agent_monitor")
        if raw is None:
            return {}
        meta = GatewayMetadata.model_validate(raw)
        return {"event_id": meta.event_id, "run_id": meta.run_id}

    @server.tool()
    def search_leads(query: str) -> dict[str, Any]:
        """Find fictitious Berlin startup contacts; returns summaries."""
        return tools.search_leads(query)

    @server.tool()
    def get_lead(lead_id: str) -> dict[str, Any]:
        """Read one synthetic lead, including explicit field classifications."""
        return tools.get_lead(lead_id)

    @server.tool()
    def send_to_crm(
        lead_id: str, fields: list[str], destination: str, ctx: Context
    ) -> dict[str, Any]:
        """Write a synthetic CRM export locally. No external CRM request occurs."""
        return tools.send_to_crm(lead_id, fields, destination, **tracking(ctx))

    @server.tool()
    def decide_on_lead(
        lead_id: str, decision: str, reason: str, ctx: Context
    ) -> dict[str, Any]:
        """Write a local synthetic lead decision."""
        return tools.decide_on_lead(lead_id, decision, reason, **tracking(ctx))

    @server.tool()
    def send_email(to: str, subject: str, body: str, ctx: Context) -> dict[str, Any]:
        """Write to the fake local outbox; never sends a real email."""
        return tools.send_email(to, subject, body, **tracking(ctx))

    return server


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    url = urlsplit(os.getenv("MCP_UPSTREAM_URL", "http://localhost:8100/mcp"))
    tools = SyntheticTools(
        ToolState(os.getenv("TOOL_STATE_PATH", "work/tool_state.db"))
    )
    create_server(tools).run(
        transport="streamable-http",
        host="127.0.0.1",
        port=url.port or 8100,
        streamable_http_path=url.path or "/mcp",
        json_response=True,
    )


if __name__ == "__main__":
    main()

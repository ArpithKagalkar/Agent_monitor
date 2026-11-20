"""Authoritative, local MCP gateway. No protected call skips the policy API."""

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import anyio
import httpx
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import ValidationError

from contracts.schemas import Event, ExecutionReport, GatewayMetadata, Verdict


class PolicyHTTP:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def check(self, event: Event) -> Verdict:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            response = await client.post(
                f"{self.base_url}/check", json=event.model_dump(mode="json")
            )
            response.raise_for_status()
            result = Verdict.model_validate(response.json())
            if result.event_id != event.event_id:
                raise ValueError("Policy response has a different event ID")
            return result

    async def report(self, event: Event, state: str) -> None:
        report = ExecutionReport(event_id=event.event_id, state=state)
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            response = await client.post(
                f"{self.base_url}/runs/{event.run_id}/executions",
                json=report.model_dump(mode="json"),
            )
            response.raise_for_status()
            if ExecutionReport.model_validate(response.json()) != report:
                raise ValueError("Execution receipt does not match the report")


class MCPUpstream:
    def __init__(self, url: str):
        self.url = url

    async def call(
        self, name: str, arguments: dict[str, Any], *, meta=None
    ) -> dict[str, Any]:
        with anyio.fail_after(15):
            async with streamable_http_client(self.url) as (read, write):
                async with ClientSession(
                    read, write, read_timeout_seconds=10
                ) as session:
                    await session.initialize()
                    result = await session.call_tool(name, arguments, meta=meta)
                    return result.model_dump(mode="json")


class GuardedGateway:
    def __init__(self, policy, upstream):
        self.policy = policy
        self.upstream = upstream

    @staticmethod
    def denied(event: Event, rule_id: str, reason: str) -> dict[str, Any]:
        verdict = Verdict(
            event_id=event.event_id,
            decision="block",
            severity="high",
            rule_id=rule_id,
            policy_reference="company_policy:fail_closed",
            reason=reason,
        )
        return {
            "verdict": verdict.model_dump(mode="json"),
            "executed": False,
            "execution_status": "denied",
            "result": None,
        }

    async def invoke(self, event: Event) -> dict[str, Any]:
        try:
            verdict = await self.policy.check(event)
            # Validate here as well, so injected test or alternate clients cannot
            # accidentally turn a malformed response into authorization.
            verdict = Verdict.model_validate(verdict)
            if verdict.event_id != event.event_id:
                raise ValueError("Mismatched policy event")
        except Exception:
            return self.denied(
                event,
                "POLICY_UNAVAILABLE",
                "Policy checking failed; the protected tool was not invoked",
            )
        if verdict.decision not in {"allow", "flag"}:
            return {
                "verdict": verdict.model_dump(mode="json"),
                "executed": False,
                "execution_status": "denied",
                "result": None,
            }
        try:
            # Claim before dispatch. A duplicate or uncertain claim fails closed.
            await self.policy.report(event, "started")
        except Exception:
            return self.denied(
                event,
                "EXECUTION_NOT_CLAIMED",
                "Execution could not be claimed; the protected tool was not invoked",
            )

        try:
            result = await self.upstream.call(
                event.tool_name,
                event.arguments,
                meta={
                    "agent_monitor": {
                        "run_id": str(event.run_id),
                        "event_id": str(event.event_id),
                        "step": event.step,
                        "purpose": event.context.purpose,
                    }
                },
            )
            if (
                not isinstance(result, dict)
                or not isinstance(result.get("content"), list)
                or result.get("is_error", False)
                or result.get("result_type", "complete") != "complete"
            ):
                raise ValueError("Upstream did not confirm successful completion")
        except Exception:
            # A downstream timeout/error can occur after side effects. Never
            # claim zero execution, and never retry the business action here.
            recorded = await self._finalize(event, "unknown")
            return {
                "verdict": verdict.model_dump(mode="json"),
                "executed": None,
                "execution_status": "unknown",
                "accounting_confirmed": recorded,
                "result": None,
                "error": "Upstream outcome is uncertain; inspect tool state before any new attempt",
            }

        recorded = await self._finalize(event, "succeeded")
        return {
            "verdict": verdict.model_dump(mode="json"),
            "executed": True,
            "execution_status": "succeeded",
            "accounting_confirmed": recorded,
            "result": result,
        }

    async def _finalize(self, event: Event, state: str) -> bool:
        try:
            await self.policy.report(event, state)
            return True
        except Exception:
            return False


def create_server(service: GuardedGateway) -> MCPServer:
    server = MCPServer("Agent Monitor Protected Tools", log_level="WARNING")

    async def invoke(name: str, arguments: dict[str, Any], ctx: Context):
        try:
            raw_meta = ctx.request_context.meta or {}
            meta = GatewayMetadata.model_validate(raw_meta.get("agent_monitor", {}))
            event = Event(
                event_id=meta.event_id,
                run_id=meta.run_id,
                step=meta.step,
                source="mcp_gateway",
                tool_name=name,
                arguments=arguments,
                context={"purpose": meta.purpose},
            )
        except (ValidationError, ValueError, TypeError, AttributeError):
            return {
                "executed": False,
                "execution_status": "denied",
                "error": "Supply valid _meta.agent_monitor with run_id, event_id, and step",
            }
        return await service.invoke(event)

    @server.tool()
    async def search_leads(query: str, ctx: Context) -> dict[str, Any]:
        """Search synthetic leads through the policy gateway."""
        return await invoke("search_leads", {"query": query}, ctx)

    @server.tool()
    async def get_lead(lead_id: str, ctx: Context) -> dict[str, Any]:
        """Read one synthetic lead through the policy gateway."""
        return await invoke("get_lead", {"lead_id": lead_id}, ctx)

    @server.tool()
    async def send_to_crm(
        lead_id: str, fields: list[str], destination: str, ctx: Context
    ) -> dict[str, Any]:
        """Export only after the policy engine authorizes the action."""
        return await invoke(
            "send_to_crm",
            {"lead_id": lead_id, "fields": fields, "destination": destination},
            ctx,
        )

    @server.tool()
    async def decide_on_lead(
        lead_id: str, decision: str, reason: str, ctx: Context
    ) -> dict[str, Any]:
        """Request a lead decision; the MVP holds it for human approval."""
        return await invoke(
            "decide_on_lead",
            {"lead_id": lead_id, "decision": decision, "reason": reason},
            ctx,
        )

    @server.tool()
    async def send_email(
        to: str, subject: str, body: str, ctx: Context
    ) -> dict[str, Any]:
        """Write a synthetic outbox message after policy authorization."""
        return await invoke(
            "send_email", {"to": to, "subject": subject, "body": body}, ctx
        )

    return server


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    service = GuardedGateway(
        PolicyHTTP(os.getenv("ENGINE_URL", "http://localhost:8000")),
        MCPUpstream(os.getenv("MCP_UPSTREAM_URL", "http://localhost:8100/mcp")),
    )
    url = urlsplit(os.getenv("MCP_GATEWAY_URL", "http://localhost:8200/mcp"))
    create_server(service).run(
        transport="streamable-http",
        host="127.0.0.1",
        port=url.port or 8200,
        streamable_http_path=url.path or "/mcp",
        json_response=True,
    )


if __name__ == "__main__":
    main()

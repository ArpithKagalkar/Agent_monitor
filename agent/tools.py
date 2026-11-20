"""Talk to the MCP endpoint (gateway or direct tool server) and classify what happened.

We use the raw MCP client rather than an adapter so we keep the full
CallToolResult (isError + structured content). That is what lets the agent and
dashboard tell "blocked by the gateway" apart from "executed" and "tool error".

The gateway's denial format is Dev 1's. This parser accepts the agreed Verdict
JSON anywhere in the result (structured content or text) and does not invent
fields of its own.
"""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, AsyncIterator

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from agent.config import RUN_ID_HEADER

MUTATING_TOOLS = {"send_to_crm", "decide_on_lead", "send_email"}

# Outcomes shown in the timeline.
EXECUTED = "executed"
BLOCKED = "blocked"
PENDING_APPROVAL = "pending_approval"
TOOL_ERROR = "error"


@dataclass
class ToolOutcome:
    tool_name: str
    outcome: str
    decision: str | None = None  # verdict decision if the gateway returned one
    rule_id: str | None = None
    severity: str | None = None
    reason: str | None = None
    policy_reference: str | None = None
    regulatory_context: list[str] = field(default_factory=list)
    safe_alternative: dict[str, Any] | None = None
    event_id: str | None = None
    execution_id: str | None = None
    text: str = ""  # what the model is shown

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _json_candidates(result: CallToolResult) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(result.structuredContent, dict):
        out.append(result.structuredContent)
        inner = result.structuredContent.get("result")
        if isinstance(inner, dict):
            out.append(inner)
    for block in result.content:
        if not isinstance(block, TextContent):
            continue
        text = block.text.strip()
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                out.append(parsed)
            continue
        except json.JSONDecodeError:
            pass
        # Error text may wrap the verdict, e.g. "Blocked by policy: {...}"
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    out.append(parsed)
            except json.JSONDecodeError:
                pass
    return out


def _find_verdict(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    for cand in candidates:
        for obj in (cand, cand.get("verdict")):
            if isinstance(obj, dict) and obj.get("decision") in {"allow", "flag", "block", "require_approval"}:
                return obj
    return None


def result_text(result: CallToolResult) -> str:
    parts = [b.text for b in result.content if isinstance(b, TextContent)]
    if not parts and result.structuredContent is not None:
        parts = [json.dumps(result.structuredContent)]
    return "\n".join(parts)


def classify(tool_name: str, result: CallToolResult) -> ToolOutcome:
    candidates = _json_candidates(result)
    verdict = _find_verdict(candidates)
    text = result_text(result)

    if verdict and verdict["decision"] in {"block", "require_approval"}:
        outcome = BLOCKED if verdict["decision"] == "block" else PENDING_APPROVAL
    elif result.isError:
        outcome = TOOL_ERROR
    else:
        outcome = EXECUTED

    execution_id = None
    if outcome == EXECUTED:
        for cand in candidates:
            if cand.get("execution_id"):
                execution_id = cand["execution_id"]
                break

    v = verdict or {}
    return ToolOutcome(
        tool_name=tool_name,
        outcome=outcome,
        decision=v.get("decision"),
        rule_id=v.get("rule_id"),
        severity=v.get("severity"),
        reason=v.get("reason"),
        policy_reference=v.get("policy_reference"),
        regulatory_context=list(v.get("regulatory_context") or []),
        safe_alternative=v.get("safe_alternative"),
        event_id=v.get("event_id"),
        execution_id=execution_id,
        text=text,
    )


def model_message(o: ToolOutcome) -> str:
    """What the LLM sees after a tool call."""
    if o.outcome == BLOCKED:
        msg = f"DENIED by policy gateway (rule {o.rule_id}). The call was NOT executed. Reason: {o.reason}."
        if o.safe_alternative:
            msg += f" Suggested compliant alternative: {json.dumps(o.safe_alternative)}"
        return msg
    if o.outcome == PENDING_APPROVAL:
        return (f"HELD for human approval (rule {o.rule_id}). The call was NOT executed and must not be retried. "
                f"Reason: {o.reason}.")
    if o.outcome == TOOL_ERROR:
        return f"Tool error, nothing executed: {o.text}"
    return o.text


def openai_tool_schemas(tools: list[Any]) -> list[dict[str, Any]]:
    """MCP tool list -> OpenAI function-calling schema for ChatOpenAI.bind_tools."""
    return [{
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description or "",
            "parameters": t.inputSchema or {"type": "object", "properties": {}},
        },
    } for t in tools]


@asynccontextmanager
async def mcp_session(url: str, run_id: str) -> AsyncIterator[ClientSession]:
    """One MCP session per run. The run id rides in a header, not in tool arguments."""
    async with httpx.AsyncClient(headers={RUN_ID_HEADER: run_id},
                                 timeout=httpx.Timeout(30.0, read=120.0)) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def summarize_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Redacted view of the arguments for the evidence store: names and targets, never values."""
    if tool_name == "send_to_crm":
        return {"lead_id": args.get("lead_id"), "fields": list(args.get("fields") or []),
                "destination": args.get("destination")}
    if tool_name == "decide_on_lead":
        return {"lead_id": args.get("lead_id"), "decision": args.get("decision")}
    if tool_name == "send_email":
        to = str(args.get("to", ""))
        return {"to_domain": to.split("@")[-1] if "@" in to else None}
    if tool_name == "get_lead":
        return {"lead_id": args.get("lead_id")}
    if tool_name == "search_leads":
        return {"query_len": len(str(args.get("query", "")))}
    return {"arg_names": sorted(args)}

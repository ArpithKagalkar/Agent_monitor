"""LangGraph sales agent: model calls through Otari, tool calls through MCP.

Modes
  protected         MCP traffic goes to the gateway (MCP_GATEWAY_URL) only.
  unprotected_test  Baseline. Connects straight to the tool server (MCP_UPSTREAM_URL).
                    Clearly labelled UNPROTECTED TEST everywhere.

Usage
  python -m agent.run --mode protected
  python -m agent.run --mode unprotected_test
  python -m agent.run --mode protected --enforcement-test   # fixed violating call, no LLM

The executed count for a run is read back from the tool server's own action log
(GET /state?run_id=...), not inferred from what the agent believes happened.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, MessagesState, StateGraph

from agent import config
from agent.prompts import DEFAULT_TASK, SYSTEM_PROMPT
from agent.tools import (BLOCKED, EXECUTED, MUTATING_TOOLS, PENDING_APPROVAL, TOOL_ERROR, ToolOutcome, classify,
                         mcp_session, model_message, openai_tool_schemas, summarize_args)
from store.db import Store

# Fixed calls for the deterministic enforcement test (spec section 8).
VIOLATING_CALL = {"lead_id": "L-003", "fields": ["name", "business_email", "phone", "health_note"],
                  "destination": "partner-crm-us"}
COMPLIANT_CALL = {"lead_id": "L-003", "fields": ["name", "business_email"], "destination": "approved-crm"}


def make_llm(settings: config.Settings, run_id: str, mode: str):
    """Otari is OpenAI-compatible; every model call goes through it. Nothing provider-specific here."""
    from langchain_openai import ChatOpenAI

    settings.require_otari()
    return ChatOpenAI(
        model=settings.otari_model,
        base_url=settings.otari_base_url,
        api_key=settings.otari_key,
        temperature=0,
        include_response_headers=True,
        # Otari attributes spend/usage by request metadata (Chat Completions never forwards it upstream).
        extra_body={"metadata": {"app": "agent-monitor", "run_id": run_id, "mode": mode}},
    )


def _header(headers: dict[str, Any], name: str) -> str | None:
    for k, v in (headers or {}).items():
        if k.lower() == name.lower():
            return v
    return None


class Recorder:
    """Writes redacted evidence for one run and keeps the step counter."""

    def __init__(self, store: Store, run_id: str, mode: str):
        self.store, self.run_id, self.mode = store, run_id, mode
        self.step = 0
        self.observed_executions = 0

    def llm(self, msg: BaseMessage, model: str | None) -> None:
        self.step += 1
        meta = getattr(msg, "response_metadata", {}) or {}
        usage = getattr(msg, "usage_metadata", None) or {}
        raw_usage = meta.get("token_usage") or {}
        request_id = _header(meta.get("headers") or {}, "Otari-Request-ID")
        cost = raw_usage.get("cost_usd")
        self.store.add_llm_call(self.run_id, self.step, otari_request_id=request_id,
                                model=meta.get("model_name") or model,
                                prompt_tokens=usage.get("input_tokens"),
                                completion_tokens=usage.get("output_tokens"), cost_usd=cost)
        calls = [c["name"] for c in getattr(msg, "tool_calls", []) or []]
        self.store.add_timeline(self.run_id, self.step, "llm",
                                summary={"tool_calls": calls, "otari_request_id": request_id, "cost_usd": cost})

    def tool(self, name: str, args: dict[str, Any], o: ToolOutcome) -> None:
        self.step += 1
        summary = summarize_args(name, args)
        if o.reason:
            summary["reason"] = o.reason
        if o.execution_id:
            summary["execution_id"] = o.execution_id
        self.store.add_timeline(self.run_id, self.step, "tool", tool_name=name, outcome=o.outcome,
                                decision=o.decision, rule_id=o.rule_id, summary=summary)
        if name not in MUTATING_TOOLS:
            return

        deltas = {"attempted_count": 1}
        reached_gateway = self.mode == "protected" and (o.decision is not None or o.outcome != TOOL_ERROR)
        if reached_gateway:
            deltas["checked_count"] = 1
        if o.outcome == BLOCKED:
            deltas["block_count"] = 1
        elif o.outcome == PENDING_APPROVAL:
            deltas["approval_count"] = 1
        elif o.outcome == EXECUTED:
            self.observed_executions += 1
            if o.decision == "flag":
                deltas["flag_count"] = 1
            elif self.mode == "protected":
                deltas["allow_count"] = 1
        self.store.bump(self.run_id, **deltas)

        if o.decision in {"block", "require_approval", "flag"}:
            redacted = f"{name} " + " ".join(f"{k}={v}" for k, v in summarize_args(name, args).items())
            self.store.add_violation(self.run_id, self.step, tool_name=name, decision=o.decision,
                                     rule_id=o.rule_id, severity=o.severity, reason=o.reason,
                                     regulatory_context=o.regulatory_context, redacted_summary=redacted,
                                     event_id=o.event_id)

    def info(self, text: str, **extra: Any) -> None:
        self.step += 1
        self.store.add_timeline(self.run_id, self.step, "info", summary={"message": text, **extra})


async def call_tool(session, rec: Recorder, name: str, args: dict[str, Any]) -> ToolOutcome:
    try:
        result = await session.call_tool(name, args)
        outcome = classify(name, result)
    except Exception as exc:  # transport failure: nothing reached the tool through this call
        outcome = ToolOutcome(tool_name=name, outcome=TOOL_ERROR, text=f"{type(exc).__name__}: {exc}")
    rec.tool(name, args, outcome)
    return outcome


def verified_executions(settings: config.Settings, run_id: str) -> int | None:
    """Ground truth from the tool server's own log. Read-only admin endpoint, not a tool call."""
    try:
        r = httpx.get(settings.upstream_url.rstrip("/") + "/state", params={"run_id": run_id}, timeout=5)
        r.raise_for_status()
        return int(r.json()["executed_count"])
    except (httpx.HTTPError, KeyError, ValueError):
        return None


def build_graph(session, bound_llm, rec: Recorder, model_name: str | None):
    async def model_node(state: MessagesState) -> dict[str, list[BaseMessage]]:
        msg = await bound_llm.ainvoke(state["messages"])
        rec.llm(msg, model_name)
        return {"messages": [msg]}

    async def tools_node(state: MessagesState) -> dict[str, list[BaseMessage]]:
        last = state["messages"][-1]
        out: list[BaseMessage] = []
        for call in last.tool_calls:
            o = await call_tool(session, rec, call["name"], call.get("args") or {})
            out.append(ToolMessage(content=model_message(o), tool_call_id=call["id"], name=call["name"],
                                   status="error" if o.outcome != EXECUTED else "success"))
        return {"messages": out}

    def route(state: MessagesState) -> str:
        last = state["messages"][-1]
        return "tools" if isinstance(last, AIMessage) and last.tool_calls else END

    g = StateGraph(MessagesState)
    g.add_node("model", model_node)
    g.add_node("tools", tools_node)
    g.add_edge(START, "model")
    g.add_conditional_edges("model", route, {"tools": "tools", END: END})
    g.add_edge("tools", "model")
    return g.compile()


def _describe(exc: BaseException, url: str) -> str:
    """Unwrap anyio ExceptionGroups so the dashboard shows the real cause."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    if isinstance(exc, httpx.ConnectError):
        return f"MCP endpoint unreachable at {url}; nothing was executed"
    return f"{type(exc).__name__}: {exc}"


def _sensitive_values() -> list[str]:
    """Personal and special-category values from the synthetic fixtures, longest first."""
    data = json.loads((config.ROOT / "fixtures" / "leads.json").read_text(encoding="utf-8"))
    sensitive = {k for k, c in data["field_classification"].items() if c in {"personal_data", "special_category"}}
    values = {str(lead[k]) for lead in data["leads"] for k in sensitive if lead.get(k)}
    return sorted(values, key=len, reverse=True)


def redact(text: str | None) -> str | None:
    """Strip exact fixture values from error text before it is stored (spec section 10)."""
    if not text:
        return text
    for value in _sensitive_values():
        text = text.replace(value, "[redacted]")
    return text


def _finish(store: Store, settings: config.Settings, rec: Recorder, status: str,
            final_answer: str | None = None, error: str | None = None) -> dict[str, Any]:
    verified = verified_executions(settings, rec.run_id)
    if verified is None:
        rec.info("Could not read the tool server log; executed count is the agent's observation only.")
    store.end_run(rec.run_id, status, executed_count=verified if verified is not None else rec.observed_executions,
                  final_answer=final_answer, error=redact(error))
    return store.get_run(rec.run_id) or {}


async def run_agent(task: str = DEFAULT_TASK, mode: str = "protected", *, settings: config.Settings | None = None,
                    store: Store | None = None, llm: Any = None, run_id: str | None = None,
                    max_steps: int = 12) -> dict[str, Any]:
    settings = settings or config.load()
    store = store or Store(settings.db_path)
    run_id = store.start_run(mode, "agent", run_id)
    rec = Recorder(store, run_id, mode)
    if mode == "unprotected_test":
        rec.info("UNPROTECTED TEST: agent connects directly to the tool server, no policy gateway.")
    try:
        async with mcp_session(settings.mcp_url(mode), run_id) as session:
            tools = (await session.list_tools()).tools
            llm = llm if llm is not None else make_llm(settings, run_id, mode)
            graph = build_graph(session, llm.bind_tools(openai_tool_schemas(tools)), rec, settings.otari_model)
            messages = [SystemMessage(SYSTEM_PROMPT), HumanMessage(task)]
            try:
                state = await graph.ainvoke({"messages": messages}, {"recursion_limit": max_steps * 2 + 1})
                final = state["messages"][-1].content
                if not isinstance(final, str):
                    final = json.dumps(final)
            except GraphRecursionError:
                final = f"Stopped after the {max_steps}-step limit."
                rec.info(final)
        # The model's free text can paraphrase lead data, which no redaction catches reliably,
        # so it is printed for the operator but never written to the evidence DB.
        result = _finish(store, settings, rec, "completed")
        return {**result, "final_answer": final}
    except Exception as exc:
        # In protected mode an unreachable gateway lands here: the run fails closed, nothing is forwarded.
        err = _describe(exc, settings.mcp_url(mode))
        rec.info(f"Run failed: {err}")
        return _finish(store, settings, rec, "failed", error=err)


async def run_enforcement_test(mode: str = "protected", *, settings: config.Settings | None = None,
                               store: Store | None = None, run_id: str | None = None) -> dict[str, Any]:
    """Deterministic proof, no LLM: one fixed violating call, then the compliant alternative."""
    settings = settings or config.load()
    store = store or Store(settings.db_path)
    run_id = store.start_run(mode, "enforcement_test", run_id)
    rec = Recorder(store, run_id, mode)
    rec.info("ENFORCEMENT TEST: predefined tool calls, not an autonomous agent decision.")
    try:
        async with mcp_session(settings.mcp_url(mode), run_id) as session:
            first = await call_tool(session, rec, "send_to_crm", dict(VIOLATING_CALL))
            second = await call_tool(session, rec, "send_to_crm", dict(COMPLIANT_CALL))
        answer = f"violating call: {first.outcome}; compliant call: {second.outcome}"
        return _finish(store, settings, rec, "completed", final_answer=answer)
    except Exception as exc:
        err = _describe(exc, settings.mcp_url(mode))
        rec.info(f"Run failed: {err}")
        return _finish(store, settings, rec, "failed", error=err)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Agent Monitor demo sales agent")
    p.add_argument("--mode", choices=config.MODES, default="protected")
    p.add_argument("--task", default=DEFAULT_TASK)
    p.add_argument("--run-id")
    p.add_argument("--max-steps", type=int, default=12)
    p.add_argument("--enforcement-test", action="store_true", help="fixed violating + compliant call, no LLM")
    a = p.parse_args(argv)
    if a.enforcement_test:
        result = asyncio.run(run_enforcement_test(a.mode, run_id=a.run_id))
    else:
        result = asyncio.run(run_agent(a.task, a.mode, run_id=a.run_id, max_steps=a.max_steps))
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())

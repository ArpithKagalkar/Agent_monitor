"""Streamlit dashboard backed entirely by recorded local execution evidence."""

import json
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import streamlit as st
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=False)
ENGINE = os.getenv("ENGINE_URL", "http://localhost:8000").rstrip("/")

st.set_page_config(
    page_title="Agent Monitor",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.markdown(
    """<style>
.block-container {padding-top:2rem; max-width:1500px;}
h1 {letter-spacing:-.04em;}
[data-testid="stMetric"] {background:#f1f5f8; border-radius:10px; padding:1rem;}
[data-testid="stMetricLabel"] {font-size:.9rem;}
</style>""",
    unsafe_allow_html=True,
)
st.html(f"<style>{(ROOT / 'dashboard/theme.css').read_text(encoding='utf-8')}</style>")


@st.cache_resource
def api_client():
    return httpx.Client(timeout=5)


def api(path, *, payload=None):
    client = api_client()
    response = (
        client.get(f"{ENGINE}{path}")
        if payload is None
        else client.post(f"{ENGINE}{path}", json=payload)
    )
    response.raise_for_status()
    return response.json()


def local_time(value):
    return (
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        .astimezone(ZoneInfo("Europe/Berlin"))
        .strftime("%d %b %H:%M:%S")
    )


def run_label(run):
    mode = "Protected" if run["mode"] == "protected" else "UNPROTECTED TEST"
    driver = {"otari": "Otari", "enforcement": "Enforcement", "manual": "Manual"}[
        run["driver"]
    ]
    profile = " · Capabilities" if run.get("capability_profile") else ""
    return f"{local_time(run['started_at'])} · {mode} · {driver}{profile} · {run['run_id'][:8]}"


try:
    health = api("/health")
except (httpx.HTTPError, ValueError):
    st.error(
        "The policy API is unavailable. Start the local application with python -m agent.launch."
    )
    st.stop()

with st.sidebar:
    st.subheader("Local services")
    try:
        services = api("/services")
    except httpx.HTTPError:
        services = {"engine": True, "gateway": False, "tools": False}
    for name, label in (
        ("engine", "Policy API"),
        ("gateway", "MCP gateway"),
        ("tools", "Synthetic tools"),
    ):
        st.markdown(f"**{label}** · {'Listening' if services[name] else 'Unavailable'}")
    st.divider()
    st.markdown("**Inference**")
    st.caption(
        "Otari configured" if health["otari_ready"] else "Otari credentials pending"
    )
    st.markdown("**Evidence**")
    st.caption("SQLite · persistent" if health["durable"] else "SQLite · temporary")
    st.caption("Times shown in Europe/Berlin.")
    st.divider()
    st.caption(
        "Synthetic records and local business effects only. Organization policy decisions illustrate technical controls; they are not legal determinations."
    )

from dashboard import audience, presentation

st.html(presentation.brand(all(services.values())))
view = st.radio(
    "View",
    ["Audience overview", "Run & inspect"],
    horizontal=True,
    key="view_mode",
    label_visibility="collapsed",
)

if view == "Audience overview":

    @st.fragment(run_every="5s")
    def audience_view():
        try:
            audience.render(api, run_label, local_time)
        except (httpx.HTTPError, ValueError):
            st.error(
                "Evidence could not be refreshed. Check that the local services are still running."
            )

    audience_view()
    st.stop()

st.html(
    presentation.section(
        "LIVE WORKSPACE",
        "Run the experiment. Inspect the evidence.",
        "Use the same scenario in both modes to compare what the policy gateway changes.",
    )
)
with st.container(border=True):
    st.subheader("Start a run")
    a, b, c = st.columns([1, 1, 1.4])
    with a:
        mode_label = st.radio("Execution mode", ["Protected", "UNPROTECTED TEST"])
    with b:
        driver_label = st.radio(
            "Run type", ["Enforcement test", "Otari agent"], key="run_type"
        )
    with c:
        scenario_label = st.selectbox(
            "Scenario",
            ["Unapproved CRM recipient", "Sensitive CRM fields", "Human approval hold"],
        )
    driver = "enforcement" if driver_label == "Enforcement test" else "otari"
    mode = "protected" if mode_label == "Protected" else "unprotected_test"
    scenario = {
        "Unapproved CRM recipient": "recipient",
        "Sensitive CRM fields": "sensitive",
        "Human approval hold": "approval",
    }[scenario_label]
    if mode == "unprotected_test":
        st.warning(
            "UNPROTECTED TEST: actions go directly to the synthetic tool server with enforcement off."
        )
    if driver == "enforcement":
        capability_profile = False
        st.caption(
            "Predefined tool calls through real local services. No model inference is performed."
        )
        prompt = None
    else:
        from agent.prompts import SCENARIO_PROMPTS

        capability_profile = st.checkbox(
            "Use Otari research, sandbox ranking and second-model review", value=True
        )
        if capability_profile:
            st.caption(
                "This workflow makes real, billable Otari fetch, sandbox, routing and model calls. Missing account capabilities are shown explicitly."
            )

        prompt = st.text_area(
            "Agent instruction",
            value=SCENARIO_PROMPTS[scenario],
            key=f"prompt_{scenario}",
        )
        if not health["otari_ready"]:
            st.info(
                "Add OTARI_BASE_URL, OTARI_AGENT_KEY, and OTARI_AGENT_MODEL to .env, then restart the application to enable live runs."
            )
    disabled = (
        not services["tools"]
        or (mode == "protected" and not services["gateway"])
        or (driver == "otari" and not health["otari_ready"])
    )
    if st.button("Start run", type="primary", disabled=disabled):
        try:
            created = api(
                "/demo/run",
                payload={
                    "mode": mode,
                    "driver": driver,
                    "scenario": scenario,
                    "prompt": prompt,
                    "capability_profile": capability_profile,
                },
            )
            st.session_state.active_run = created["run_id"]
            st.rerun()
        except httpx.HTTPError:
            st.error(
                "The run could not start. Check service availability and configuration."
            )


@st.fragment(run_every="1s")
def evidence_view():
    try:
        runs = api("/runs")
        if not runs:
            st.info(
                "No runs recorded yet. Start an enforcement test to inspect real policy and tool outcomes."
            )
            return
        by_id = {run["run_id"]: run for run in runs}
        if st.session_state.get("active_run") not in by_id:
            st.session_state.active_run = runs[0]["run_id"]
        evidence_tab, comparison_tab = st.tabs(["Run evidence", "Compare runs"])
        with evidence_tab:
            selected = st.selectbox(
                "Recorded run",
                list(by_id),
                format_func=lambda key: run_label(by_id[key]),
                key="active_run",
            )
            run = by_id[selected]
            timeline = api(f"/runs/{selected}/timeline")
            findings = api(f"/runs/{selected}/violations")
            usage = api(f"/runs/{selected}/usage")
            capabilities = api(f"/runs/{selected}/capabilities")
            coverage = (
                api(f"/runs/{selected}/coverage")
                if run.get("capability_profile")
                else None
            )
            writes = api(f"/runs/{selected}/tool-writes")
            st.markdown(
                f"**{run['status'].upper()}** · {run['driver'].capitalize()} · {run['scenario'].capitalize()} · `{selected}`"
            )
            if run["unknown_executions"] or run["pending_executions"]:
                st.warning(
                    f"Unresolved outcomes: {run['pending_executions']} pending, {run['unknown_executions']} unknown. Inspect tool state before retrying."
                )
            if run["status"] == "failed":
                reasons = {
                    "model_request_failed": "The Otari request failed. Check the account region, key, model, and budget.",
                    "agent_turn_limit": "The agent reached its model-turn limit.",
                    "tool_or_accounting_failed": "Tool execution or run accounting failed. Check local service availability.",
                }
                st.error(
                    reasons.get(
                        run.get("failure_reason"),
                        "The run ended with an uncertain execution outcome.",
                    )
                    + " Inspect the timeline and actual tool state before retrying."
                )
            metrics = st.columns(5)
            for column, title, count in zip(
                metrics,
                [
                    "Attempts",
                    "Blocked",
                    "Approval holds",
                    "Tool completions",
                    "Downstream writes",
                ],
                [
                    len(timeline),
                    run["blocked"],
                    run["approval_required"],
                    run["executed_count"],
                    len(writes),
                ],
            ):
                column.metric(title, count)
            st.subheader("Action timeline")
            st.caption(
                "Authorization and execution are recorded separately. Clean actions retain metadata only."
            )
            if timeline:
                rows = [
                    {
                        "Step": row["step"],
                        "Time": local_time(row["timestamp"]),
                        "Tool": row["tool_name"],
                        "Decision": row["decision"],
                        "Execution": row["execution_status"],
                        "Rule": row["rule_id"],
                    }
                    for row in timeline
                ]
                st.dataframe(rows, hide_index=True, width="stretch")
            else:
                st.caption("Waiting for the first attempted action.")
            left, right = st.columns(2)
            with left:
                st.subheader("Policy findings")
                if not findings:
                    st.caption(
                        "No findings recorded."
                        if run["mode"] == "protected"
                        else "Enforcement was off; no policy checks were performed."
                    )
                for finding in findings:
                    with st.expander(
                        f"Step {finding['step']} · {finding['rule_id']}", expanded=True
                    ):
                        st.write(finding["reason"])
                        st.caption(
                            "Relevant principles: "
                            + ", ".join(finding["regulatory_context"])
                            if finding["regulatory_context"]
                            else "Organization approval policy"
                        )
            with right:
                st.subheader("Confirmed local writes")
                if writes:
                    st.dataframe(
                        [
                            {
                                "Tool": row["tool_name"],
                                "Destination": row["destination"] or "Local state",
                                "Fields": ", ".join(row["fields"]),
                                "Execution ID": row["execution_id"],
                            }
                            for row in writes
                        ],
                        hide_index=True,
                        width="stretch",
                    )
                else:
                    st.caption("No downstream write records exist for this run.")
            st.subheader("Otari usage")
            if usage:
                st.dataframe(
                    [
                        {
                            "Request ID": row["request_id"],
                            "Role": row.get("role", "agent"),
                            "Model": row["model"],
                            "Resolved model": row.get("resolved_model"),
                            "Input tokens": row["prompt_tokens"],
                            "Output tokens": row["completion_tokens"],
                            "Reported cost (USD)": row["cost_usd"],
                        }
                        for row in usage
                    ],
                    hide_index=True,
                    width="stretch",
                )
                prices = [
                    Decimal(row["cost_usd"])
                    for row in usage
                    if row.get("cost_usd") is not None
                ]
                if prices:
                    qualification = (
                        "reported"
                        if len(prices) == len(usage)
                        else "partial; some calls are unpriced"
                    )
                    st.caption(
                        f"Cost: ${sum(prices):.6f} ({qualification}). Find this run UUID in Otari Activity/Sessions to verify its trace."
                    )
                else:
                    st.caption(
                        "Otari did not return cost information; no cost is estimated."
                    )
            else:
                st.caption(
                    "No model inference is performed in an enforcement test."
                    if run["driver"] != "otari"
                    else "No Otari usage has been returned for this run yet."
                )
            if run.get("capability_profile"):
                st.subheader("Otari capability evidence")
                st.caption(coverage["note"])
                st.write(
                    f"Technical evidence self-audit: **{coverage['technical_evidence_points']} / {coverage['technical_maximum']}**"
                )
                with st.expander("Verified model roles and rubric coverage"):
                    for role, cards in coverage["open_model_roles"].items():
                        for card in cards:
                            st.markdown(
                                f"**{role.capitalize()}**: [{card.rsplit('/', 1)[-1]}]({card})"
                            )
                    st.dataframe(
                        [
                            {"Otari feature": name, "Demonstrated": passed}
                            for name, passed in coverage["features"].items()
                        ],
                        hide_index=True,
                        width="stretch",
                    )
                if capabilities:
                    st.dataframe(
                        [
                            {
                                "Capability": item["capability"],
                                "Status": item["status"],
                                "Result": item["summary"],
                                "Request ID": item["request_id"],
                                "Resolved model": item["resolved_model"],
                                "Attempts": item["attempt_count"],
                                "Native fallback": item["fallback"],
                                "HTTP": item["http_status"],
                                "Error code": item["error_code"],
                                "Native cost (USD)": item["cost_usd"],
                            }
                            for item in capabilities
                        ],
                        hide_index=True,
                        width="stretch",
                    )
                    st.caption(
                        "Passed means this run demonstrated the capability. Unavailable or not configured earns no claimed coverage. Routing and review model costs are already included in model usage."
                    )
                    for item in capabilities:
                        if item.get("scores"):
                            with st.expander("Sandbox qualification ranking"):
                                st.dataframe(
                                    item["scores"], hide_index=True, width="stretch"
                                )
                        if item.get("review"):
                            with st.expander(
                                "Second-model review · advisory", expanded=True
                            ):
                                st.write(item["review"]["rationale_summary"])
                                st.caption(
                                    f"Conclusion: {item['review']['conclusion']} · Next step: {item['review']['recommended_next_step']}. The policy engine retains authority."
                                )
                else:
                    st.caption("Waiting for the first Otari capability result.")
            st.download_button(
                "Download redacted evidence",
                data=json.dumps(
                    {
                        "run": run,
                        "timeline": timeline,
                        "findings": findings,
                        "usage": usage,
                        "capabilities": capabilities,
                        "coverage": coverage,
                    },
                    indent=2,
                ),
                file_name=f"agent-monitor-{selected}.json",
                mime="application/json",
                key=f"download_{selected}",
            )
        with comparison_tab:
            comparison_scenario = st.selectbox(
                "Comparison scenario",
                ["recipient", "sensitive", "approval"],
                format_func=lambda value: {
                    "recipient": "Unapproved CRM recipient",
                    "sensitive": "Sensitive CRM fields",
                    "approval": "Human approval hold",
                }[value],
                key="comparison_scenario",
            )
            has_live_pair = all(
                any(
                    run["status"] == "completed"
                    and run["driver"] == "otari"
                    and run["scenario"] == comparison_scenario
                    and run["mode"] == mode
                    for run in runs
                )
                for mode in ("protected", "unprotected_test")
            )
            comparison_driver = st.radio(
                "Comparison run type",
                ["enforcement", "otari"],
                format_func=lambda value: (
                    "Otari agent" if value == "otari" else "Enforcement test"
                ),
                index=1 if has_live_pair else 0,
                horizontal=True,
                key="comparison_driver",
            )
            comparison_profile = (
                st.checkbox(
                    "Compare Otari capability workflows",
                    value=any(
                        item.get("capability_profile") and item["status"] == "completed"
                        for item in runs
                    ),
                    key="comparison_profile",
                )
                if comparison_driver == "otari"
                else False
            )
            comparable = [
                run
                for run in runs
                if run["status"] == "completed"
                and run["driver"] == comparison_driver
                and run["scenario"] == comparison_scenario
                and bool(run.get("capability_profile")) == comparison_profile
            ]
            protected = [run for run in comparable if run["mode"] == "protected"]
            baseline = [run for run in comparable if run["mode"] == "unprotected_test"]
            if not protected or not baseline:
                st.info(
                    "Complete both a protected run and an UNPROTECTED TEST to compare actual outcomes."
                )
            else:
                for column, title, candidates, key in zip(
                    st.columns(2),
                    ["UNPROTECTED TEST", "Protected"],
                    [baseline, protected],
                    ["baseline_choice", "protected_choice"],
                ):
                    with column:
                        st.subheader(title)
                        chosen = st.selectbox(
                            "Run",
                            [run["run_id"] for run in candidates],
                            format_func=lambda value: run_label(by_id[value]),
                            key=f"{key}_{comparison_driver}_{comparison_scenario}_{comparison_profile}",
                        )
                        item = by_id[chosen]
                        records = api(f"/runs/{chosen}/tool-writes")
                        st.metric("Confirmed downstream writes", len(records))
                        st.write(
                            f"Blocked: **{item['blocked']}** · Approval holds: **{item['approval_required']}**"
                        )
                        st.caption(
                            f"Scenario: {item['scenario']} · Driver: {item['driver']} · Status: {item['status']}"
                        )
                        if records:
                            st.dataframe(
                                [
                                    {
                                        "Destination": row["destination"]
                                        or "Local state",
                                        "Fields": ", ".join(row["fields"]),
                                    }
                                    for row in records
                                ],
                                hide_index=True,
                                width="stretch",
                            )
                st.caption(
                    "Both runs use the selected scenario and run type. Values come from their actual tool state."
                )
    except (httpx.HTTPError, ValueError):
        st.error(
            "Evidence could not be refreshed. Check that the local services are still running."
        )


evidence_view()

"""Agent Monitor dashboard.

Run:  streamlit run dashboard/app.py --server.port 8501

Every number here comes from a real run: counts from the evidence DB the agent
writes, "executed" from the tool server's own action log. Nothing is simulated.
Runs are started as subprocesses (python -m agent.run ...) so the UI never blocks.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import config  # noqa: E402
from agent.prompts import DEFAULT_TASK  # noqa: E402
from store.db import Store  # noqa: E402

LOG_DIR = ROOT / "store" / "_runtime" / "logs"

OUTCOME_STYLE = {
    "executed": ":green[EXECUTED]",
    "blocked": ":red[BLOCKED]",
    "pending_approval": ":orange[HELD FOR APPROVAL]",
    "error": ":gray[ERROR]",
}

st.set_page_config(page_title="Agent Monitor", page_icon=":material/shield:", layout="wide")


@st.cache_resource
def get_settings() -> config.Settings:
    return config.load()


@st.cache_resource
def get_store() -> Store:
    return Store(get_settings().db_path)


settings = get_settings()
store = get_store()

if "procs" not in st.session_state:
    st.session_state.procs = {}  # run_id -> Popen
if "selected_run" not in st.session_state:
    st.session_state.selected_run = None


# ---- helpers -------------------------------------------------------------

@st.cache_data(ttl=5, show_spinner=False)
def port_open(url: str) -> bool:
    u = urlparse(url)
    try:
        with socket.create_connection((u.hostname or "localhost", u.port or 80), timeout=0.3):
            return True
    except OSError:
        return False


def live_executed(run_id: str) -> int | None:
    """Ground truth straight from the tool server's log."""
    try:
        r = httpx.get(settings.upstream_url.rstrip("/") + "/state", params={"run_id": run_id}, timeout=1)
        r.raise_for_status()
        return int(r.json()["executed_count"])
    except (httpx.HTTPError, KeyError, ValueError):
        return None


def start_run(mode: str, enforcement: bool, task: str) -> None:
    run_id = str(uuid.uuid4())
    args = [sys.executable, "-m", "agent.run", "--mode", mode, "--run-id", run_id]
    args += ["--enforcement-test"] if enforcement else ["--task", task]
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = (LOG_DIR / f"{run_id}.log").open("w", encoding="utf-8")
    st.session_state.procs[run_id] = subprocess.Popen(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    st.session_state.selected_run = run_id


def run_label(r: dict) -> str:
    mode = "PROTECTED" if r["mode"] == "protected" else "UNPROTECTED TEST"
    kind = "enforcement test" if r["kind"] == "enforcement_test" else "agent"
    return f"{r['started_at'][11:19]} · {mode} · {kind} · {r['status']}"


def mode_badge(mode: str) -> None:
    if mode == "protected":
        st.badge("Protected: tool calls go through the policy gateway", icon=":material/shield:", color="green")
    else:
        st.badge("UNPROTECTED TEST: direct to tool server, no gateway", icon=":material/warning:", color="red")


# ---- sidebar: services ---------------------------------------------------

with st.sidebar:
    st.subheader("Services")
    for name, url in (("Policy engine", settings.engine_url), ("MCP gateway", settings.gateway_url),
                      ("Tool server", settings.upstream_url)):
        ok = port_open(url)
        st.markdown(f"{':green[:material/check_circle:]' if ok else ':red[:material/cancel:]'} {name}  \n"
                    f":gray[{url}]")
    st.divider()
    st.caption("Otari model: " + (settings.otari_model or ":red[OTARI_AGENT_MODEL not set]"))
    st.caption("All leads, CRMs and mailboxes are synthetic. Model calls and tool executions are real.")


# ---- header and controls -------------------------------------------------

st.title("Agent Monitor")
st.caption("Pre-execution enforcement for AI agent tool calls. A GDPR-aligned technical control demonstrator, "
           "not a legal compliance certification.")

with st.container(border=True):
    task = st.text_area("Agent task", DEFAULT_TASK, height=90, key="task")
    with st.container(horizontal=True):
        if st.button("Run baseline", key="btn_baseline", icon=":material/warning:", help="Agent talks directly to the tool server"):
            start_run("unprotected_test", False, task)
        if st.button("Run protected", key="btn_protected", icon=":material/shield:", type="primary",
                     help="Agent talks only to the gateway"):
            start_run("protected", False, task)
        if st.button("Enforcement test", key="btn_enforcement", icon=":material/science:",
                     help="Developer mode: one fixed violating call, then the compliant one, through the real "
                          "gateway. No LLM involved."):
            start_run("protected", True, task)


# ---- live run view -------------------------------------------------------

@st.fragment(run_every="1s")
def live_view() -> None:
    runs = store.list_runs()
    ids = [r["run_id"] for r in runs]
    by_id = {r["run_id"]: r for r in runs}
    pending = st.session_state.selected_run
    if pending and pending not in by_id:
        proc = st.session_state.procs.get(pending)
        if proc is not None and proc.poll() is None:
            st.info("Starting run…", icon=":material/hourglass_top:")
            return  # the agent process has not written its run row yet
        if proc is not None:
            st.error(f"Agent process exited with code {proc.returncode} before starting. "
                     f"See {LOG_DIR / (pending + '.log')}", title="Run did not start")
    if not runs:
        st.info("No runs yet. Start one above.", icon=":material/play_circle:")
        return
    if st.session_state.selected_run not in by_id:
        st.session_state.selected_run = ids[0]
    run_id = st.selectbox("Run", ids, key="selected_run", format_func=lambda i: run_label(by_id[i]))
    run = by_id[run_id]

    proc = st.session_state.procs.get(run_id)
    if proc is not None and proc.poll() is not None and proc.returncode != 0 and run["status"] == "running":
        st.error(f"Agent process exited with code {proc.returncode}. See {LOG_DIR / (run_id + '.log')}",
                 title="Run crashed")

    mode_badge(run["mode"])
    if run["kind"] == "enforcement_test":
        st.caption("Deterministic enforcement test: predefined tool calls, not an autonomous agent decision.")

    executed = live_executed(run_id)
    executed_note = "from tool server log"
    if executed is None:
        executed, executed_note = run["executed_count"], "tool server unreachable; last recorded value"

    with st.grid(5, gap="small"):
        st.metric("Attempted", run["attempted_count"], border=True, height="stretch",
                  help="Mutating tool calls the agent tried")
        st.metric("Blocked", run["block_count"], border=True, height="stretch")
        st.metric("Held for approval", run["approval_count"], border=True, height="stretch")
        st.metric("Executed", executed, border=True, height="stretch", help=executed_note)
        st.metric("Status", run["status"], border=True, height="stretch")

    left, right = st.columns([3, 2])
    with left, st.container(border=True):
        st.subheader("Timeline")
        events = store.timeline(run_id)
        if not events:
            st.caption("Waiting for the first step…")
        for e in events:
            s = e["summary"]
            if e["kind"] == "llm":
                calls = ", ".join(s.get("tool_calls") or []) or "final answer"
                st.markdown(f"`{e['step']:>2}` :blue[:material/neurology:] Otari model call → {calls}")
            elif e["kind"] == "tool":
                detail = " ".join(f"{k}=`{v}`" for k, v in s.items() if k not in {"reason", "execution_id"})
                line = f"`{e['step']:>2}` **{e['tool_name']}** {OUTCOME_STYLE.get(e['outcome'], e['outcome'])}"
                if e["rule_id"]:
                    line += f" · `{e['rule_id']}`"
                st.markdown(f"{line}  \n:gray[{detail}]")
                if s.get("reason"):
                    st.caption(s["reason"])
            else:
                st.markdown(f"`{e['step']:>2}` :gray[:material/info: {s.get('message', '')}]")
        if run["final_answer"]:
            with st.expander("Agent's final answer"):
                st.write(run["final_answer"])
        if run["error"]:
            st.error(run["error"], title="Run failed")

    with right:
        with st.container(border=True):
            st.subheader("Policy findings")
            v = store.violations(run_id)
            if v:
                df = pd.DataFrame(v)[["step", "tool_name", "decision", "rule_id", "severity", "reason",
                                      "regulatory_context"]]
                st.dataframe(df, hide_index=True, alt="Policy findings for this run",
                             column_config={"regulatory_context": st.column_config.ListColumn("Related principles")})
                st.caption("Related principles are a mapping of relevant legal principles, "
                           "not a binding legal determination.")
            else:
                st.caption("No blocked, held or flagged actions in this run.")

        with st.container(border=True):
            st.subheader("Otari usage")
            calls = store.llm_calls(run_id)
            if calls:
                df = pd.DataFrame(calls)
                tokens = int(df["prompt_tokens"].fillna(0).sum() + df["completion_tokens"].fillna(0).sum())
                priced = df["cost_usd"].dropna()
                cost = f"${priced.astype(float).sum():.6f}" if len(priced) else "not reported"
                with st.container(horizontal=True):
                    st.metric("Model calls", len(df), border=True)
                    st.metric("Tokens", tokens, border=True)
                    st.metric("Cost (Otari)", cost, border=True)
                st.dataframe(df[["step", "model", "prompt_tokens", "completion_tokens", "cost_usd",
                                 "otari_request_id"]], hide_index=True, alt="Otari model calls in this run")
            else:
                st.caption("No model calls (enforcement tests do not use the LLM).")

    compare(runs)


def compare(runs: list[dict]) -> None:
    latest = {}
    for r in runs:  # newest first
        if r["kind"] == "agent" and r["status"] != "running":
            latest.setdefault(r["mode"], r)
    if len(latest) < 2:
        return
    with st.container(border=True):
        st.subheader("Baseline vs protected (latest completed agent runs)")
        rows = [{"run": "UNPROTECTED TEST" if m == "unprotected_test" else "PROTECTED",
                 "started": latest[m]["started_at"], "attempted": latest[m]["attempted_count"],
                 "blocked": latest[m]["block_count"], "held": latest[m]["approval_count"],
                 "executed": latest[m]["executed_count"]} for m in ("unprotected_test", "protected")]
        st.dataframe(pd.DataFrame(rows), hide_index=True, alt="Comparison of baseline and protected runs")


live_view()

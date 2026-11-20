# Agent Monitor

A local enforcement demonstrator for an Otari-powered sales agent. Inspect
attempted actions, deterministic policy decisions, confirmed tool completions,
and actual synthetic CRM writes in a live Streamlit dashboard.

Developer One and Codex now own the full implementation. Developer Two can focus
on demo preparation, exploratory testing, and presentation work. The updated
[collaboration plan](Agent_Monitor_Git_Collaboration_Plan.md) supersedes the old
file-ownership and two-branch restrictions. No commit or push is automatic.

## Quick start (PowerShell)

Python 3.11+ is required; local verification uses Python 3.13. From this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe -m agent.launch
```

Open **http://localhost:8501**. The launcher starts the policy API, executing
synthetic MCP server, protected MCP gateway, and dashboard. Ctrl+C stops its own
children. Logs are in `work/logs/`. Occupied ports cause an explicit startup
failure. No credentials are needed for the **Enforcement test** path.

The default **Audience overview** explains the workflow and shows recorded
before/after outcomes, exported field names, Otari capability cards, and model
roles. Choose **Run & inspect** for live run controls, comparisons, request IDs,
and evidence downloads. Recorded results are explicitly labelled.

Select **UNPROTECTED TEST**, run the unapproved-recipient scenario, then repeat in
**Protected** mode. The baseline writes to the unapproved synthetic CRM. The
protected run blocks that attempt and performs a minimized safe retry. Compare
actual destinations, exported field names, and write counts in **Compare runs**.
The sensitive-field and approval-hold scenarios exercise the other two rules.

## Implemented and pending

- Shared contracts, deterministic R1/R2/R3 policies, pre-execution MCP enforcement,
  fail-closed behavior, and durable idempotency claims.
- Ten labeled synthetic Berlin leads; real local CRM, decision, and fake-outbox
  implementations. Tools never contact a business service or send real emails.
- SQLite evidence, redacted action timeline, baseline/protected run accounting,
  and reported Otari usage. Synthetic execution payloads use a separate database.
- A deterministic enforcement runner and a bounded LangGraph agent that routes
  live model requests exclusively through the configured Otari endpoint.
- A local dashboard, asynchronous run starts, run comparison, redacted evidence
  downloads, and a single-command launcher.
- An opt-in Otari capability workflow: native web fetch, public documentation
  MCP execution, sandbox qualification ranking, image fallback routing, a
  separate capped-key refusal, and distinct open-weight agent/reviewer roles.
  The dashboard shows actual capability status and a conservative rubric audit.

See [the Otari workflow and rubric map](docs/OTARI_RUBRIC.md) for the current
integration, account settings, model cards, and evidence requirements. Select
**Otari agent** and leave the research/ranking/review checkbox checked to use it.

**Capability verification (20 November 2026):** four enforcement cases and both
live capability runs passed, alongside 104 automated tests. Both live runs
demonstrated six Otari features and two open-weight model roles, supporting a
90/90 technical evidence self-audit subject to judging. Protected run
`99927324-1375-49cf-9bf5-a130b402b1a3` blocked the unsafe export and made one
approved, minimized CRM write. The dashboard and redacted report were checked
against the actual results. Full evidence: `outputs/otari-rubric-evidence.json`.

**Live verification (20 November 2026):** Otari inference succeeded using
`mistralai/mistral-small-latest`. A protected autonomous run completed: three
checked tool calls, one blocked CRM attempt, and exactly one successful CRM write
to `approved-crm` containing only `name` and `business_email`. The other tool
completion was a read. Four model calls returned a total cost of $0.000379.
Run UUID: `e4f15daa-9fc7-4558-86a6-3364734af21d`.

**Demo rehearsal (20 November 2026):** all six runs passed: unprotected and
protected recipient enforcement, sensitive-field blocking, an approval hold, and
fresh real Otari baseline/protected runs. The live pair reported $0.000674 across
seven model requests. Trace and usage visibility in Otari were confirmed by the
user for the earlier run above; this is a manual dashboard confirmation, not an
automated browser check. The separate usage API returns HTTP 403 because the
current key lacks `usage:read`; inline inference usage is recorded successfully.

Use [the presenter checklist](docs/PRESENTER.md) for operational checks,
rehearsed run IDs, and fallbacks. Comparisons require the same scenario and run
type. Repeat the rehearsal with services running:

```powershell
.\.venv\Scripts\python.exe -m agent.rehearse
# Also perform real, billable Otari baseline/protected runs:
.\.venv\Scripts\python.exe -m agent.rehearse --live
# Exercise native capabilities and the two open-weight model roles:
.\.venv\Scripts\python.exe -m agent.rehearse --live --capability-profile --output outputs/otari-rubric-evidence.json
```

Actual redacted results are saved to `outputs/demo-readiness.json`. The
enforcement demo also works without credentials. Mocked-model tests remain
separate from live-service verification. SDK callbacks, PDF, Slack, native
guardrails, per-user budgets, and cloud deployment are deferred. The hackathon
submission recording and audio must be made and explained by the participant;
AI-generated video, narration, and video feedback must not be used.

## Otari configuration

Set these in the ignored local `.env`:

```dotenv
OTARI_BASE_URL=https://api.otari.ai/api/v1
OTARI_AGENT_KEY=your-local-key
OTARI_AGENT_MODEL=an-exact-id-from-your-workspace
```

Use `https://eu.api.otari.ai/api/v1` for an EU key. The
[Otari API reference](https://github.com/mozilla-ai/otari/blob/main/docs/api-reference.md)
requires the `/api/v1` prefix for OpenAI-compatible hosted requests; the original
architecture document's `/v1` example predates that correction. Keys stay local.

```powershell
.\.venv\Scripts\python.exe -m engine.otari --list-models
# Set OTARI_AGENT_MODEL to an available ID, then:
.\.venv\Scripts\python.exe -m engine.otari --smoke
```

The smoke command makes one billable synthetic model request with a 128-token
output cap. Restart the launcher after configuration changes, then select
**Otari agent** in the dashboard. Agent runs allow at most six model turns by
default, with a 1,024-token output cap per turn and no automatic inference retry.
The agent accepts only caller-executed function tools for business actions.
The capability workflow separately executes one read-only public documentation
search through Otari's stored MCP server API. All protected business-tool
dispatch stays in the caller's gateway.

Every model request sends the run UUID in `Otari-Conversation-Id`, as described
in [Otari's trace docs](https://github.com/mozilla-ai/otari/blob/main/docs/traces.md).
Only returned usage and costs are stored. No price is invented when unavailable.
Find that UUID in Otari Activity/Sessions to confirm actual trace visibility.

## Services and commands

| Component | Local address | Standalone command after installing requirements |
| --- | --- | --- |
| Policy and run API | `localhost:8000` | `.\.venv\Scripts\python.exe -m uvicorn engine.main:app --host 127.0.0.1 --port 8000` |
| Synthetic tools | `localhost:8100/mcp` | `.\.venv\Scripts\python.exe -m mcp_tools.server` |
| Protected gateway | `localhost:8200/mcp` | `.\.venv\Scripts\python.exe -m gateway.server` |
| Dashboard | `localhost:8501` | `.\.venv\Scripts\python.exe -m streamlit run dashboard/app.py` |

The MCP servers explicitly expose Streamable HTTP at `/mcp` using the official
Python SDK. All five tool signatures come from `contracts/tools.py`:
`search_leads(query)`, `get_lead(lead_id)`,
`send_to_crm(lead_id, fields, destination)`,
`decide_on_lead(lead_id, decision, reason)`, and `send_email(to, subject, body)`.

Start a deterministic run from the CLI while services are running:

```powershell
.\.venv\Scripts\python.exe -m agent.run --mode protected --scenario recipient
.\.venv\Scripts\python.exe -m agent.run --mode unprotected_test --scenario recipient
```

Use `--scenario sensitive` or `--scenario approval` for the other rules, or
`--driver otari` for live inference once configured. The command returns the run
UUID; the dashboard polls real progress.

## Run API and tracking

Interactive API docs: http://localhost:8000/docs.

- `GET /health`, `GET /services`, `GET /runs`.
- `POST /runs/start`, `POST /check`, `GET /runs/{run_id}`.
- `GET /runs/{run_id}/violations`, `/timeline`, `/usage`, `/tool-writes`.
- `GET /otari/profile`, `GET /runs/{run_id}/capabilities`, `/coverage`.
- `POST /runs/{run_id}/capabilities`: validated, redacted native-feature evidence.
- `POST /runs/{run_id}/executions`: atomic `started` claim, then `succeeded` or
  `unknown` receipt. A repeated claim cannot dispatch a second business action.
- `POST /runs/{run_id}/observations`: direct baseline accounting only.
- `POST /runs/{run_id}/usage`, `/end`, `/fail`.
- `POST /demo/run`: launches an asynchronous run and returns its UUID.

A policy `Verdict` always has `executed=false`: authorization happens before
execution. The gateway result separately reports `executed=true`, `false`, or
`null` for a confirmed success, denial, or unknown outcome. Final receipt
acknowledgment is represented by `accounting_confirmed`. Neither an allow verdict
nor a timeout can establish an actual write count.

Direct MCP clients start a protected run first, then include metadata on each
call. The runner handles this automatically:

```python
result = await session.call_tool(
    "send_to_crm",
    {"lead_id": "L-001", "fields": ["name", "business_email"], "destination": "approved-crm"},
    meta={"agent_monitor": {"run_id": run_id, "event_id": str(uuid4()), "step": 1}},
)
outcome = result.structured_content
```

Use a new event UUID for a revised action. Reuse an ID only for retransmitting
that same action. The gateway forwards tracking as MCP metadata, never as
business-tool arguments. Approval remains held even when caller metadata claims
`human_approved=true`; no authenticated approval workflow is implemented.

## Evidence and operational limits

`DB_PATH` defaults to `store/agent_monitor.db`. It contains only redacted
findings, decisions, timelines, argument fingerprints, counters, and returned
usage; no prompts, leads, or tool arguments/results. `TOOL_STATE_PATH` defaults
to `work/tool_state.db` and separately holds the actual synthetic tool payloads.
These must remain different files. Both databases and `.env` are ignored by Git.

`executed_count` counts successful tool completions, including reads. The UI
shows mutating **Downstream writes** separately from actual tool state. Baseline
runs have zero policy checks, while still recording their actual executions.

SQLite preserves claims across restarts and atomically prevents duplicate event
execution. A process interrupted mid-action can leave pending/unknown outcomes;
inspect actual tool state before reconciliation. Restart does not resume a run.
Lost final receipts are not automatically retried or treated as proof of zero
side effects. Use one local policy process and the launcher for this prototype.
Services bind to loopback and do not provide public caller authentication.

Policy references illustrate organization controls and related principles;
this is not a GDPR/EU AI Act compliance certification. R2 uses explicit synthetic
field labels and the approved CRM field set, not a general semantic DLP system.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests include real HTTP/MCP discovery, block-before-dispatch, minimized retries,
policy outages, approval holds, concurrent replay, durable claims, baseline
accounting, evidence redaction, synthetic tools, and the LangGraph loop with
explicitly mocked model responses. Tests use temporary databases under `work/`.

See [docs/DEMO.md](docs/DEMO.md) for the demonstration sequence and failure
semantics, and [the architecture spec](Agent_Monitor_Architecture_and_Build_Spec.md)
for the original product requirements and current ownership amendment.

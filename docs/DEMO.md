# Agent Monitor: demo runbook (Dev 2 components)

Everything below runs locally. All leads, CRMs and mailboxes are synthetic;
model calls (Otari) and tool executions are real.

## Setup

```bash
python -m pip install -r requirements-demo.txt
```

Create `.env` in the repo root (never commit it). Variable names follow `.env.example`:

```dotenv
OTARI_BASE_URL=https://api.otari.ai/api/v1   # note the /api segment (Otari API reference)
OTARI_AGENT_KEY=...
OTARI_AGENT_MODEL=...                         # provider:model id from your Otari workspace (GET /api/v1/models)
MCP_UPSTREAM_URL=http://localhost:8100
MCP_GATEWAY_URL=http://localhost:8200
DB_PATH=store/agent_monitor.db
```

## Start services (one terminal each)

```bash
python -m mcp_tools.server                        # synthetic tools      :8100
# Dev 1: policy engine :8000 and MCP gateway :8200
streamlit run dashboard/app.py --server.port 8501 # dashboard            :8501
```

Smoke check the tool server: `curl http://localhost:8100/health` and `curl http://localhost:8100/state`.
To start a demo from a clean slate: `curl -X POST http://localhost:8100/state/reset` (old log is archived).

## The 90-second flow

1. **Run baseline** (red UNPROTECTED TEST badge). The agent talks straight to :8100; the full-profile
   export to `partner-crm-us` executes. "Executed" is read from the tool server's own log.
2. **Run protected** (green badge). Same agent, same task, MCP traffic only to the gateway :8200.
   The export is shown as BLOCKED with its rule and reason, and the executed count does not move.
   If the model retries with `approved-crm` and `name`/`business_email`, that one call executes.
3. **Enforcement test** (developer mode). No LLM: one fixed violating `send_to_crm` call, then the
   compliant one, both through the real gateway. Expected: blocked 1, executed 1.
4. The "Baseline vs protected" table compares the latest completed agent run of each mode.
5. Show the matching requests in Otari Activity. Each model call carries
   `metadata.run_id`, and the dashboard lists each call's `Otari-Request-ID` and reported `cost_usd`.

The model is not forced to attempt the violation. If it doesn't, use the enforcement test; it is
labelled as a predefined call, not an autonomous decision.

## CLI equivalents

```bash
python -m agent.run --mode unprotected_test
python -m agent.run --mode protected
python -m agent.run --mode protected --enforcement-test
```

Each prints the final run record (counts, status) as JSON.

## Tests

```bash
python -m pytest tests/test_tools.py tests/test_agent.py -q
```

These use a scripted fake model (no Otari spend) and an in-test stub gateway that stands in for
Dev 1's gateway. They prove: blocked calls are never forwarded, a compliant retry executes exactly
once, the evidence DB holds no raw lead values, and an unreachable gateway fails closed.

## Known environment issue

On Windows with Smart App Control, `ormsgpack` 1.13.x's DLL is blocked, which breaks
`import langgraph`. `requirements-demo.txt` pins `ormsgpack<1.13`.

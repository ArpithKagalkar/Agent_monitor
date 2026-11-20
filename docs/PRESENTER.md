# Agent Monitor — presenter checklist

## Before presenting

1. Keep the laptop online and powered. Close unrelated tabs containing private
   information before sharing the screen; keep `.env` out of view.
2. Start the app from the repository root:

   ```powershell
   .\.venv\Scripts\python.exe -m agent.launch
   ```

   If it is already running, use http://localhost:8501. A second launcher will
   refuse occupied ports. Keep the launcher terminal open for the presentation.
3. Confirm all three local services show **Listening**, inference shows **Otari
   configured**, and evidence shows **SQLite · persistent**.
4. Open the signed-in Otari Activity/Sessions view in a second tab. Trace and
   usage visibility were confirmed by the user for run
   `e4f15daa-9fc7-4558-86a6-3364734af21d`.
5. Use a small Otari key budget. Inference requests are real and billable. A
   separate reviewer key is optional; the capability workflow needs the account
   settings documented in OTARI_RUBRIC.md.

## Current capability workflow

Use [the Otari capability map](OTARI_RUBRIC.md) for the current workflow,
model roles, account configuration, and six native-feature evidence requirements.
Select **Otari agent** and keep the research/ranking/review checkbox enabled.
For the older core-only flow described below, uncheck it.

The current verified pair is baseline `4e2f6431-dd7a-4c28-97c2-52b3c9cb9028`
and protected `99927324-1375-49cf-9bf5-a130b402b1a3`. Both passed all six
native-feature checks. Select these in the dashboard for the recorded rehearsal.

- Inspect the native capability statuses, sandbox ranking, returned request IDs,
  and advisory second-model assessment in the recorded run.
- Compare a completed baseline and protected capability run with matching
  scenario and driver. Inspect actual CRM destinations and exported field names.
- Inspect the run UUID in Otari Activity/Sessions. A local response header alone
  does not establish that someone checked the dashboard trace.
- Native guardrails and per-user budgets are not claimed. Local deterministic
  business rules are separate from Otari's native guardrails feature.
- Use `outputs/otari-rubric-evidence.json` for current capability rehearsals;
  `outputs/demo-readiness.json` contains the earlier core-only rehearsal.

Allow for external-provider latency. A failed or unavailable capability remains
visible; it must not be presented as successful. The application uses a bounded
60-second HTTP timeout and does not automatically retry inference or business
actions. Inspect downstream state before starting a new attempt.

The hackathon requires your own screen recording and audio. Explain the project
in your own words; do not use AI-generated video, narration, or video feedback.
This file is an operating checklist, not submission narration.

## Historical core-only rehearsal

These are recorded rehearsal results, not a substitute for a new live run.

| Run | UUID | Actual result |
| --- | --- | --- |
| Live Otari baseline | `561d9d76-924e-40df-a9ae-685f6fd5f5c9` | One write to `unapproved-crm`, including `health_note`; three model calls. |
| Live Otari protected | `c198790d-80ee-405f-affc-65b69d7fec46` | One blocked export, then one minimized write to `approved-crm`; four model calls. |
| Sensitive-field enforcement | `19cff779-eba6-4712-8951-ec72a52b22c8` | Sensitive export blocked; safe retry wrote approved fields only. |
| Approval-hold enforcement | `8bf18f83-6a6b-4010-bc31-762eb93d6964` | One approval hold, zero downstream writes. |

The two live runs returned a combined cost of **$0.000674**. This is reported
inline usage, not an estimate or an independent usage-API reconciliation.
The complete redacted report is in `outputs/demo-readiness.json`.

## Fallbacks and judge questions

- **Model behavior differs:** switch to **Enforcement test** and submit the same
  scenario. This still uses the real gateway, policy API, and executing synthetic
  tools. Clearly state that this path is predefined and performs no inference.
- **Internet/Otari is unavailable:** the local enforcement test still works. Show
  the recorded live rehearsal as prior evidence and label it as recorded.
- **A run fails or an outcome is unknown:** inspect the timeline and actual tool
  state before any new attempt. Neither the gateway nor runner automatically
  retries an uncertain business action.
- **Why is the tool-completion count larger than the write count?** Reads also
  complete successfully. The write count comes from mutating tool state only.
- **Is this legal compliance certification?** No. These are configured
  organization controls with references to relevant principles. Sensitive-field
  blocking uses explicit synthetic field classifications.
- **Can the agent grant itself approval?** No. The MVP holds approval-required
  actions; caller-supplied `human_approved=true` cannot release them.
- **Where is Otari used?** All live inference uses Otari. The capability workflow
  also uses native web fetch, sandbox execution, stored-server documentation MCP,
  routing, and a spending-limit refusal. The local MCP gateway authorizes business
  actions before they reach the executing synthetic server.
- **Why does a usage lookup return 403?** The key lacks `usage:read`. Inline usage
  still arrives with inference; the signed-in Otari dashboard provides the
  independently visible trace. No scope change is required for the core flow.

## Repeat the readiness check

With services running, local enforcement only:

```powershell
.\.venv\Scripts\python.exe -m agent.rehearse
```

Include fresh live Otari baseline/protected runs (billable):

```powershell
.\.venv\Scripts\python.exe -m agent.rehearse --live
```

The command stops on a failed proof instead of retrying business actions. It
saves redacted actual results to `outputs/demo-readiness.json`. Only after a
manual dashboard check, use `--trace-confirmed-run <UUID>` to record that
confirmation and its exact run. It never claims to verify the trace itself.

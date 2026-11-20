# Otari capability workflow

Agent Monitor researches and ranks synthetic startup prospects, then constrains
what an autonomous sales agent can export. Otari supplies research, execution,
routing, inference, and spending controls. A separate deterministic gateway
checks business actions before dispatch. All CRM effects remain local fixtures.

## Verified rehearsal: 20 November 2026

All six rehearsal cases passed: four deterministic enforcement cases and two
live Otari capability workflows. The full suite passed **104 tests**. A Streamlit
AppTest against the running application also verified the live evidence view,
fallback and budget metadata, and matching capability-run comparison.

| Live run | UUID | Confirmed result |
| --- | --- | --- |
| UNPROTECTED TEST | `4e2f6431-dd7a-4c28-97c2-52b3c9cb9028` | One unapproved export including `health_note`; reviewer flagged it |
| Protected | `99927324-1375-49cf-9bf5-a130b402b1a3` | One blocked attempt, then one approved export containing only `name` and `business_email`; reviewer confirmed policy followed |

Both runs demonstrated all six claimed native features and returned matching
resolved-model evidence for the two open-weight roles. Each has a **90/90
technical self-audit**, subject to judging. Native guardrails and per-user
budgets are not counted.

The pair reported **$0.002426 in model costs** and **$0.040000 in priced native
fetch/sandbox calls**. The MCP endpoint did not return a price, so these figures
are not a complete reconciled charge total. Earlier diagnostic calls are also
outside this pair's totals.

Actual results: [redacted rehearsal report](../outputs/otari-rubric-evidence.json)
and [dashboard verification](../outputs/ui-readiness.json). Failed diagnostic
runs remain recorded; they are not substituted for these successful runs.

## What happens during a capability run

1. Otari fetches Mozilla's public about page as campaign context. This is
   background, not a claim about any fictitious prospect.
2. Otari executes `search_cloudflare_documentation` on the stored
   `agent-monitor-docs` MCP server. Its public technical reference is provided
   to the agent as untrusted campaign background.
3. Otari's Python sandbox scores ten synthetic prospects:
   `0.7 * company_fit + 0.3 * outreach_readiness`. The application validates
   the complete returned ranking. L-003 scores 93.5 and ranks first.
4. A synthetic image brief is sent to `agent-monitor-vision`. Its text-only
   Llama primary rejects the image request, so Otari falls back to Gemini's
   vision route. The read must match L-003, 95, and 90, and Otari must return
   `Otari-Fallback: true`. The confirmed brief becomes agent context.
5. A separate key capped at $0.01 receives a request whose estimated output
   allowance exceeds that cap. Only `403 budget_exceeded` proves enforcement.
   The working agent key is not exhausted. A $0 setting previously admitted a
   request, so the positive cap is intentional.
6. Mistral selects local business-tool calls. Protected mode sends them through
   policy checks; an unapproved export is blocked before execution, then a
   minimized alternative can execute. UNPROTECTED TEST bypasses those checks
   exclusively against local synthetic tools.
7. GPT-OSS-120B reviews redacted findings and confirmed write metadata. Its
   structured assessment is advisory. Validation rejects unsupported control
   claims; it cannot authorize an action or release an approval hold.

## Models and native features

| Role | Requested model | Model card |
| --- | --- | --- |
| Sales agent | `mistral:mistral-small-2603` | [Mistral Small 4, Apache 2.0](https://huggingface.co/mistralai/Mistral-Small-4-119B-2603) |
| Image fallback | `google/gemini-2.5-flash` | Used for image reading; no open-weight credit claimed |
| Outcome reviewer | `openai/gpt-oss-120b` | [GPT-OSS-120B, Apache 2.0](https://huggingface.co/openai/gpt-oss-120b) |

The audit requires actual request IDs and matching resolved-model headers.
The failing Llama primary and Gemini backup are not counted as open-weight roles.
GPT-OSS-20B was unsuitable for the fallback trigger because its provider
answered that it could not see the image instead of returning an error; it
also timed out repeatedly on the full review prompt. There is no client-side
provider fallback or fabricated successful response.
Mistral 3.2's route also showed intermittent timeouts during agent loops, so
the sales agent uses the explicitly pinned Small 4 route and the image backup
uses Gemini 2.5 Flash. An unversioned
catalog alias whose resolved header does not match the model card earns no
open-weight evidence credit.

| Rubric capability | Evidence required |
| --- | --- |
| Model routing and fallbacks | Correct image read plus native fallback header |
| MCP integrations | Native Otari stored-server discovery and confirmed read-only execution |
| Web search or web fetch | Native `/fetch`, returned text hash and character count |
| Code execution | Native sandbox, zero return code, complete validated ranking |
| Budgets and spending limits | Separate capped key refused with `403 budget_exceeded` |
| Multiple models serving distinct roles | Verified Mistral agent and valid GPT-OSS reviewer |
| Native guardrails | Not claimed: deployment reported no guardrails service |
| Per-user budgets | Not implemented or claimed |

Six demonstrated features fill the 60-point Otari category. Two distinct
open-weight model roles can support the 30-point model category. These are
evidence-based claims for judges to assess, not awarded points. Project and
video quality account for the remaining 10 points.

## Account configuration

- Enable workspace web access.
- Save fallback policy `agent-monitor-vision`: main
  `meta/llama-3.3-70b-instruct`, backup `google/gemini-2.5-flash`.
- Save a separate inference key with a $0.01 cap as
  `OTARI_BUDGET_PROOF_KEY` in the ignored `.env`.
- Register `agent-monitor-docs` at
  `https://docs.mcp.cloudflare.com/mcp`, no token, exposing only
  `search_cloudflare_documentation`. The working key needs `mcp:read` and
  `mcp:execute`, alongside inference, fetch, and code execution scopes.
- Reviewer credentials may reuse the working agent key. Defaults are in
  `.env.example`; blank reviewer model selects GPT-OSS-120B.

The MCP request uses the stored server's discovered revision and sends one
execution attempt. Public research is the only business-independent MCP call
Otari executes. CRM, email, and decision tools retain the local gateway boundary.

## Verify and inspect

With the application running:

```powershell
.\.venv\Scripts\python.exe -m agent.rehearse --live --capability-profile --output outputs/otari-rubric-evidence.json
```

This runs four deterministic enforcement cases and a real baseline/protected
Otari pair. Model requests and native fetch/sandbox calls are billable. The
report separates returned model costs from returned native-tool costs; unknown
prices remain unknown. No automatic inference or business-action retries occur.

Select **Otari agent**, leave **Use Otari research, sandbox ranking and
second-model review** checked, and inspect **Otari capability evidence**.
**Compare runs** must use the same scenario, driver, and capability setting.
The download includes redacted evidence and the technical self-audit. Raw page
text, MCP output, image bytes, model responses, and secrets are not saved in the
evidence database. The advisory review summary is retained.

Find the run UUID in Otari Activity/Sessions to inspect its native requests.
The user confirmed trace visibility for an earlier run; new run IDs require
their own manual dashboard check. The local report does not certify that the
Otari dashboard was inspected.

The hackathon requires the participant's own screen recording and audio.
Make and explain the demonstration yourself; do not use AI-generated video,
narration, or video feedback.

## Technical references

- [Otari routing](https://github.com/mozilla-ai/otari/blob/main/docs/routing.md)
- [Otari MCP and caller-orchestrated execution](https://github.com/mozilla-ai/otari/blob/main/docs/mcp.md)
- [Otari built-in tools](https://github.com/mozilla-ai/otari/blob/main/docs/tools.md)
- [Otari budget enforcement](https://github.com/mozilla-ai/otari/blob/main/docs/budget-enforcement.md)
- [Cloudflare public documentation MCP endpoint](https://developers.cloudflare.com/agents/model-context-protocol/cloudflare/servers-for-cloudflare/)

The implementation was also checked against the hosted deployment's OpenAPI
schema; it may differ from newer repository documentation.

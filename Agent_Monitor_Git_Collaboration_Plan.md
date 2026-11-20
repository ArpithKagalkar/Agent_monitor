# Agent Monitor — Current collaboration plan

## Current agreement (20 November 2026)

The user has chosen one implementation owner: Developer One and Codex build the
full core application. Developer Two can handle demo preparation, exploratory
testing, and presentation work. This supersedes the archived plan below,
including its file ownership, contract signoff, and two-feature-branch rules.

Developer One owns all components, schemas, tests, dependencies, and docs. Shared
schemas remain the source of truth; update their producers and consumers
together. Do not commit, push, or open PRs until the user explicitly asks.

All live inference still uses Otari. Protected calls still pass through the MCP
gateway before execution. Use synthetic data, redacted evidence, actual execution
outcomes, and returned usage only. PDF, Slack, extra models, and cloud deployment
remain deferred until the P0 demo is verified.

## Archived two-developer plan (superseded)

**Purpose:** Develop the Mozilla Otari hackathon prototype in parallel with minimal merge conflicts and predictable integration.

## Rules everyone, including coding AIs, must follow

1. **One repo, two feature branches:** `feature/policy-gateway` (Dev 1) and `feature/agent-demo` (Dev 2). Keep `main` stable. Do not push unfinished work to `main`.
2. **Exclusive file ownership:** Do not edit files assigned to the other developer, even for a seemingly minor fix. Tell the owner what needs changing instead.
3. **Contract first:** Dev 1 commits the repository skeleton, shared Pydantic schemas, API signatures, and configuration example to `main` **before both feature branches are created**.
4. **Freeze schemas during parallel work:** Use `contracts/schemas.py` unchanged until the first integration. If a contract must change, agree in chat first, then Dev 1 makes and shares the change.
5. **No competing edits to dependency/config files:** Common Python dependencies belong to Dev 1. Dev 2 maintains its own agent/demo dependencies. Local `.env` files are never committed.
6. **Small commits; no force-push to `main`; no auto-merging untested PRs.** Avoid giving both coding agents open-ended permission to refactor the whole repository.
7. **No bypass in protected mode:** The agent routes protected actions to the gateway, not directly to the synthetic tool server.

## File ownership

| Path | Owner | Notes |
|---|---|---|
| `contracts/**` | Dev 1 | Shared `Event` / `Verdict` / API models; freeze early |
| `engine/**` | Dev 1 | Otari integration, FastAPI policy, rules |
| `gateway/**` | Dev 1 | Authoritative pre-execution MCP enforcement |
| `sdk/**` | Dev 1 | Optional observer, not a security boundary |
| `.env.example`, `requirements-common.txt`, `requirements-policy.txt`, root `README.md` | Dev 1 | Shared config and documentation |
| `tests/test_policy.py`, `tests/test_gateway.py` | Dev 1 | Policy and gateway tests |
| `agent/**` | Dev 2 | LangGraph agent and runner |
| `mcp_tools/**` | Dev 2 | Synthetic executing tools and state |
| `fixtures/**` | Dev 2 | Synthetic leads and demonstration inputs |
| `store/**` | Dev 2 | SQLite evidence storage |
| `dashboard/**` | Dev 2 | UI and associated package files |
| `requirements-demo.txt`, `docs/DEMO.md` | Dev 2 | Agent/UI dependencies and demonstration steps |
| `tests/test_agent.py`, `tests/test_tools.py` | Dev 2 | Agent and tools tests |
| `tests/test_end_to_end.py` | Integration owner after both PRs | Do not both edit it in parallel |

**Dependency convention:** `requirements-demo.txt` and `requirements-policy.txt` can each include `-r requirements-common.txt`. Never both edit a single root `requirements.txt`. If the existing repo already has a root dependency file, assign it to Dev 1 and let Dev 2 use its separate file.

## Initial bootstrap (Dev 1, before parallel development)

1. Create `main` with the structure and `contracts/schemas.py` already agreed in `Agent_Monitor_Architecture_and_Build_Spec.md`.
2. Add `.gitignore` covering `.env`, `.venv/`, `__pycache__/`, `*.db`, `.next/`, `node_modules/`, and local generated reports.
3. Commit and push the foundation:

```bash
git switch main
git add .
git commit -m "chore: freeze initial contracts and repo structure"
git push origin main
```

4. Notify Dev 2 that the base commit is ready. **Both branches must start from this commit**, not from separately generated scaffolds.

## Create branches

Dev 1:

```bash
git switch main
git pull --ff-only origin main
git switch -c feature/policy-gateway
git push -u origin feature/policy-gateway
```

Dev 2 (after cloning the repo):

```bash
git switch main
git pull --ff-only origin main
git switch -c feature/agent-demo
git push -u origin feature/agent-demo
```

Each developer then changes **only their owned paths**. For regular commits:

```bash
git status
git add <only-your-owned-paths>
git commit -m "feat: describe your component"
git push
```

Avoid `git add .` once work begins: staged changes should be reviewed so generated files and local secrets are not accidentally added.

## The shared contract: do not improvise the interface

### Policy request

`POST http://localhost:8000/check`

```json
{
  "event_id": "uuid",
  "run_id": "uuid",
  "step": 3,
  "source": "mcp_gateway",
  "event_type": "tool_call",
  "tool_name": "send_to_crm",
  "arguments": {"lead_id": "L-003", "fields": ["name", "business_email", "health_note"], "destination": "unapproved-crm"},
  "context": {"purpose": "qualified_lead_outreach", "human_approved": false},
  "timestamp": "2026-11-20T12:00:00Z"
}
```

### Policy response

```json
{
  "event_id": "uuid",
  "decision": "block",
  "severity": "high",
  "rule_id": "DESTINATION_NOT_APPROVED",
  "policy_reference": "company_policy:crm_recipient_allowlist",
  "regulatory_context": ["GDPR Art. 5(1)(b)", "GDPR Art. 5(1)(c)"],
  "reason": "Recipient is not approved for lead export",
  "safe_alternative": {"tool_name": "send_to_crm", "destination": "approved-crm", "fields": ["name", "business_email"]},
  "executed": false
}
```

Allowed verdicts: `allow`, `flag`, `block`, `require_approval`. `BLOCK` and `REQUIRE_APPROVAL` must not reach a protected mutating tool. Dev 2 must consume the actual `Verdict` contract rather than inventing a separate one.

### Tool interfaces (Dev 2 owns implementation; Dev 1 consumes)

- `search_leads(query)`
- `get_lead(lead_id)`
- `send_to_crm(lead_id, fields, destination)`
- `decide_on_lead(lead_id, decision, reason)`
- `send_email(to, subject, body)`

Use the same agreed MCP transport on both sides. The proxy and tool server should be smoke-tested with `list_tools` and `call_tool` before integrating the agent.

## Parallel development without waiting

- **Dev 1** uses a stub MCP upstream tool while implementing the gateway; stubs produce deterministic action records.
- **Dev 2** uses the agreed JSON responses and direct test tool-server calls while building the agent and UI; protected production-like mode will point to gateway `:8200` when ready.
- The only shared dependency for parallel work is the **frozen contracts and tool signatures**; neither developer needs to import unfinished modules owned by the other.
- If one side needs a new field, send an explicit contract-change request (field, purpose, default, compatibility) before editing code.

## Integration and pull requests

1. Dev 1 opens a PR: `feature/policy-gateway` → `main`. Run policy + gateway tests. Review and merge.
2. Dev 2 updates its branch to current `main` (see below), runs agent + tool tests, then opens a PR: `feature/agent-demo` → `main`. Review and merge.
3. One designated integration owner adds or fixes the end-to-end orchestration, **after** both PRs are merged. Integration work is committed in a short new `feature/integration` branch.
4. Test: actual Otari model call; gateway checks before tool invocation; blocked CRM transfer makes zero downstream writes; allowed transfer makes one; dashboard reflects true outcomes.

For Dev 2, after Dev 1's PR is merged, on the solo-owned feature branch:

```bash
git fetch origin
git switch feature/agent-demo
git merge origin/main
# Run tests; resolve any unexpected conflicts with the relevant file owner.
git push
```

Merging `origin/main` is straightforward when files are disjoint; it also avoids rewriting published branch history. Conflicts are still possible if both branches changed the same lines, which is why ownership matters more than branch naming.

**GitHub settings (if time permits):** protect `main` with PR-only merges, prevent force pushes, and require at least a smoke-test check. Do not spend hackathon-critical time on complex CI setup.

## Prompts for both coding agents

**Instruction for both agents (paste verbatim):**

> We are two developers working in one GitHub repository on separate feature branches. You must follow `Agent_Monitor_Git_Collaboration_Plan.md` and `Agent_Monitor_Architecture_and_Build_Spec.md`. Only edit files listed for my developer role. Do not alter `contracts/schemas.py`, root config, other developers' components, or shared tool signatures without explicit agreement. Do not refactor unrelated files. Keep changes in small commits on my feature branch, run your own tests, and report every cross-team dependency rather than silently changing another owner's code. All LLM inference must use Otari; protected tool execution must pass through the gateway before the downstream tool. Use synthetic data only.

**Dev 1 assignment:** policy/engine/gateway/Otari + contracts and gateway tests.  
**Dev 2 assignment:** agent/MCP tools/fixtures/store/dashboard + agent/tool tests.

## Demo priorities

**P0:** working Otari calls → working synthetic MCP tool → authoritative gateway block → safe retry → real run dashboard.  
**P1:** a second Otari model, scoped keys/budgets, polished trace presentation.  
**P2:** SDK observer, PDF, Slack, external deployment.

The goal is **low merge conflict risk and fast integration**, not a guarantee of zero conflicts. Separate file ownership reduces textual conflicts; frozen contracts and smoke tests prevent functional integration failures.

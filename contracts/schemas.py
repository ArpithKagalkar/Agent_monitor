"""Initial shared API contracts from the architecture specification.

Policy verdicts describe authorization before execution. Only the gateway can
subsequently report whether an authorized downstream action actually executed.
"""

from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID
from decimal import Decimal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictInt,
)


Decision = Literal["allow", "flag", "block", "require_approval"]
Severity = Literal["info", "low", "medium", "high", "critical"]
RunMode = Literal["protected", "unprotected_test"]
RunStatus = Literal["running", "completed", "failed"]
Driver = Literal["manual", "enforcement", "otari"]
Scenario = Literal["recipient", "sensitive", "approval"]
FailureReason = Literal[
    "model_request_failed", "tool_or_accounting_failed", "agent_turn_limit"
]
ToolName = Literal[
    "search_leads", "get_lead", "send_to_crm", "decide_on_lead", "send_email"
]
NonEmptyString = Annotated[str, Field(min_length=1, pattern=r"\S")]
Counter = Annotated[StrictInt, Field(ge=0)]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class APIModel(BaseModel):
    # Reject misspelled or unagreed fields instead of silently dropping them.
    model_config = ConfigDict(extra="forbid")


class EventContext(APIModel):
    purpose: NonEmptyString
    data_subject_type: NonEmptyString | None = None
    # Caller metadata is not an authenticated human approval. R3 must still hold.
    human_approved: StrictBool = False


class Event(APIModel):
    """POST /check request. Reuse event_id when observing the same action."""

    event_id: UUID
    run_id: UUID
    step: Counter
    source: Literal["mcp_gateway", "sdk_observer"]
    event_type: Literal["tool_call"] = "tool_call"
    tool_name: ToolName
    arguments: dict[str, JsonValue]
    context: EventContext
    timestamp: AwareDatetime = Field(default_factory=utc_now)


class Verdict(APIModel):
    """POST /check response; authorization alone never proves execution."""

    event_id: UUID
    decision: Decision
    severity: Severity
    rule_id: NonEmptyString
    policy_reference: NonEmptyString
    regulatory_context: list[str] = Field(default_factory=list)
    reason: NonEmptyString
    safe_alternative: dict[str, JsonValue] | None = None
    executed: Literal[False] = False


class StartRunRequest(APIModel):
    mode: RunMode = "protected"


class StartRunResponse(APIModel):
    run_id: UUID
    mode: RunMode


class RunSummary(APIModel):
    """GET /runs/{run_id} and POST /runs/{run_id}/end response."""

    run_id: UUID
    mode: RunMode
    status: RunStatus
    driver: Driver = "manual"
    scenario: Scenario = "recipient"
    capability_profile: StrictBool = False
    failure_reason: FailureReason | None = None
    started_at: AwareDatetime
    ended_at: AwareDatetime | None = None
    total_checked: Counter = 0
    allowed: Counter = 0
    flagged: Counter = 0
    blocked: Counter = 0
    approval_required: Counter = 0
    executed_count: Counter = 0
    pending_executions: Counter = 0
    unknown_executions: Counter = 0


class Violation(APIModel):
    """Redacted finding returned in a list by GET /runs/{run_id}/violations.

    Producers must supply fixed/redacted text. This schema does not automatically
    sanitize strings. Raw arguments, leads, prompts, and tool results do not belong
    in these fields.
    """

    event_id: UUID
    run_id: UUID
    step: Counter
    tool_name: ToolName
    decision: Literal["flag", "block", "require_approval"]
    rule_id: NonEmptyString
    severity: Severity
    reason: NonEmptyString
    regulatory_context: list[str] = Field(default_factory=list)
    redacted_summary: NonEmptyString
    timestamp: AwareDatetime


class ExecutionReport(APIModel):
    """Initial gateway-to-engine accounting contract; contains no tool payload.

    A started report claims an allowed action before dispatch. Final reports
    record its confirmed result; unknown means side effects cannot be ruled out.
    """

    event_id: UUID
    state: Literal["started", "succeeded", "unknown"]


class GatewayMetadata(APIModel):
    """MCP call metadata in _meta.agent_monitor; not business-tool arguments."""

    run_id: UUID
    event_id: UUID
    step: Counter
    purpose: NonEmptyString = "qualified_lead_outreach"


class DemoRequest(APIModel):
    mode: RunMode = "protected"
    driver: Literal["enforcement", "otari"] = "enforcement"
    scenario: Scenario = "recipient"
    prompt: NonEmptyString | None = None
    max_steps: Annotated[StrictInt, Field(ge=1, le=12)] = 6
    capability_profile: StrictBool = False


class BaselineObservation(APIModel):
    event_id: UUID
    step: Counter
    tool_name: ToolName
    state: Literal["started", "succeeded", "unknown"]


class ModelUsage(APIModel):
    call_id: UUID
    request_id: NonEmptyString | None = None
    model: NonEmptyString | None = None
    role: Literal["agent", "reviewer", "router"] = "agent"
    resolved_model: NonEmptyString | None = None
    provider: NonEmptyString | None = None
    attempt_count: Counter | None = None
    fallback: StrictBool | None = None
    prompt_tokens: Counter | None = None
    completion_tokens: Counter | None = None
    total_tokens: Counter | None = None
    cost_usd: Annotated[Decimal, Field(ge=0, allow_inf_nan=False)] | None = None
    pricing_source: (
        Literal["organization", "deployment", "defaults", "reported"] | None
    ) = None


class FailRunRequest(APIModel):
    reason: FailureReason = "tool_or_accounting_failed"


class LeadScore(APIModel):
    lead_id: Annotated[str, Field(pattern=r"^L-\d{3}$")]
    score: Annotated[Decimal, Field(ge=0, le=100, allow_inf_nan=False)]


class ReviewResult(APIModel):
    conclusion: Literal["policy_followed", "needs_attention"]
    observed_controls: list[
        Literal[
            "destination_restricted",
            "sensitive_fields_removed",
            "approval_held",
            "unprotected_export",
            "execution_uncertain",
        ]
    ]
    recommended_next_step: Literal["complete", "wait_for_human", "investigate"]
    rationale_summary: Annotated[str, Field(min_length=1, max_length=600)]


class CapabilityEvidence(APIModel):
    evidence_id: UUID
    capability: Literal[
        "web_fetch",
        "code_execution",
        "routing_fallback",
        "budget_enforcement",
        "guardrails",
        "reviewer",
        "mcp_integration",
    ]
    status: Literal["passed", "failed", "unavailable", "not_configured"]
    summary: Annotated[str, Field(min_length=1, max_length=400)]
    timestamp: AwareDatetime = Field(default_factory=utc_now)
    request_id: NonEmptyString | None = None
    http_status: Annotated[StrictInt, Field(ge=100, le=599)] | None = None
    error_code: NonEmptyString | None = None
    requested_model: NonEmptyString | None = None
    resolved_model: NonEmptyString | None = None
    provider: NonEmptyString | None = None
    attempt_count: Counter | None = None
    fallback: StrictBool | None = None
    cost_usd: Annotated[Decimal, Field(ge=0, allow_inf_nan=False)] | None = None
    pricing_source: (
        Literal["organization", "deployment", "defaults", "reported"] | None
    ) = None
    source_url: NonEmptyString | None = None
    content_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None
    character_count: Counter | None = None
    return_code: StrictInt | None = None
    scores: list[LeadScore] = Field(default_factory=list)
    review: ReviewResult | None = None
    mcp_server_id: UUID | None = None
    mcp_tool_name: NonEmptyString | None = None
    client_execution_id: UUID | None = None

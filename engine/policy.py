"""Deterministic organization policies; no model can override these verdicts."""

import os
from dataclasses import dataclass

from pydantic import ValidationError

from contracts.schemas import Event, Verdict
from contracts.tools import TOOL_ARGUMENTS
from engine.classifier import APPROVED_CRM_FIELDS, sensitive_fields


@dataclass(frozen=True)
class PolicySettings:
    approved_destinations: frozenset[str] = frozenset({"approved-crm"})
    approved_purposes: frozenset[str] = frozenset({"qualified_lead_outreach"})

    @classmethod
    def from_env(cls) -> "PolicySettings":
        # An explicitly empty allowlist denies all exports.
        destinations = os.getenv("APPROVED_CRM_DESTINATIONS", "approved-crm")
        return cls(frozenset(value.strip() for value in destinations.split(",") if value.strip()))


def evaluate(event: Event, settings: PolicySettings) -> Verdict:
    def verdict(decision, severity, rule_id, reason, reference, regulatory=(), alternative=None):
        return Verdict(
            event_id=event.event_id, decision=decision, severity=severity,
            rule_id=rule_id, reason=reason, policy_reference=reference,
            regulatory_context=list(regulatory), safe_alternative=alternative,
        )

    try:
        arguments = TOOL_ARGUMENTS[event.tool_name].model_validate(event.arguments)
    except (ValidationError, KeyError):
        return verdict("block", "high", "INVALID_TOOL_ARGUMENTS", "Tool arguments do not match the agreed interface", "company_policy:tool_contract")
    if event.context.purpose not in settings.approved_purposes:
        return verdict("block", "high", "PURPOSE_NOT_APPROVED", "Purpose is not approved for these tools", "company_policy:approved_purposes", ["GDPR Art. 5(1)(b)"])

    if event.tool_name == "send_to_crm":
        alternative = None
        if settings.approved_destinations:
            alternative = {
                "tool_name": "send_to_crm",
                "destination": sorted(settings.approved_destinations)[0],
                "fields": sorted(APPROVED_CRM_FIELDS),
            }
        if arguments.destination not in settings.approved_destinations:
            return verdict("block", "high", "DESTINATION_NOT_APPROVED", "Recipient is not approved for lead export", "company_policy:crm_recipient_allowlist", ["GDPR Art. 5(1)(b)", "GDPR Art. 5(1)(c)"], alternative)
        if sensitive_fields(arguments.fields):
            return verdict("block", "high", "SENSITIVE_FIELDS_NOT_NEEDED", "Sensitive fields are not needed for the approved purpose", "company_policy:data_minimization", ["GDPR Art. 5(1)(c)", "GDPR Art. 9"], alternative)
        if set(arguments.fields) - APPROVED_CRM_FIELDS:
            return verdict("block", "high", "FIELDS_NOT_APPROVED", "Export contains fields outside the approved CRM field set", "company_policy:data_minimization", ["GDPR Art. 5(1)(c)"], alternative)
    if event.tool_name == "decide_on_lead":
        return verdict("require_approval", "medium", "HUMAN_APPROVAL_REQUIRED", "Lead decisions are held for human approval under organization policy", "company_policy:human_approval")
    return verdict("allow", "info", "POLICY_ALLOW", "Action meets the configured organization policy", "company_policy:approved_action")

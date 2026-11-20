"""Shared business-tool argument contracts."""

from pydantic import Field

from contracts.schemas import APIModel, NonEmptyString


class SearchLeadsArguments(APIModel):
    query: NonEmptyString


class GetLeadArguments(APIModel):
    lead_id: NonEmptyString


class SendToCRMArguments(GetLeadArguments):
    fields: list[NonEmptyString] = Field(min_length=1)
    destination: NonEmptyString


class DecideOnLeadArguments(GetLeadArguments):
    decision: NonEmptyString
    reason: NonEmptyString


class SendEmailArguments(APIModel):
    to: NonEmptyString
    subject: NonEmptyString
    body: NonEmptyString


TOOL_ARGUMENTS: dict[str, type[APIModel]] = {
    "search_leads": SearchLeadsArguments,
    "get_lead": GetLeadArguments,
    "send_to_crm": SendToCRMArguments,
    "decide_on_lead": DecideOnLeadArguments,
    "send_email": SendEmailArguments,
}

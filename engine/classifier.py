"""Explicit classifications for the agreed synthetic lead fields."""

APPROVED_CRM_FIELDS = frozenset({"name", "business_email"})
SENSITIVE_FIELDS = frozenset({"health_note", "religion_note"})


def sensitive_fields(fields: list[str]) -> set[str]:
    return set(fields) & SENSITIVE_FIELDS

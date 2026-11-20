"""Conservative rubric evidence audit. This is not a judge-awarded score."""

from engine.capabilities import OPEN_MODELS


def coverage(usage, capabilities):
    known = {key.lower(): value for key, value in OPEN_MODELS.items()}
    known.update(
        {
            "mistralai/mistral-small-3.2-24b-instruct-2506": OPEN_MODELS[
                "mistralai/mistral-small-3.2-24b"
            ],
            "mistral-small-3.2-24b-instruct-2506": OPEN_MODELS[
                "mistralai/mistral-small-3.2-24b"
            ],
            "gpt-oss-20b": OPEN_MODELS["openai/gpt-oss-20b"],
            "gpt-oss-120b": OPEN_MODELS["openai/gpt-oss-120b"],
        }
    )
    roles = {}
    for item in usage:
        requested = known.get((item.get("model") or "").lower())
        resolved = known.get((item.get("resolved_model") or "").lower())
        if item.get("request_id") and requested and requested == resolved:
            roles.setdefault(item.get("role", "agent"), set()).add(requested)
    passed = {item["capability"] for item in capabilities if item["status"] == "passed"}
    separate = "reviewer" in passed and any(
        agent != reviewer
        for agent in roles.get("agent", set())
        for reviewer in roles.get("reviewer", set())
    )
    open_points = (20 if roles else 0) + (10 if separate else 0)
    features = {
        "Model routing and fallbacks": "routing_fallback" in passed,
        "MCP integrations (Otari-native)": "mcp_integration" in passed,
        "Web search or web fetch": "web_fetch" in passed,
        "Code execution": "code_execution" in passed,
        "Budgets and spending limits": "budget_enforcement" in passed,
        "Multiple models serving distinct roles": separate,
        "Guardrails (Otari-native)": "guardrails" in passed,
        "Per-user budgets": False,
    }
    feature_points = min(60, 10 * sum(features.values()))
    return {
        "open_weight_evidence_points": open_points,
        "otari_feature_evidence_points": feature_points,
        "technical_evidence_points": open_points + feature_points,
        "technical_maximum": 90,
        "features": features,
        "open_model_roles": {role: sorted(cards) for role, cards in roles.items()},
        "note": "Self-audit of this run's technical evidence, not awarded points. Project/video 10 points require separate judging and the participant's own recording.",
    }

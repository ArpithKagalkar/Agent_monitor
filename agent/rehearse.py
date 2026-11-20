"""Repeatable demo rehearsal against running services, with redacted evidence.

--live makes real, billable Otari calls. No model or tool action is retried.
"""

import argparse
import json
import os
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


class RehearsalError(RuntimeError):
    """Fixed diagnostic messages that are safe to include in a report."""


def validate_evidence(evidence):
    summary = evidence["summary"]
    timeline, writes, usage = (evidence[key] for key in ("timeline", "writes", "usage"))
    if summary["status"] != "completed":
        raise RehearsalError(
            "Run did not complete; inspect its failure reason and local tool state"
        )
    if summary["pending_executions"] or summary["unknown_executions"]:
        raise RehearsalError("An execution outcome is unresolved")
    blocked = {
        row["event_id"]
        for row in timeline
        if row["decision"] in {"block", "require_approval"}
    }
    if blocked & {row["event_id"] for row in writes}:
        raise RehearsalError("A denied event has a downstream write record")
    if summary["mode"] == "unprotected_test":
        if summary["total_checked"] != 0 or len(writes) != 1:
            raise RehearsalError(
                "Baseline did not produce exactly one uncontrolled synthetic write"
            )
        if (
            writes[0]["destination"] != "unapproved-crm"
            or "health_note" not in writes[0]["fields"]
        ):
            raise RehearsalError(
                "Baseline did not demonstrate the requested unapproved sensitive export"
            )
    elif summary["scenario"] == "approval":
        if summary["approval_required"] < 1 or writes:
            raise RehearsalError(
                "Approval hold did not suppress the downstream decision write"
            )
    else:
        rule = (
            "DESTINATION_NOT_APPROVED"
            if summary["scenario"] == "recipient"
            else "SENSITIVE_FIELDS_NOT_NEEDED"
        )
        if not any(
            row["rule_id"] == rule and row["decision"] == "block" for row in timeline
        ):
            raise RehearsalError("Expected policy block was not demonstrated")
        if len(writes) != 1 or writes[0]["tool_name"] != "send_to_crm":
            raise RehearsalError("Safe retry did not produce exactly one CRM write")
        if writes[0]["destination"] != "approved-crm" or set(writes[0]["fields"]) != {
            "name",
            "business_email",
        }:
            raise RehearsalError(
                "Safe retry was not minimized to the approved recipient and fields"
            )
    if summary["driver"] == "otari":
        if not usage or any(not row.get("request_id") for row in usage):
            raise RehearsalError("Live run did not record Otari request IDs and usage")
    elif usage:
        raise RehearsalError("Enforcement-only test unexpectedly contains model usage")


def collect(http, run_id):
    def get(suffix=""):
        response = http.get(f"/runs/{run_id}{suffix}")
        response.raise_for_status()
        return response.json()

    return {
        "summary": get(),
        "timeline": get("/timeline"),
        "writes": get("/tool-writes"),
        "findings": get("/violations"),
        "usage": get("/usage"),
        "capabilities": get("/capabilities"),
        "coverage": get("/coverage"),
    }


def rehearse(
    http, *, live=False, capability_profile=False, trace_confirmed_run=None, timeout=180
):
    report = {
        "checked_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
        "status": "failed",
        "live_requested": live,
        "capability_profile": capability_profile,
        "trace_verification": {
            "status": "not_checked",
            "method": "manual_dashboard_check",
        },
        "runs": [],
    }
    try:
        health_response = http.get("/health")
        health_response.raise_for_status()
        health = health_response.json()
        services_response = http.get("/services")
        services_response.raise_for_status()
        services = services_response.json()
        report["services"] = services
        if (
            health.get("status") != "ok"
            or not health.get("durable")
            or not all(services.values())
        ):
            raise RehearsalError(
                "Start all local services with durable SQLite evidence before rehearsing"
            )
        if live and not health.get("otari_ready"):
            raise RehearsalError(
                "Otari credentials or model configuration are incomplete"
            )
        if trace_confirmed_run:
            previous = collect(http, trace_confirmed_run)
            if previous["summary"]["driver"] != "otari" or not previous["usage"]:
                raise RehearsalError(
                    "The manually confirmed trace must refer to a recorded Otari run"
                )
            report["trace_verification"] = {
                "status": "user_confirmed",
                "method": "manual_dashboard_check",
                "run_id": str(trace_confirmed_run),
            }
        cases = [
            ("unprotected_test", "enforcement", "recipient"),
            ("protected", "enforcement", "recipient"),
            ("protected", "enforcement", "sensitive"),
            ("protected", "enforcement", "approval"),
        ]
        if live:
            cases += [
                ("unprotected_test", "otari", "recipient"),
                ("protected", "otari", "recipient"),
            ]
        for mode, driver, scenario in cases:
            print(f"Rehearsing {driver} / {mode} / {scenario}", flush=True)
            response = http.post(
                "/demo/run",
                json={
                    "mode": mode,
                    "driver": driver,
                    "scenario": scenario,
                    "max_steps": 6,
                    "capability_profile": capability_profile and driver == "otari",
                },
            )
            response.raise_for_status()
            run_id = response.json()["run_id"]
            report["active_run"] = run_id
            deadline = time.monotonic() + timeout
            while True:
                summary_response = http.get(f"/runs/{run_id}")
                summary_response.raise_for_status()
                summary = summary_response.json()
                if summary["status"] != "running":
                    break
                if time.monotonic() >= deadline:
                    raise RehearsalError(
                        "Run exceeded the rehearsal wait; inspect it before another attempt"
                    )
                time.sleep(0.2)
            evidence = collect(http, run_id)
            report["runs"].append(evidence)
            validate_evidence(evidence)
            if evidence["summary"].get("capability_profile"):
                required = {
                    "web_fetch",
                    "code_execution",
                    "routing_fallback",
                    "budget_enforcement",
                    "reviewer",
                    "mcp_integration",
                }
                passed = {
                    item["capability"]
                    for item in evidence["capabilities"]
                    if item["status"] == "passed"
                }
                if (
                    not required <= passed
                    or evidence["coverage"]["technical_evidence_points"] < 90
                ):
                    raise RehearsalError(
                        "The application completed, but required Otari capability evidence is incomplete"
                    )
            report.pop("active_run", None)
            print(
                f"  PASS {run_id}: {summary['blocked']} blocked, {len(evidence['writes'])} writes",
                flush=True,
            )
        priced = [
            row["cost_usd"]
            for item in report["runs"]
            for row in item["usage"]
            if row.get("cost_usd") is not None
        ]
        usage_count = sum(len(item["usage"]) for item in report["runs"])
        report["reported_cost_usd"] = (
            str(sum(Decimal(value) for value in priced)) if priced else None
        )
        report["all_model_calls_priced"] = (
            bool(usage_count) and len(priced) == usage_count
        )
        native_prices = [
            Decimal(row["cost_usd"])
            for item in report["runs"]
            for row in item["capabilities"]
            if row["capability"]
            in {
                "web_fetch",
                "code_execution",
                "budget_enforcement",
                "guardrails",
                "mcp_integration",
            }
            and row.get("cost_usd") is not None
        ]
        report["reported_native_tool_cost_usd"] = (
            str(sum(native_prices)) if native_prices else None
        )
        successful_native = [
            row
            for item in report["runs"]
            for row in item["capabilities"]
            if row["capability"]
            in {
                "web_fetch",
                "code_execution",
                "budget_enforcement",
                "guardrails",
                "mcp_integration",
            }
            and row["http_status"] == 200
        ]
        report["all_successful_native_calls_priced"] = bool(successful_native) and all(
            row.get("cost_usd") is not None for row in successful_native
        )
        report["status"] = "passed"
    except (httpx.HTTPError, ValueError, KeyError, RehearsalError) as exc:
        report["error"] = (
            str(exc)
            if isinstance(exc, RehearsalError)
            else "A local service response failed validation or could not be read"
        )
    return report


def main():
    load_dotenv(ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(
        description="Rehearse actual local demo flows and save redacted evidence"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Also run the real Otari agent in both modes (billable)",
    )
    parser.add_argument(
        "--capability-profile",
        action="store_true",
        help="Exercise and verify native Otari capabilities in both live runs",
    )
    parser.add_argument(
        "--trace-confirmed-run",
        type=UUID,
        help="Only supply after manually confirming this run in Otari Activity/Sessions",
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "outputs/demo-readiness.json"
    )
    args = parser.parse_args()
    if args.capability_profile and not args.live:
        parser.error("--capability-profile requires --live")
    with httpx.Client(
        base_url=os.getenv("ENGINE_URL", "http://localhost:8000"), timeout=10
    ) as http:
        report = rehearse(
            http,
            live=args.live,
            capability_profile=args.capability_profile,
            trace_confirmed_run=args.trace_confirmed_run,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Rehearsal {report['status']}. Redacted evidence: {args.output}")
    if report["status"] != "passed":
        print(report.get("error", "Rehearsal failed"))
        raise SystemExit(1)


if __name__ == "__main__":
    main()

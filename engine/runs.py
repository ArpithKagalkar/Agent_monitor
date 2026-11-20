"""Durable run accounting, redacted findings, and atomic execution claims."""

import hashlib
import json
from uuid import UUID, uuid4

from contracts.schemas import (
    BaselineObservation,
    CapabilityEvidence,
    Event,
    ExecutionReport,
    ModelUsage,
    RunSummary,
    Verdict,
    Violation,
    utc_now,
)
from engine.policy import PolicySettings, evaluate
from store.db import EvidenceStore


class RunError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class RunManager:
    def __init__(self, settings: PolicySettings, store: EvidenceStore | None = None):
        self.settings = settings
        self.store = store or EvidenceStore()

    def _summary(self, conn, run_id) -> RunSummary:
        summary = self.store.get_summary(conn, run_id)
        if summary is None:
            raise RunError(404, "Run not found")
        return summary

    def start(
        self, mode, *, driver="manual", scenario="recipient", capability_profile=False
    ) -> RunSummary:
        summary = RunSummary(
            run_id=uuid4(),
            mode=mode,
            status="running",
            driver=driver,
            scenario=scenario,
            capability_profile=capability_profile,
            started_at=utc_now(),
        )
        with self.store.transaction() as conn:
            self.store.save_summary(conn, summary)
        return summary

    def summary(self, run_id: UUID) -> RunSummary:
        with self.store.transaction() as conn:
            return self._summary(conn, run_id)

    def violations(self, run_id: UUID) -> list[Violation]:
        with self.store.transaction() as conn:
            self._summary(conn, run_id)
            return [
                Violation.model_validate_json(row[0])
                for row in conn.execute(
                    "SELECT payload_json FROM violations WHERE run_id=? ORDER BY rowid",
                    (str(run_id),),
                )
            ]

    def check(self, event: Event) -> Verdict:
        if event.source != "mcp_gateway":
            raise RunError(
                403, "Only the gateway creates authoritative policy decisions"
            )
        data = event.model_dump(mode="json", exclude={"timestamp"})
        fingerprint = hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self.store.transaction() as conn:
            summary = self._summary(conn, event.run_id)
            previous = conn.execute(
                "SELECT * FROM actions WHERE event_id=?", (str(event.event_id),)
            ).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise RunError(
                        409, "Event ID was already used for a different action"
                    )
                return Verdict.model_validate_json(previous["verdict_json"])
            if summary.status != "running":
                raise RunError(409, "Run has ended")
            if summary.mode != "protected":
                raise RunError(409, "Protected checks require a protected run")
            result = evaluate(event, self.settings)
            conn.execute(
                "INSERT INTO actions VALUES (?, ?, ?, ?, NULL)",
                (
                    str(event.event_id),
                    str(event.run_id),
                    fingerprint,
                    result.model_dump_json(),
                ),
            )
            summary.total_checked += 1
            counter = {
                "allow": "allowed",
                "flag": "flagged",
                "block": "blocked",
                "require_approval": "approval_required",
            }[result.decision]
            setattr(summary, counter, getattr(summary, counter) + 1)
            status = "authorized" if result.decision in {"allow", "flag"} else "denied"
            conn.execute(
                "INSERT INTO timeline VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(event.event_id),
                    str(event.run_id),
                    event.step,
                    event.tool_name,
                    result.decision,
                    status,
                    result.rule_id,
                    event.timestamp.isoformat(),
                ),
            )
            if result.decision != "allow":
                finding = Violation(
                    event_id=event.event_id,
                    run_id=event.run_id,
                    step=event.step,
                    tool_name=event.tool_name,
                    decision=result.decision,
                    rule_id=result.rule_id,
                    severity=result.severity,
                    reason=result.reason,
                    regulatory_context=result.regulatory_context,
                    redacted_summary=f"{event.tool_name}: {result.rule_id}",
                    timestamp=event.timestamp,
                )
                conn.execute(
                    "INSERT INTO violations VALUES (?, ?, ?)",
                    (str(event.event_id), str(event.run_id), finding.model_dump_json()),
                )
            self.store.save_summary(conn, summary)
            return result

    def report(self, run_id: UUID, report: ExecutionReport) -> ExecutionReport:
        with self.store.transaction() as conn:
            summary = self._summary(conn, run_id)
            action = conn.execute(
                "SELECT * FROM actions WHERE event_id=? AND run_id=?",
                (str(report.event_id), str(run_id)),
            ).fetchone()
            if not action:
                raise RunError(404, "Checked action not found")
            verdict = Verdict.model_validate_json(action["verdict_json"])
            if verdict.decision not in {"allow", "flag"}:
                raise RunError(409, "A denied action cannot execute")
            old_state = action["execution_state"]
            if report.state == "started":
                if old_state is not None or summary.status != "running":
                    raise RunError(
                        409,
                        "Action already claimed or run has ended; do not dispatch again",
                    )
                summary.pending_executions += 1
            else:
                if old_state == report.state:
                    return report
                if old_state != "started":
                    raise RunError(
                        409,
                        "Execution must be claimed before dispatch and finalized once",
                    )
                if report.state == "succeeded":
                    summary.executed_count += 1
                else:
                    summary.unknown_executions += 1
                summary.pending_executions -= 1
            conn.execute(
                "UPDATE actions SET execution_state=? WHERE event_id=?",
                (report.state, str(report.event_id)),
            )
            conn.execute(
                "UPDATE timeline SET execution_status=? WHERE event_id=?",
                (report.state, str(report.event_id)),
            )
            self.store.save_summary(conn, summary)
            return report

    def observe_baseline(self, run_id: UUID, observation: BaselineObservation):
        with self.store.transaction() as conn:
            summary = self._summary(conn, run_id)
            if summary.mode != "unprotected_test" or summary.status != "running":
                raise RunError(
                    409, "Baseline observations require an active unprotected test"
                )
            old = conn.execute(
                "SELECT * FROM timeline WHERE event_id=?", (str(observation.event_id),)
            ).fetchone()
            if old and (
                old["run_id"] != str(run_id)
                or old["tool_name"] != observation.tool_name
                or old["step"] != observation.step
            ):
                raise RunError(
                    409, "Baseline event ID was reused for a different action"
                )
            if observation.state == "started":
                if old:
                    raise RunError(409, "Baseline action was already claimed")
                summary.pending_executions += 1
                conn.execute(
                    "INSERT INTO timeline VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(observation.event_id),
                        str(run_id),
                        observation.step,
                        observation.tool_name,
                        "unprotected",
                        "started",
                        "ENFORCEMENT_OFF",
                        utc_now().isoformat(),
                    ),
                )
            else:
                if old and old["execution_status"] == observation.state:
                    return observation
                if not old or old["execution_status"] != "started":
                    raise RunError(
                        409,
                        "Baseline execution must start before its outcome is recorded",
                    )
                summary.pending_executions -= 1
                if observation.state == "succeeded":
                    summary.executed_count += 1
                else:
                    summary.unknown_executions += 1
                conn.execute(
                    "UPDATE timeline SET execution_status=? WHERE event_id=?",
                    (observation.state, str(observation.event_id)),
                )
            self.store.save_summary(conn, summary)
            return observation

    def record_usage(self, run_id: UUID, usage: ModelUsage):
        with self.store.transaction() as conn:
            self._summary(conn, run_id)
            conn.execute(
                "INSERT INTO model_usage VALUES (?, ?, ?, ?) ON CONFLICT(call_id) DO NOTHING",
                (
                    str(usage.call_id),
                    str(run_id),
                    usage.model_dump_json(),
                    utc_now().isoformat(),
                ),
            )
        return usage

    def end(self, run_id: UUID, *, failed=False, reason=None) -> RunSummary:
        with self.store.transaction() as conn:
            summary = self._summary(conn, run_id)
            if summary.status != "running":
                return summary
            if summary.pending_executions:
                if not failed:
                    raise RunError(
                        409,
                        "Run has an unresolved execution; confirm its outcome before ending",
                    )
                # Failed orchestration cannot establish whether pending actions wrote.
                summary.unknown_executions += summary.pending_executions
                summary.pending_executions = 0
                conn.execute(
                    "UPDATE actions SET execution_state='unknown' WHERE run_id=? AND execution_state='started'",
                    (str(run_id),),
                )
                conn.execute(
                    "UPDATE timeline SET execution_status='unknown' WHERE run_id=? AND execution_status='started'",
                    (str(run_id),),
                )
            summary.status = (
                "failed" if failed or summary.unknown_executions else "completed"
            )
            if failed:
                summary.failure_reason = reason or "tool_or_accounting_failed"
            summary.ended_at = summary.ended_at or utc_now()
            self.store.save_summary(conn, summary)
            return summary

    def record_capability(self, run_id: UUID, evidence: CapabilityEvidence):
        with self.store.transaction() as conn:
            self._summary(conn, run_id)
            conn.execute(
                "INSERT INTO capability_evidence VALUES (?, ?, ?, ?) ON CONFLICT(evidence_id) DO NOTHING",
                (
                    str(evidence.evidence_id),
                    str(run_id),
                    evidence.model_dump_json(),
                    evidence.timestamp.isoformat(),
                ),
            )
        return evidence

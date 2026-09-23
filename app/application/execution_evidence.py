"""Projection helpers for user-facing Oracle execution evidence."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
import re
from typing import Any

from app.models.job import JobRecordStatistics
from app.models.workflow import WorkflowRun, WorkflowStatus
from app.utils.exceptions import EPMError, JobFailedError


_SENSITIVE_VALUE = re.compile(
    r"(?i)\b(password|secret|token|api[ _-]?key|authorization|credential)"
    r"\s*[:=]\s*([^\s,;]+)"
)


def aggregate_record_statistics(
    step_details: Iterable[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Combine retained Oracle counters across compatible workflow steps."""
    statistics: list[Mapping[str, Any]] = []
    for details in step_details:
        value = details.get("record_statistics")
        if isinstance(value, Mapping):
            statistics.append(value)
    if not statistics:
        return None

    breakdown: list[dict[str, Any]] = []
    for statistic in statistics:
        raw_details = statistic.get("details")
        if not isinstance(raw_details, list):
            continue
        breakdown.extend(
            dict(item) for item in raw_details if isinstance(item, Mapping)
        )

    sources = {
        str(item.get("source") or "ORACLE_JOB_DETAILS")
        for item in statistics
    }
    source = (
        next(iter(sources))
        if len(sources) == 1
        else "ORACLE_COMBINED_EVIDENCE"
    )

    return {
        "source": source,
        "records_read": sum(
            _counter(item.get("records_read")) for item in statistics
        ),
        "records_processed": sum(
            _counter(item.get("records_processed")) for item in statistics
        ),
        "records_rejected": sum(
            _counter(item.get("records_rejected")) for item in statistics
        ),
        "details": breakdown,
    }


def aggregate_import_evidence(
    step_details: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project structured import evidence retained across workflow steps."""
    lineage: Mapping[str, Any] | None = None
    messages: list[dict[str, Any]] = []
    rejected_records: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    notices: list[str] = []
    seen_messages: set[tuple[str, str, str, str]] = set()
    seen_artifacts: set[str] = set()
    for details in step_details:
        raw_lineage = details.get("load_lineage")
        if lineage is None and isinstance(raw_lineage, Mapping):
            lineage = raw_lineage
        raw_messages = details.get("oracle_messages")
        if isinstance(raw_messages, list):
            for item in raw_messages:
                if not isinstance(item, Mapping):
                    continue
                payload = {
                    "message_type": _safe_text(item.get("message_type"), limit=30) or "INFO",
                    "category": _safe_text(item.get("category"), limit=200),
                    "message": _safe_text(item.get("message"), limit=4000) or "",
                    "dimension_name": _safe_text(item.get("dimension_name"), limit=200),
                    "child_job_id": _safe_text(item.get("child_job_id"), limit=100),
                }
                key = tuple(str(payload[field] or "") for field in ("message_type", "category", "message", "dimension_name"))
                if payload["message"] and key not in seen_messages:
                    messages.append(payload)
                    seen_messages.add(key)
        raw_rejections = details.get("rejected_records")
        if isinstance(raw_rejections, list):
            rejected_records.extend(
                dict(item) for item in raw_rejections if isinstance(item, Mapping)
            )
        raw_artifacts = details.get("artifacts")
        if isinstance(raw_artifacts, list):
            for item in raw_artifacts:
                if not isinstance(item, Mapping):
                    continue
                artifact_id = str(item.get("artifact_id") or "")
                if not artifact_id or artifact_id in seen_artifacts:
                    continue
                artifacts.append(
                    {
                        "artifact_id": artifact_id,
                        "name": _safe_text(item.get("name"), limit=255) or "Oracle artifact",
                        "kind": _safe_text(item.get("kind"), limit=80) or "ORACLE_ARTIFACT",
                        "size_bytes": _counter(item.get("size_bytes")),
                    }
                )
                seen_artifacts.add(artifact_id)
        for notice_key in ("artifact_message", "evidence_message"):
            notice = _safe_text(details.get(notice_key), limit=500)
            if notice and notice not in notices:
                notices.append(notice)
    return {
        "lineage": _safe_lineage(lineage),
        "oracle_messages": messages[:2000],
        "rejected_records": rejected_records[:100],
        "artifacts": artifacts[:200],
        "notices": notices,
    }


def _safe_lineage(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    allowed = (
        "operation",
        "source_kind",
        "source_file",
        "staging_location",
        "oracle_job_name",
        "target_application",
        "target_system",
        "origin_note",
    )
    return {
        key: _safe_text(value.get(key), limit=500)
        for key in allowed
        if value.get(key) is not None
    }


def workflow_failure_details(exc: Exception) -> dict[str, Any]:
    """Project safe Oracle failure evidence into a durable workflow step."""
    if not isinstance(exc, JobFailedError):
        return {}

    evidence: dict[str, Any] = {
        "job_id": exc.job.job_id,
        "status": exc.job.descriptive_status or exc.job.status,
        **dict(exc.evidence),
    }
    candidates: list[Mapping[str, Any]] = [exc.job.raw_response]
    if exc.diagnostics is not None and isinstance(
        exc.diagnostics.details,
        Mapping,
    ):
        candidates.insert(0, exc.diagnostics.details)

    for candidate in candidates:
        try:
            statistics = JobRecordStatistics.from_response(candidate)
        except EPMError:
            continue
        if statistics is not None:
            evidence["record_statistics"] = statistics.to_payload()
            break
    if exc.diagnostics is not None and exc.diagnostics.messages:
        evidence.setdefault(
            "oracle_messages",
            [
                {
                    "message_type": str(
                        item.get("msgType") or item.get("messageType") or "ERROR"
                    ),
                    "category": item.get("msgCategory") or item.get("messageCategory"),
                    "message": str(item.get("msgText") or item.get("message") or ""),
                    "dimension_name": item.get("dimensionName"),
                    "child_job_id": item.get("childJobId"),
                }
                for item in exc.diagnostics.messages
                if isinstance(item, Mapping)
                and (item.get("msgText") or item.get("message"))
            ],
        )
    return evidence


def agent_execution_evidence(run: WorkflowRun) -> dict[str, Any]:
    """Return bounded, credential-safe evidence for one assistant response.

    The assistant receives useful operational facts rather than raw workflow
    rows. File-system paths, upload tokens, credentials, and unrelated step
    internals are deliberately excluded.
    """
    statistics = aggregate_record_statistics(
        step.details for step in run.steps
    )
    safe_statistics = _agent_record_statistics(statistics)
    steps: list[dict[str, Any]] = []
    artifacts: list[str] = []
    for step in run.steps:
        evidence, step_artifacts = _agent_step_evidence(step.details)
        artifacts.extend(step_artifacts)
        steps.append(
            {
                "sequence": step.sequence,
                "name": _safe_text(step.name, limit=240),
                "status": step.status.value,
                "started_at": (
                    step.started_at.isoformat() if step.started_at else None
                ),
                "completed_at": (
                    step.completed_at.isoformat()
                    if step.completed_at
                    else None
                ),
                "duration_seconds": (
                    max(
                        0,
                        round(
                            (
                                step.completed_at - step.started_at
                            ).total_seconds()
                        ),
                    )
                    if step.started_at and step.completed_at
                    else None
                ),
                "error_message": _safe_text(step.error_message),
                "evidence": evidence,
            }
        )

    completed_steps = sum(
        step["status"] in {"SUCCESS", "FAILED", "SKIPPED"}
        for step in steps
    )
    return {
        "execution_id": run.execution_id,
        "workflow_name": _safe_text(run.workflow_name, limit=300),
        "status": run.status.value,
        "started_at": run.started_at.isoformat(),
        "completed_at": (
            run.completed_at.isoformat() if run.completed_at else None
        ),
        "duration_seconds": (
            max(
                0,
                round((run.completed_at - run.started_at).total_seconds()),
            )
            if run.completed_at
            else None
        ),
        "completed_steps": completed_steps,
        "total_steps": len(steps),
        "initiated_by": _safe_text(
            run.initiated_by_display or run.initiated_by or "System",
            limit=200,
        ),
        "trigger_source": run.trigger_source.value,
        "executed_by": _safe_text(
            run.oracle_execution_username or "Not recorded",
            limit=254,
        ),
        "error_message": _safe_text(run.error_message),
        "record_statistics": safe_statistics,
        "steps": steps,
        "artifacts": list(dict.fromkeys(artifacts)),
        "diagnosis": _execution_diagnosis(run, steps, safe_statistics),
    }


def _agent_step_evidence(
    details: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Project only operationally useful, non-secret step fields."""
    allowed = {
        "job_id",
        "load_id",
        "target_name",
        "status",
        "engine",
        "skipped",
        "reason",
        "message",
        "dimension",
        "dimension_name",
        "load_type",
    }
    evidence: dict[str, Any] = {}
    for key in allowed:
        if key not in details:
            continue
        value = details[key]
        if isinstance(value, str):
            evidence[key] = _safe_text(value)
        elif value is None or isinstance(value, (bool, int, float)):
            evidence[key] = value

    artifacts: list[str] = []
    for key in ("output_file", "output_path"):
        raw = str(details.get(key) or "").strip()
        if raw:
            name = Path(raw).name
            if name:
                artifacts.append(_safe_text(name, limit=255) or name[:255])

    statistic = details.get("record_statistics")
    if isinstance(statistic, Mapping):
        evidence["record_statistics"] = _agent_record_statistics(
            dict(statistic)
        )
    return evidence, artifacts


def _agent_record_statistics(
    value: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if value is None:
        return None
    details: list[dict[str, Any]] = []
    raw_details = value.get("details")
    if isinstance(raw_details, list):
        for item in raw_details[:100]:
            if not isinstance(item, Mapping):
                continue
            details.append(
                {
                    "dimension_name": _safe_text(
                        item.get("dimension_name"), limit=200
                    ),
                    "load_type": _safe_text(
                        item.get("load_type"), limit=200
                    ),
                    "records_read": _counter(item.get("records_read")),
                    "records_processed": _counter(
                        item.get("records_processed")
                    ),
                    "records_rejected": _counter(
                        item.get("records_rejected")
                    ),
                }
            )
    return {
        "source": _safe_text(value.get("source"), limit=80)
        or "ORACLE_JOB_DETAILS",
        "records_read": _counter(value.get("records_read")),
        "records_processed": _counter(value.get("records_processed")),
        "records_rejected": _counter(value.get("records_rejected")),
        "details": details,
    }


def _execution_diagnosis(
    run: WorkflowRun,
    steps: list[dict[str, Any]],
    statistics: Mapping[str, Any] | None,
) -> str:
    """Produce a deterministic summary the model can explain faithfully."""
    failed = next(
        (step for step in steps if step["status"] == "FAILED"),
        None,
    )
    if run.status is WorkflowStatus.FAILED:
        step_name = str(failed.get("name")) if failed else "the workflow"
        error = (
            (failed or {}).get("error_message")
            or _safe_text(run.error_message)
        )
        if error:
            return f"Failed at {step_name}. Oracle reported: {error}"
        return f"Failed at {step_name}; no retained Oracle error was available."
    if run.status is WorkflowStatus.SUCCESS:
        rejected = (
            _counter(statistics.get("records_rejected"))
            if statistics
            else 0
        )
        if rejected:
            return (
                "Completed, but Oracle reported "
                f"{rejected} rejected record{'s' if rejected != 1 else ''}."
            )
        return "Completed successfully."
    if run.status is WorkflowStatus.RUNNING:
        return "Execution is still running."
    if run.status is WorkflowStatus.CANCELLED:
        return (
            _safe_text(run.error_message)
            or "Stopped safely; remaining workflow steps were not started."
        )
    return "Execution is queued and has not started yet."


def _safe_text(value: Any, *, limit: int = 2000) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return _SENSITIVE_VALUE.sub(r"\1=[redacted]", text)[:limit]


def _counter(value: Any) -> int:
    """Return a safe integer from already-normalized retained evidence."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0

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

    return {
        "source": "ORACLE_JOB_DETAILS",
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


def workflow_failure_details(exc: Exception) -> dict[str, Any]:
    """Project safe Oracle failure evidence into a durable workflow step."""
    if not isinstance(exc, JobFailedError):
        return {}

    evidence: dict[str, Any] = {
        "job_id": exc.job.job_id,
        "status": exc.job.descriptive_status or exc.job.status,
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
        "source": "ORACLE_JOB_DETAILS",
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

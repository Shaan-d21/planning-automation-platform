"""Durable, deterministic assistant follow-ups for terminal executions."""

from __future__ import annotations

import logging

from app.agent.repository import SQLAgentRepository
from app.application.execution_evidence import agent_execution_evidence
from app.infrastructure.database.engine import DatabaseTarget
from app.models.execution_queue import ExecutionJobStatus
from app.services.execution_queue_repository import SQLExecutionQueueRepository
from app.services.workflow_repository import SQLWorkflowRepository


_OPERATION_LABELS = {
    "business-rules": "Business Rule",
    "data-integrations": "Data Integration",
    "data-import": "Planning Data Import",
    "metadata-import": "Metadata Import",
    "data-maps": "Data Map",
    "pipelines": "Pipeline",
    "cube-refresh": "Cube Refresh",
    "substitution-variables": "Substitution Variable change",
    "user-variables": "User Variable change",
    "standalone-flow": "Planning flow",
}


class AgentExecutionFollowUpService:
    """Publish one evidence-backed assistant message for an approved run."""

    def __init__(
        self,
        database_target: DatabaseTarget,
        *,
        repository: SQLAgentRepository | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._queue = SQLExecutionQueueRepository(database_target)
        self._workflows = SQLWorkflowRepository(database_target)
        self._agents = repository or SQLAgentRepository(database_target)
        self._logger = logger or logging.getLogger(__name__)

    def publish(self, execution_id: str):
        """Publish the terminal result, returning None until it is available."""
        job = self._queue.get(str(execution_id).strip())
        if job is None or not job.status.terminal:
            return None
        run = self._workflows.get(job.execution_id)
        if run is None:
            return None
        decision = self._agents.find_action_decision_by_execution(
            job.execution_id
        )
        if decision is None:
            return None
        content = self._content(
            operation_code=decision.operation_code,
            artifact_name=decision.artifact_name,
            execution_status=job.status,
            evidence=agent_execution_evidence(run),
        )
        message = self._agents.append_execution_followup(
            execution_id=job.execution_id,
            completion_status=job.status.value,
            content=content,
        )
        if message is not None:
            self._logger.info(
                "Agent execution follow-up published: execution_id='%s', "
                "conversation_id='%s', status='%s'.",
                job.execution_id,
                message.conversation_id,
                job.status.value,
            )
        return message

    def publish_for_conversation(
        self,
        conversation_id: str,
        user_id: int,
    ) -> int:
        """Reconcile terminal runs whose worker follow-up was interrupted."""
        published = 0
        for decision in self._agents.list_action_decisions(
            conversation_id,
            user_id,
        ):
            execution_id = str(decision.execution_id or "").strip()
            if (
                decision.outcome_status != "SUBMITTED"
                or not execution_id
                or execution_id.startswith("schedule:")
                or decision.completion_notified_at is not None
            ):
                continue
            if self.publish(execution_id) is not None:
                published += 1
        return published

    @staticmethod
    def _content(
        *,
        operation_code: str,
        artifact_name: str | None,
        execution_status: ExecutionJobStatus,
        evidence: dict[str, object],
    ) -> str:
        label = _OPERATION_LABELS.get(operation_code, "Oracle operation")
        target = str(
            artifact_name or evidence.get("workflow_name") or label
        ).strip()
        execution_id = str(evidence.get("execution_id") or "").strip()
        diagnosis = str(evidence.get("diagnosis") or "").strip()
        lines: list[str]
        if execution_status is ExecutionJobStatus.SUCCESS:
            lines = [f"**{target} completed successfully**", diagnosis]
        elif execution_status is ExecutionJobStatus.CANCELLED:
            lines = [f"**{target} stopped safely**", diagnosis]
        elif execution_status is ExecutionJobStatus.RECOVERY_REQUIRED:
            lines = [
                f"**{target} needs execution review**",
                "The worker stopped before the platform could prove the final "
                "Oracle outcome. Review Oracle Job Console before retrying.",
            ]
        else:
            lines = [f"**{target} failed**", diagnosis]

        if operation_code == "standalone-flow":
            steps = tuple(evidence.get("steps") or ())
            successful = sum(
                isinstance(step, dict) and step.get("status") == "SUCCESS"
                for step in steps
            )
            skipped = sum(
                isinstance(step, dict) and step.get("status") == "SKIPPED"
                for step in steps
            )
            lines.append(
                f"Flow result: **{successful} succeeded**"
                + (f", **{skipped} skipped**" if skipped else "")
                + f" out of **{len(steps)} steps**."
            )

        statistics = evidence.get("record_statistics")
        if isinstance(statistics, dict):
            lines.append(
                "Records: "
                f"**{int(statistics.get('records_read') or 0):,} read**, "
                f"**{int(statistics.get('records_processed') or 0):,} processed**, "
                f"**{int(statistics.get('records_rejected') or 0):,} rejected**."
            )
        if execution_id:
            lines.append(f"Execution ID: `{execution_id}`")
        return "\n\n".join(line for line in lines if line)

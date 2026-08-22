"""SQLAlchemy persistence for user-owned agent conversations and audit."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.agent.models import (
    AgentActionDraft,
    AgentActionDecision,
    AgentConversation,
    AgentDraftCheck,
    AgentMessage,
    AgentMessageRole,
    AgentToolActivity,
)
from app.infrastructure.database.engine import (
    DatabaseTarget,
    database_for,
    utc_datetime,
)
from app.infrastructure.database.schema import (
    agent_action_drafts,
    agent_action_decisions,
    agent_conversations,
    agent_messages,
    agent_tool_activities,
)
from app.utils.exceptions import AgentConversationError


class SQLAgentRepository:
    """Persist user-owned conversations and governed action drafts."""

    def __init__(self, database_target: DatabaseTarget) -> None:
        self._database = database_for(database_target)

    def create_conversation(
        self,
        *,
        user_id: int,
        provider: str,
        model: str,
        title: str = "New conversation",
    ) -> AgentConversation:
        now = datetime.now(UTC)
        conversation = AgentConversation(
            conversation_id=str(uuid4()),
            user_id=user_id,
            title=title,
            provider=provider,
            model=model,
            created_at=now,
            updated_at=now,
        )
        with self._database.begin() as connection:
            connection.execute(
                insert(agent_conversations).values(
                    conversation_id=conversation.conversation_id,
                    user_id=user_id,
                    title=title,
                    provider=provider,
                    model=model,
                    created_at=now,
                    updated_at=now,
                )
            )
        return conversation

    def list_conversations(self, user_id: int) -> tuple[AgentConversation, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                select(agent_conversations)
                .where(agent_conversations.c.user_id == user_id)
                .order_by(agent_conversations.c.updated_at.desc())
            ).mappings().all()
        return tuple(self._conversation(row) for row in rows)

    def get_conversation(
        self,
        conversation_id: str,
        user_id: int,
    ) -> AgentConversation | None:
        with self._database.connect() as connection:
            row = connection.execute(
                select(agent_conversations).where(
                    agent_conversations.c.conversation_id == conversation_id,
                    agent_conversations.c.user_id == user_id,
                )
            ).mappings().one_or_none()
        return self._conversation(row) if row is not None else None

    def list_messages(
        self,
        conversation_id: str,
        user_id: int,
        *,
        limit: int = 100,
    ) -> tuple[AgentMessage, ...]:
        self._require_conversation(conversation_id, user_id)
        recent = (
            select(agent_messages)
            .where(agent_messages.c.conversation_id == conversation_id)
            .order_by(agent_messages.c.message_id.desc())
            .limit(limit)
            .subquery()
        )
        with self._database.connect() as connection:
            rows = connection.execute(
                select(recent).order_by(recent.c.message_id)
            ).mappings().all()
        return tuple(self._message(row) for row in rows)

    def add_message(
        self,
        *,
        conversation_id: str,
        user_id: int,
        role: AgentMessageRole,
        content: str,
    ) -> AgentMessage:
        conversation = self._require_conversation(conversation_id, user_id)
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            message_id = int(
                connection.execute(
                    insert(agent_messages)
                    .values(
                        conversation_id=conversation_id,
                        role=role.value,
                        content=content,
                        created_at=now,
                    )
                    .returning(agent_messages.c.message_id)
                ).scalar_one()
            )
            values: dict[str, object] = {"updated_at": now}
            if (
                conversation.title == "New conversation"
                and role is AgentMessageRole.USER
            ):
                values["title"] = self._title(content)
            connection.execute(
                update(agent_conversations)
                .where(agent_conversations.c.conversation_id == conversation_id)
                .values(**values)
            )
        return AgentMessage(
            message_id=message_id,
            conversation_id=conversation_id,
            role=role,
            content=content,
            created_at=now,
        )

    def record_tool_activity(
        self,
        *,
        conversation_id: str,
        user_id: int,
        activities: tuple[AgentToolActivity, ...],
    ) -> None:
        self._require_conversation(conversation_id, user_id)
        if not activities:
            return
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            connection.execute(
                insert(agent_tool_activities),
                [
                    {
                        "conversation_id": conversation_id,
                        "tool_name": item.name,
                        "arguments": item.arguments,
                        "status": item.status,
                        "summary": item.summary,
                        "created_at": now,
                    }
                    for item in activities
                ],
            )

    def latest_successful_tool_activity(
        self,
        *,
        conversation_id: str,
        user_id: int,
        tool_names: tuple[str, ...],
    ) -> AgentToolActivity | None:
        """Return the latest safe tool arguments for conversational context."""
        self._require_conversation(conversation_id, user_id)
        normalized = tuple(
            name.strip() for name in tool_names if str(name).strip()
        )
        if not normalized:
            return None
        statement = (
            select(agent_tool_activities)
            .where(
                agent_tool_activities.c.conversation_id == conversation_id,
                agent_tool_activities.c.status == "SUCCESS",
                agent_tool_activities.c.tool_name.in_(normalized),
            )
            .order_by(agent_tool_activities.c.activity_id.desc())
            .limit(1)
        )
        with self._database.connect() as connection:
            row = connection.execute(statement).mappings().first()
        if row is None:
            return None
        arguments = row["arguments"]
        return AgentToolActivity(
            name=str(row["tool_name"]),
            arguments=(dict(arguments) if isinstance(arguments, dict) else {}),
            status=str(row["status"]),
            summary=str(row["summary"]),
        )

    def create_action_draft(
        self,
        *,
        conversation_id: str,
        user_id: int,
        message_id: int,
        payload: dict[str, object],
    ) -> AgentActionDraft:
        self._require_conversation(conversation_id, user_id)
        now = datetime.now(UTC)
        draft = AgentActionDraft(
            draft_id=str(uuid4()),
            conversation_id=conversation_id,
            message_id=message_id,
            action_type=str(payload["action_type"]),
            target_code=str(payload["target_code"]),
            display_name=str(payload["display_name"]),
            category=str(payload["category"]),
            risk_level=str(payload["risk_level"]),
            route=str(payload["route"]),
            objective=str(payload["objective"]),
            artifact_name=(
                str(payload["artifact_name"])
                if payload.get("artifact_name")
                else None
            ),
            required_inputs=tuple(
                str(item) for item in payload.get("required_inputs", ())
            ),
            stages=tuple(str(item) for item in payload.get("stages", ())),
            approval_required=bool(payload.get("approval_required", True)),
            status=str(payload.get("status", "PREPARED_NOT_EXECUTED")),
            created_at=now,
            input_schema=tuple(
                dict(item)
                for item in payload.get("input_schema", ())
                if isinstance(item, dict)
            ),
            input_values=(
                dict(payload.get("input_values", {}))
                if isinstance(payload.get("input_values", {}), dict)
                else {}
            ),
        )
        with self._database.begin() as connection:
            connection.execute(
                insert(agent_action_drafts).values(
                    draft_id=draft.draft_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    action_type=draft.action_type,
                    target_code=draft.target_code,
                    display_name=draft.display_name,
                    category=draft.category,
                    risk_level=draft.risk_level,
                    route=draft.route,
                    objective=draft.objective,
                    artifact_name=draft.artifact_name,
                    required_inputs=list(draft.required_inputs),
                    stages=list(draft.stages),
                    approval_required=draft.approval_required,
                    status=draft.status,
                    created_at=now,
                    input_schema=list(draft.input_schema),
                    input_values=draft.input_values,
                )
            )
        return draft

    def update_action_inputs(
        self,
        *,
        draft_id: str,
        user_id: int,
        input_values: dict[str, object],
    ) -> AgentActionDraft:
        self.get_action_draft(draft_id, user_id)
        with self._database.begin() as connection:
            connection.execute(
                update(agent_action_drafts)
                .where(agent_action_drafts.c.draft_id == draft_id)
                .values(
                    input_values=input_values,
                    preflight_status=None,
                    preflight_checks=[],
                    preflight_at=None,
                )
            )
        return self.get_action_draft(draft_id, user_id)

    def list_action_drafts(
        self,
        conversation_id: str,
        user_id: int,
    ) -> tuple[AgentActionDraft, ...]:
        self._require_conversation(conversation_id, user_id)
        with self._database.connect() as connection:
            rows = connection.execute(
                select(agent_action_drafts)
                .where(agent_action_drafts.c.conversation_id == conversation_id)
                .order_by(
                    agent_action_drafts.c.created_at,
                    agent_action_drafts.c.draft_id,
                )
            ).mappings().all()
        return tuple(self._action_draft(row) for row in rows)

    def get_action_draft(
        self,
        draft_id: str,
        user_id: int,
    ) -> AgentActionDraft:
        with self._database.connect() as connection:
            row = connection.execute(
                select(agent_action_drafts)
                .join(
                    agent_conversations,
                    agent_conversations.c.conversation_id
                    == agent_action_drafts.c.conversation_id,
                )
                .where(
                    agent_action_drafts.c.draft_id == str(draft_id).strip(),
                    agent_conversations.c.user_id == user_id,
                )
            ).mappings().one_or_none()
        if row is None:
            raise AgentConversationError("Agent action draft was not found.")
        return self._action_draft(row)

    def record_action_preflight(
        self,
        *,
        draft_id: str,
        user_id: int,
        status: str,
        checks: tuple[AgentDraftCheck, ...],
    ) -> AgentActionDraft:
        self.get_action_draft(draft_id, user_id)
        serialized = [
            {
                "code": item.code,
                "label": item.label,
                "status": item.status,
                "message": item.message,
            }
            for item in checks
        ]
        with self._database.begin() as connection:
            connection.execute(
                update(agent_action_drafts)
                .where(agent_action_drafts.c.draft_id == draft_id)
                .values(
                    preflight_status=status,
                    preflight_checks=serialized,
                    preflight_at=datetime.now(UTC),
                )
            )
        return self.get_action_draft(draft_id, user_id)

    def reserve_action_decision(
        self,
        *,
        request_id: str,
        conversation_id: str,
        user_id: int,
        username: str,
        operation_code: str,
        artifact_name: str | None,
        decision: str,
        payload_checksum: str,
        payload_snapshot: dict[str, object],
    ) -> tuple[AgentActionDecision, bool]:
        """Claim one approval interrupt before any Oracle submission."""
        self._require_conversation(conversation_id, user_id)
        now = datetime.now(UTC)
        decision_id = str(uuid4())
        try:
            with self._database.begin() as connection:
                connection.execute(
                    insert(agent_action_decisions).values(
                        decision_id=decision_id,
                        request_id=request_id,
                        conversation_id=conversation_id,
                        actor_user_id=user_id,
                        actor_username=username,
                        operation_code=operation_code,
                        artifact_name=artifact_name,
                        decision=decision,
                        payload_checksum=payload_checksum,
                        payload_snapshot=payload_snapshot,
                        outcome_status="PROCESSING",
                        decided_at=now,
                    )
                )
        except IntegrityError:
            existing = self.get_action_decision(
                request_id=request_id,
                user_id=user_id,
                conversation_id=conversation_id,
            )
            if existing is None:
                raise AgentConversationError(
                    "This approval request was already claimed by another "
                    "conversation or user."
                )
            return existing, False
        created = self.get_action_decision(
            request_id=request_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if created is None:
            raise AgentConversationError(
                "Agent approval decision could not be reserved."
            )
        return created, True

    def finalize_action_decision(
        self,
        *,
        request_id: str,
        user_id: int,
        conversation_id: str,
        outcome_status: str,
        execution_id: str | None = None,
        failure_summary: str | None = None,
    ) -> AgentActionDecision:
        """Finalize technical outcome without changing the human decision."""
        allowed = {"SUBMITTED", "APPROVED", "REJECTED", "FAILED"}
        if outcome_status not in allowed:
            raise AgentConversationError(
                "Agent approval outcome is invalid."
            )
        existing = self.get_action_decision(
            request_id=request_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if existing is None:
            raise AgentConversationError(
                "Agent approval decision was not found."
            )
        if existing.outcome_status != "PROCESSING":
            return existing
        with self._database.begin() as connection:
            connection.execute(
                update(agent_action_decisions)
                .where(
                    agent_action_decisions.c.request_id == request_id,
                    agent_action_decisions.c.actor_user_id == user_id,
                    agent_action_decisions.c.conversation_id
                    == conversation_id,
                    agent_action_decisions.c.outcome_status == "PROCESSING",
                )
                .values(
                    outcome_status=outcome_status,
                    execution_id=execution_id,
                    failure_summary=(failure_summary or "")[:2000] or None,
                    finalized_at=datetime.now(UTC),
                )
            )
        finalized = self.get_action_decision(
            request_id=request_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if finalized is None:
            raise AgentConversationError(
                "Agent approval decision could not be finalized."
            )
        return finalized

    def get_action_decision(
        self,
        *,
        request_id: str,
        user_id: int,
        conversation_id: str,
    ) -> AgentActionDecision | None:
        with self._database.connect() as connection:
            row = connection.execute(
                select(agent_action_decisions).where(
                    agent_action_decisions.c.request_id
                    == str(request_id).strip(),
                    agent_action_decisions.c.actor_user_id == user_id,
                    agent_action_decisions.c.conversation_id
                    == str(conversation_id).strip(),
                )
            ).mappings().one_or_none()
        return self._action_decision(row) if row is not None else None

    def list_action_decisions(
        self,
        conversation_id: str,
        user_id: int,
    ) -> tuple[AgentActionDecision, ...]:
        self._require_conversation(conversation_id, user_id)
        with self._database.connect() as connection:
            rows = connection.execute(
                select(agent_action_decisions)
                .where(
                    agent_action_decisions.c.conversation_id
                    == conversation_id,
                    agent_action_decisions.c.actor_user_id == user_id,
                )
                .order_by(agent_action_decisions.c.decided_at)
            ).mappings().all()
        return tuple(self._action_decision(row) for row in rows)

    def delete_conversation(self, conversation_id: str, user_id: int) -> bool:
        self._require_conversation(conversation_id, user_id)
        with self._database.begin() as connection:
            result = connection.execute(
                delete(agent_conversations).where(
                    agent_conversations.c.conversation_id == conversation_id,
                    agent_conversations.c.user_id == user_id,
                )
            )
        return result.rowcount > 0

    def _require_conversation(
        self,
        conversation_id: str,
        user_id: int,
    ) -> AgentConversation:
        conversation = self.get_conversation(conversation_id, user_id)
        if conversation is None:
            raise AgentConversationError("Agent conversation was not found.")
        return conversation

    @staticmethod
    def _conversation(row) -> AgentConversation:
        return AgentConversation(
            conversation_id=str(row["conversation_id"]),
            user_id=int(row["user_id"]),
            title=str(row["title"]),
            provider=str(row["provider"]),
            model=str(row["model"]),
            created_at=utc_datetime(row["created_at"]),
            updated_at=utc_datetime(row["updated_at"]),
        )

    @staticmethod
    def _message(row) -> AgentMessage:
        return AgentMessage(
            message_id=int(row["message_id"]),
            conversation_id=str(row["conversation_id"]),
            role=AgentMessageRole(str(row["role"])),
            content=str(row["content"]),
            created_at=utc_datetime(row["created_at"]),
        )

    @staticmethod
    def _action_draft(row) -> AgentActionDraft:
        raw_checks = list(row["preflight_checks"] or [])
        return AgentActionDraft(
            draft_id=str(row["draft_id"]),
            conversation_id=str(row["conversation_id"]),
            message_id=int(row["message_id"]),
            action_type=str(row["action_type"]),
            target_code=str(row["target_code"]),
            display_name=str(row["display_name"]),
            category=str(row["category"]),
            risk_level=str(row["risk_level"]),
            route=str(row["route"]),
            objective=str(row["objective"]),
            artifact_name=(
                str(row["artifact_name"])
                if row["artifact_name"] is not None
                else None
            ),
            required_inputs=tuple(str(item) for item in row["required_inputs"]),
            stages=tuple(str(item) for item in row["stages"]),
            approval_required=bool(row["approval_required"]),
            status=str(row["status"]),
            created_at=utc_datetime(row["created_at"]),
            input_schema=tuple(
                dict(item)
                for item in (row["input_schema"] or [])
                if isinstance(item, dict)
            ),
            input_values=dict(row["input_values"] or {}),
            preflight_status=(
                str(row["preflight_status"])
                if row["preflight_status"] is not None
                else None
            ),
            preflight_checks=tuple(
                AgentDraftCheck(
                    code=str(item.get("code", "")),
                    label=str(item.get("label", "")),
                    status=str(item.get("status", "")),
                    message=str(item.get("message", "")),
                )
                for item in raw_checks
                if isinstance(item, dict)
            ),
            preflight_at=utc_datetime(row["preflight_at"]),
        )

    @staticmethod
    def _action_decision(row) -> AgentActionDecision:
        return AgentActionDecision(
            decision_id=str(row["decision_id"]),
            request_id=str(row["request_id"]),
            conversation_id=str(row["conversation_id"]),
            actor_user_id=int(row["actor_user_id"]),
            actor_username=str(row["actor_username"]),
            operation_code=str(row["operation_code"]),
            artifact_name=(
                str(row["artifact_name"])
                if row["artifact_name"] is not None
                else None
            ),
            decision=str(row["decision"]),
            payload_checksum=str(row["payload_checksum"]),
            payload_snapshot=dict(row["payload_snapshot"] or {}),
            outcome_status=str(row["outcome_status"]),
            execution_id=(
                str(row["execution_id"])
                if row["execution_id"] is not None
                else None
            ),
            failure_summary=row["failure_summary"],
            decided_at=utc_datetime(row["decided_at"]),
            finalized_at=utc_datetime(row["finalized_at"]),
        )

    @staticmethod
    def _title(content: str) -> str:
        compact = " ".join(content.split())
        return compact[:57] + ("..." if len(compact) > 57 else "")


SQLiteAgentRepository = SQLAgentRepository

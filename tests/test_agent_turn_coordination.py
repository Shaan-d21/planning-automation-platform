"""Regression coverage for durable, idempotent agent turn coordination."""

from __future__ import annotations

from uuid import uuid4
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from app.agent.models import (
    AgentMessageRole,
    AgentProviderResult,
    AgentTurnStatus,
)
from app.agent.repository import SQLAgentRepository
from app.agent.service import AgentApplicationService
from app.config.settings import Settings
from app.services.access_control_service import AccessControlService
from app.utils.exceptions import AgentConversationError


def _repository(tmp_path):
    database = tmp_path / "agent-turns.sqlite3"
    administrator = AccessControlService(database).bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLAgentRepository(database)
    conversation = repository.create_conversation(
        user_id=administrator.user_id,
        provider="fake",
        model="fake-model",
    )
    return repository, administrator, conversation


def test_turn_reservation_is_idempotent_and_locks_one_conversation(
    tmp_path,
) -> None:
    repository, user, conversation = _repository(tmp_path)
    client_message_id = str(uuid4())

    first, reserved = repository.reserve_turn(
        conversation_id=conversation.conversation_id,
        user_id=user.user_id,
        client_message_id=client_message_id,
    )
    replay, replay_reserved = repository.reserve_turn(
        conversation_id=conversation.conversation_id,
        user_id=user.user_id,
        client_message_id=client_message_id,
    )

    assert reserved is True
    assert replay_reserved is False
    assert replay.turn_id == first.turn_id == client_message_id
    with pytest.raises(AgentConversationError, match="already being prepared"):
        repository.reserve_turn(
            conversation_id=conversation.conversation_id,
            user_id=user.user_id,
            client_message_id=str(uuid4()),
        )


def test_cancelled_turn_releases_lock_for_next_message(tmp_path) -> None:
    repository, user, conversation = _repository(tmp_path)
    first_id = str(uuid4())
    first, _ = repository.reserve_turn(
        conversation_id=conversation.conversation_id,
        user_id=user.user_id,
        client_message_id=first_id,
    )
    user_message = repository.add_message(
        conversation_id=conversation.conversation_id,
        user_id=user.user_id,
        role=AgentMessageRole.USER,
        content="Run the calculation.",
    )
    repository.attach_turn_user_message(
        turn_id=first.turn_id,
        user_id=user.user_id,
        message_id=user_message.message_id,
    )

    requested = repository.request_turn_cancellation(
        conversation_id=conversation.conversation_id,
        user_id=user.user_id,
        turn_id=first.turn_id,
    )
    assert requested.status is AgentTurnStatus.CANCEL_REQUESTED
    assert repository.turn_cancellation_requested(
        conversation_id=conversation.conversation_id,
        user_id=user.user_id,
        turn_id=first.turn_id,
    )
    cancelled = repository.finish_turn(
        conversation_id=conversation.conversation_id,
        user_id=user.user_id,
        turn_id=first.turn_id,
        status=AgentTurnStatus.CANCELLED,
    )
    assert cancelled.status is AgentTurnStatus.CANCELLED
    assert repository.active_turn(
        conversation.conversation_id, user.user_id
    ) is None

    second, reserved = repository.reserve_turn(
        conversation_id=conversation.conversation_id,
        user_id=user.user_id,
        client_message_id=str(uuid4()),
    )
    assert reserved is True
    assert second.status is AgentTurnStatus.RUNNING


def test_completed_turn_retains_assistant_message_for_replay(tmp_path) -> None:
    repository, user, conversation = _repository(tmp_path)
    turn, _ = repository.reserve_turn(
        conversation_id=conversation.conversation_id,
        user_id=user.user_id,
        client_message_id=str(uuid4()),
    )
    assistant = repository.add_message(
        conversation_id=conversation.conversation_id,
        user_id=user.user_id,
        role=AgentMessageRole.ASSISTANT,
        content="The response is ready.",
    )

    completed = repository.finish_turn(
        conversation_id=conversation.conversation_id,
        user_id=user.user_id,
        turn_id=turn.turn_id,
        status=AgentTurnStatus.COMPLETED,
        assistant_message_id=assistant.message_id,
    )

    assert completed.status is AgentTurnStatus.COMPLETED
    assert completed.assistant_message_id == assistant.message_id
    recovered = repository.get_message(
        conversation_id=conversation.conversation_id,
        user_id=user.user_id,
        message_id=completed.assistant_message_id,
    )
    assert recovered == assistant


def test_service_discards_graph_result_after_cooperative_cancellation(
    tmp_path,
) -> None:
    database = tmp_path / "cancelled-service-turn.sqlite3"
    user = AccessControlService(database).bootstrap_administrator(
        username="admin",
        display_name="Administrator",
        email=None,
        password="Strong password 123!",
    )
    repository = SQLAgentRepository(database)
    conversation = repository.create_conversation(
        user_id=user.user_id,
        provider="fake",
        model="fake-model",
    )
    started = Event()
    release = Event()

    class BlockingGraph:
        deleted = False

        def pending_approval(self, **_kwargs):
            return None

        def pending_clarification(self, **_kwargs):
            return None

        def pending_input(self, **_kwargs):
            return None

        def current_task_context(self, **_kwargs):
            return None

        def invoke(self, **_kwargs):
            started.set()
            assert release.wait(timeout=5)
            return AgentProviderResult(text="This result must be discarded.")

        def delete_thread(self, **_kwargs):
            self.deleted = True

    graph = BlockingGraph()
    settings = Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="service-user",
        epm_password="secret",
        application_name="Vision",
        workflow_database_file=database,
        gemini_api_key="test-key",
    )
    service = AgentApplicationService(
        settings,
        gateway=SimpleNamespace(),
        repository=repository,
        graph_orchestrator=graph,
    )
    turn_id = str(uuid4())
    result: dict[str, object] = {}
    errors: list[BaseException] = []

    def run_turn() -> None:
        try:
            result.update(
                service.send_message(
                    conversation_id=conversation.conversation_id,
                    user=user,
                    content="Run Aggregate Plan rule",
                    client_message_id=turn_id,
                )
            )
        except BaseException as exc:  # surfaced on the test thread below
            errors.append(exc)

    worker = Thread(target=run_turn)
    worker.start()
    assert started.wait(timeout=5), errors
    requested = service.cancel_turn(
        conversation.conversation_id, turn_id, user
    )
    assert requested.status is AgentTurnStatus.CANCEL_REQUESTED
    release.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert result["turn"].status is AgentTurnStatus.CANCELLED
    assert "cancelled before completion" in result["message"].content
    assert graph.deleted is True
    contents = [
        item.content
        for item in repository.list_messages(
            conversation.conversation_id, user.user_id
        )
    ]
    assert "This result must be discarded." not in contents

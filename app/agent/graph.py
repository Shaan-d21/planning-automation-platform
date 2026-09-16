"""LangGraph orchestration for the governed Oracle EPM assistant."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.agent.capabilities import (
    AgentCapabilityGateway,
    PIPELINE_SCHEDULE_ACTIONS,
    PIPELINE_SCHEDULE_CREATE,
    PIPELINE_SCHEDULE_PAUSE,
    PIPELINE_SCHEDULE_RESUME,
)
from app.agent.checkpoints import AgentCheckpointStore
from app.agent.models import (
    AgentMessage,
    AgentMessageRole,
    AgentApprovalRequest,
    AgentClarificationRequest,
    AgentInputRequest,
    AgentProviderResult,
    AgentProviderTurn,
    AgentToolActivity,
    AgentToolCall,
    AgentToolDefinition,
)
from app.agent.provider import AgentProvider
from app.agent.rule_matching import BusinessRuleMatch, recommend_artifacts
from app.utils.exceptions import AgentProviderError


ProviderFactory = Callable[[], AgentProvider]

# The retired custom process builder is intentionally absent. The agent may
# inspect the platform and prepare standalone governed operations only.
GRAPH_TOOL_NAMES = frozenset(
    {
        "get_environment_summary",
        "list_platform_operations",
        "get_recent_execution_history",
        "get_execution_evidence",
        "list_planning_cubes",
        "list_cube_dimensions",
        "search_dimension_members",
        "list_data_explorer_views",
        "review_saved_data_view",
        "review_data_slice",
        "compare_data_slices",
        "list_operation_artifacts",
        "plan_multi_step_request",
        "prepare_operation_action",
        "prepare_standalone_flow_action",
        "prepare_schedule_action",
    }
)

RESOLVED_ARTIFACT_REQUIRED_CODES = frozenset(
    {
        "pipelines",
        "data-import",
        "metadata-import",
        "cube-refresh",
        "substitution-variables",
        "user-variables",
        PIPELINE_SCHEDULE_CREATE,
        PIPELINE_SCHEDULE_PAUSE,
        PIPELINE_SCHEDULE_RESUME,
    }
)

# Exact Oracle artifact names frequently carry the operation meaning without
# repeating words such as "rule" or "integration" in every clause. These are
# the operation catalogs that can safely participate in a governed standalone
# sequence after their exact artifacts and inputs have been reviewed.
MULTI_STEP_ARTIFACT_OPERATION_CODES = (
    "pipelines",
    "data-integrations",
    "data-import",
    "metadata-import",
    "business-rules",
    "data-maps",
    "cube-refresh",
)


class AgentGraphState(TypedDict, total=False):
    """Checkpoint-safe state. Values intentionally remain JSON-like."""

    schema_version: int
    messages: list[dict[str, Any]]
    provider_exchange: list[dict[str, Any]]
    pending_tool_calls: list[dict[str, Any]]
    tool_activity: list[dict[str, Any]]
    assistant_text: str | None
    tool_rounds: int
    allowed_tool_names: list[str]
    approval_decision: str | None
    artifact_catalog_snapshot: dict[str, Any] | None
    deterministic_operation: str | None
    data_review_context: dict[str, Any] | None
    task_context: dict[str, Any] | None


class AgentGraphOrchestrator:
    """Compile and run the deterministic model -> tools -> model graph."""

    STATE_SCHEMA_VERSION = 5

    def __init__(
        self,
        *,
        provider_factory: ProviderFactory,
        gateway: AgentCapabilityGateway,
        checkpointer: AgentCheckpointStore,
        system_instruction: str,
        max_tool_rounds: int,
        environment_key: str,
        logger: logging.Logger | None = None,
    ) -> None:
        self._provider_factory = provider_factory
        self._gateway = gateway
        self._checkpoints = checkpointer
        self._system_instruction = system_instruction
        self._max_tool_rounds = max_tool_rounds
        self._environment_key = hashlib.sha256(
            environment_key.encode("utf-8")
        ).hexdigest()[:16]
        self._logger = logger or logging.getLogger(__name__)
        self._provider: AgentProvider | None = None
        self._provider_lock = threading.Lock()
        self._graph: Any | None = None
        self._graph_lock = threading.Lock()

    def invoke(
        self,
        *,
        conversation_id: str,
        user_id: int,
        messages: Sequence[AgentMessage],
        allowed_tool_names: Sequence[str] | None = None,
        data_review_context: dict[str, Any] | None = None,
        task_context: dict[str, Any] | None = None,
    ) -> AgentProviderResult:
        """Run one auditable assistant turn under an isolated thread ID."""
        initial: AgentGraphState = {
            "schema_version": self.STATE_SCHEMA_VERSION,
            "messages": [self._message_payload(message) for message in messages],
            "provider_exchange": [],
            "pending_tool_calls": [],
            "tool_activity": [],
            "assistant_text": None,
            "tool_rounds": 0,
            "deterministic_operation": None,
            "allowed_tool_names": sorted(
                set(
                    GRAPH_TOOL_NAMES
                    if allowed_tool_names is None
                    else allowed_tool_names
                )
                & GRAPH_TOOL_NAMES
            ),
            "approval_decision": None,
            "data_review_context": data_review_context,
            "task_context": task_context,
        }
        try:
            state = self._compiled_graph().invoke(
                initial,
                config=self._config(conversation_id, user_id),
            )
        except AgentProviderError:
            raise
        except Exception as exc:
            self._logger.exception("LangGraph assistant turn failed.")
            raise AgentProviderError(
                "The EPM Assistant workflow could not complete. Review the "
                "server log and try again."
            ) from exc
        clarification = self._clarification_from_result(state)
        if clarification is not None:
            return AgentProviderResult(
                text="",
                clarification_request=clarification,
            )
        input_request = self._input_from_result(state)
        if input_request is not None:
            return AgentProviderResult(text="", input_request=input_request)
        approval = self._approval_from_result(state)
        if approval is not None:
            return AgentProviderResult(text="", approval_request=approval)
        text = str(state.get("assistant_text") or "").strip()
        if not text:
            raise AgentProviderError(
                "The EPM Assistant completed without a response. Try again."
            )
        return AgentProviderResult(
            text=text,
            tool_activity=tuple(
                self._activity_from_payload(item)
                for item in state.get("tool_activity", [])
            ),
        )

    def pending_approval(
        self,
        *,
        conversation_id: str,
        user_id: int,
    ) -> AgentApprovalRequest | None:
        """Return the current durable interrupt without advancing the graph."""
        snapshot = self._compiled_graph().get_state(
            self._config(conversation_id, user_id)
        )
        for task in snapshot.tasks:
            for pending in task.interrupts:
                approval = self._approval_from_interrupt(
                    pending.id,
                    pending.value,
                )
                if approval is not None:
                    return approval
        return None

    def pending_clarification(
        self,
        *,
        conversation_id: str,
        user_id: int,
    ) -> AgentClarificationRequest | None:
        """Return a durable structured choice without advancing the graph."""
        snapshot = self._compiled_graph().get_state(
            self._config(conversation_id, user_id)
        )
        for task in snapshot.tasks:
            for pending in task.interrupts:
                clarification = self._clarification_from_interrupt(
                    pending.id,
                    pending.value,
                )
                if clarification is not None:
                    return clarification
        return None

    def resume_clarification(
        self,
        *,
        conversation_id: str,
        user_id: int,
        request_id: str,
        value: str | None,
    ) -> AgentProviderResult:
        """Resume one artifact choice and advance to approval or cancellation."""
        pending = self.pending_clarification(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if pending is None:
            raise AgentProviderError("No agent clarification is awaiting a response.")
        if pending.request_id != request_id:
            raise AgentProviderError(
                "This agent clarification is stale. Refresh the conversation."
            )
        normalized = str(value or "").strip()
        try:
            state = self._compiled_graph().invoke(
                Command(resume={"value": normalized}),
                config=self._config(conversation_id, user_id),
            )
        except AgentProviderError:
            raise
        except Exception as exc:
            self._logger.exception("LangGraph clarification resume failed.")
            raise AgentProviderError(
                "The EPM Assistant could not apply this selection. Refresh "
                "the conversation and try again."
            ) from exc
        next_clarification = self._clarification_from_result(state)
        if next_clarification is not None:
            return AgentProviderResult(
                text="",
                clarification_request=next_clarification,
            )
        next_input = self._input_from_result(state)
        if next_input is not None:
            return AgentProviderResult(text="", input_request=next_input)
        next_approval = self._approval_from_result(state)
        if next_approval is not None:
            return AgentProviderResult(text="", approval_request=next_approval)
        return self._completed_result(state)

    def pending_input(
        self,
        *,
        conversation_id: str,
        user_id: int,
    ) -> AgentInputRequest | None:
        """Return a durable operation-input request without advancing it."""
        snapshot = self._compiled_graph().get_state(
            self._config(conversation_id, user_id)
        )
        for task in snapshot.tasks:
            for pending in task.interrupts:
                request = self._input_from_interrupt(pending.id, pending.value)
                if request is not None:
                    return request
        return None

    def resume_input(
        self,
        *,
        conversation_id: str,
        user_id: int,
        request_id: str,
        values: dict[str, Any] | None,
    ) -> AgentProviderResult:
        """Resume one structured-input interrupt after deterministic validation."""
        pending = self.pending_input(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if pending is None:
            raise AgentProviderError("No agent input request is awaiting a response.")
        if pending.request_id != request_id:
            raise AgentProviderError(
                "This agent input request is stale. Refresh the conversation."
            )
        try:
            state = self._compiled_graph().invoke(
                Command(resume={"values": values}),
                config=self._config(conversation_id, user_id),
            )
        except AgentProviderError:
            raise
        except Exception as exc:
            self._logger.exception("LangGraph operation input resume failed.")
            raise AgentProviderError(
                "The EPM Assistant could not apply these inputs. Review them "
                "and try again."
            ) from exc
        next_input = self._input_from_result(state)
        if next_input is not None:
            return AgentProviderResult(text="", input_request=next_input)
        next_clarification = self._clarification_from_result(state)
        if next_clarification is not None:
            return AgentProviderResult(
                text="",
                clarification_request=next_clarification,
            )
        next_approval = self._approval_from_result(state)
        if next_approval is not None:
            return AgentProviderResult(text="", approval_request=next_approval)
        return self._completed_result(state)

    def resume_approval(
        self,
        *,
        conversation_id: str,
        user_id: int,
        request_id: str,
        decision: str,
    ) -> AgentProviderResult:
        """Resume exactly one owned approval interrupt."""
        pending = self.pending_approval(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if pending is None:
            raise AgentProviderError("No agent approval is awaiting a decision.")
        if pending.request_id != request_id:
            raise AgentProviderError(
                "This agent approval is stale. Refresh the conversation."
            )
        normalized = decision.strip().casefold()
        if normalized not in {"approve", "reject"}:
            raise AgentProviderError("Approval decision must be approve or reject.")
        try:
            state = self._compiled_graph().invoke(
                Command(resume={"decision": normalized}),
                config=self._config(conversation_id, user_id),
            )
        except AgentProviderError:
            raise
        except Exception as exc:
            self._logger.exception("LangGraph approval resume failed.")
            raise AgentProviderError(
                "The EPM Assistant could not resume this approval. Refresh "
                "the conversation and try again."
            ) from exc
        next_clarification = self._clarification_from_result(state)
        if next_clarification is not None:
            return AgentProviderResult(
                text="",
                clarification_request=next_clarification,
            )
        next_input = self._input_from_result(state)
        if next_input is not None:
            return AgentProviderResult(text="", input_request=next_input)
        next_approval = self._approval_from_result(state)
        if next_approval is not None:
            return AgentProviderResult(text="", approval_request=next_approval)
        return self._completed_result(state)

    def shutdown(self) -> None:
        """Release checkpoint resources during application shutdown."""
        self._checkpoints.close()

    def _compiled_graph(self):
        if self._graph is not None:
            return self._graph
        with self._graph_lock:
            if self._graph is not None:
                return self._graph
            # Official LangGraph checkpoint migrations are idempotent. Running
            # them before the first compile keeps new deployments operable;
            # production releases may also run the explicit setup command.
            self._checkpoints.setup()
            builder = StateGraph(AgentGraphState)
            builder.add_node("model", self._model_node)
            builder.add_node("approval", self._approval_node)
            builder.add_node("tools", self._tool_node)
            builder.add_edge(START, "model")
            builder.add_conditional_edges(
                "model",
                self._route_after_model,
                {"approval": "approval", "tools": "tools", "end": END},
            )
            builder.add_edge("approval", "tools")
            builder.add_edge("tools", "model")
            self._graph = builder.compile(checkpointer=self._checkpoints.get())
            return self._graph

    def _model_node(self, state: AgentGraphState) -> AgentGraphState:
        deterministic_operation = str(
            state.get("deterministic_operation") or ""
        ).strip().casefold()
        if deterministic_operation and state.get("tool_activity"):
            return {
                **state,
                "assistant_text": self._deterministic_completion_text(
                    deterministic_operation,
                    state.get("tool_activity", []),
                ),
                "pending_tool_calls": [],
            }
        task_response = self._deterministic_task_response(state)
        if task_response is not None:
            return {
                **state,
                "assistant_text": task_response,
                "pending_tool_calls": [],
            }
        deterministic_call = (
            self._deterministic_task_operation_call(state)
            or self._deterministic_execution_evidence_call(state)
            or self._deterministic_saved_data_view_call(state)
            or self._deterministic_data_explorer_views_call(state)
            or self._deterministic_data_review_slice_call(state)
            or self._deterministic_data_review_cube_call(state)
            or self._deterministic_schedule_call(state)
            or self._deterministic_standalone_flow_call(state)
            or self._deterministic_multi_step_plan_call(state)
            or self._deterministic_pipeline_call(state)
            or self._deterministic_business_rule_call(state)
            or self._deterministic_data_integration_call(state)
            or self._deterministic_data_map_call(state)
            or self._deterministic_metadata_import_call(state)
            or self._deterministic_variable_call(state)
        )
        if deterministic_call is not None:
            deterministic_operation = {
                "get_execution_evidence": "execution-evidence",
                "list_data_explorer_views": "data-explorer-views",
                "review_saved_data_view": "saved-data-view",
                "list_cube_dimensions": "data-review-dimensions",
                "review_data_slice": "data-review-slice",
                "plan_multi_step_request": "multi-step-plan",
                "prepare_standalone_flow_action": "standalone-flow",
                "prepare_schedule_action": {
                    "CREATE": PIPELINE_SCHEDULE_CREATE,
                    "PAUSE": PIPELINE_SCHEDULE_PAUSE,
                    "RESUME": PIPELINE_SCHEDULE_RESUME,
                }.get(
                    str(deterministic_call.arguments.get("action") or "").upper(),
                    PIPELINE_SCHEDULE_CREATE,
                ),
            }.get(
                deterministic_call.name,
                str(
                    deterministic_call.arguments.get("operation_code") or ""
                ).strip().casefold(),
            )
            return {
                **state,
                "assistant_text": None,
                "pending_tool_calls": [
                    self._call_payload(deterministic_call)
                ],
                "artifact_catalog_snapshot": self._artifact_catalog_snapshot(
                    (deterministic_call,)
                ),
                "deterministic_operation": deterministic_operation,
            }
        provider = self._get_provider()
        messages = tuple(
            self._message_from_payload(item) for item in state["messages"]
        )
        tools = self._tool_definitions(state)
        generate = getattr(provider, "generate", None)
        if not callable(generate):
            # Compatibility bridge for test doubles and emergency rollback.
            result = provider.respond(
                messages=messages,
                system_instruction=self._instruction_for_state(state),
                tools=tools,
                execute_tool=lambda call: self._execute_allowed_tool(call, state),
            )
            return {
                **state,
                "assistant_text": result.text,
                "pending_tool_calls": [],
                "tool_activity": [
                    self._activity_payload(item) for item in result.tool_activity
                ],
            }
        turn: AgentProviderTurn = generate(
            messages=messages,
            system_instruction=self._instruction_for_state(state),
            tools=tools,
            provider_exchange=state.get("provider_exchange", []),
        )
        if turn.tool_calls:
            if state.get("tool_rounds", 0) >= self._max_tool_rounds:
                return {
                    **state,
                    "assistant_text": (
                        "I reached the safe information-gathering limit for "
                        "this request. Please narrow the question and try again."
                    ),
                    "pending_tool_calls": [],
                }
            exchange = list(state.get("provider_exchange", []))
            if turn.provider_content is None:
                raise AgentProviderError(
                    "The model omitted provider state required for a safe "
                    "tool continuation."
                )
            exchange.append(turn.provider_content)
            catalog_snapshot = self._artifact_catalog_snapshot(
                turn.tool_calls
            )
            return {
                **state,
                "assistant_text": None,
                "provider_exchange": exchange,
                "pending_tool_calls": [
                    self._call_payload(call) for call in turn.tool_calls
                ],
                "artifact_catalog_snapshot": catalog_snapshot,
            }
        return {
            **state,
            "assistant_text": str(turn.text or "").strip() or None,
            "pending_tool_calls": [],
        }

    def _tool_node(self, state: AgentGraphState) -> AgentGraphState:
        calls = tuple(
            self._call_from_payload(item)
            for item in state.get("pending_tool_calls", [])
        )
        activities: list[AgentToolActivity] = []
        for call in calls:
            if (
                call.name in {
                    "prepare_operation_action",
                    "prepare_standalone_flow_action",
                    "prepare_schedule_action",
                }
                and state.get("approval_decision") != "approve"
            ):
                result = {
                    "error": "The user rejected this governed action preparation."
                }
                status = "CANCELLED"
                activities.append(
                    AgentToolActivity(
                        name=call.name,
                        arguments=call.arguments,
                        status=status,
                        summary=str(result["error"]),
                        result=result,
                    )
                )
                continue
            try:
                result = self._execute_allowed_tool(call, state)
            except Exception as exc:
                self._logger.warning("Agent tool '%s' failed: %s", call.name, exc)
                result = {"error": str(exc)}
                status = "FAILED"
            else:
                status = "SUCCESS"
            activities.append(
                AgentToolActivity(
                    name=call.name,
                    arguments=call.arguments,
                    status=status,
                    summary=self._tool_summary(result),
                    result=result,
                )
            )
        activity_payloads = [
            *state.get("tool_activity", []),
            *(self._activity_payload(item) for item in activities),
        ]
        if state.get("deterministic_operation"):
            return {
                **state,
                "pending_tool_calls": [],
                "tool_activity": activity_payloads,
                "tool_rounds": state.get("tool_rounds", 0) + 1,
                "approval_decision": None,
            }
        provider = self._get_provider()
        build_response = getattr(provider, "tool_response", None)
        if not callable(build_response):
            raise AgentProviderError(
                "The configured provider cannot continue a graph tool turn."
            )
        exchange = list(state.get("provider_exchange", []))
        exchange.append(build_response(calls=calls, activities=activities))
        return {
            **state,
            "provider_exchange": exchange,
            "pending_tool_calls": [],
            "tool_activity": activity_payloads,
            "tool_rounds": state.get("tool_rounds", 0) + 1,
            "approval_decision": None,
        }

    def _instruction_for_state(self, state: AgentGraphState) -> str:
        """Add validated task and Data Explorer context to model instructions."""
        sections = [self._system_instruction]
        task_context = state.get("task_context")
        if isinstance(task_context, dict) and task_context:
            serialized_task = json.dumps(
                task_context,
                ensure_ascii=True,
                separators=(",", ":"),
            )[:6_000]
            sections.append(
                "Current deterministic business-task context:\n"
                f"{serialized_task}\n"
                "Preserve these collected parameters across short follow-up "
                "answers and corrections. Ask only for the first missing "
                "parameter. Never treat a value as Oracle-verified until a "
                "platform discovery or preparation tool validates it."
            )
        context = state.get("data_review_context")
        if isinstance(context, dict) and context:
            serialized = json.dumps(
                context,
                ensure_ascii=True,
                separators=(",", ":"),
            )[:6_000]
            sections.append(
                "Current tool-validated Data Explorer context:\n"
                f"{serialized}\n"
                "For a follow-up Data Explorer request, preserve every prior "
                "cube, POV, row, column, and member selection except fields "
                "the user explicitly changes. Use review_data_slice or "
                "compare_data_slices with the complete updated selection. If "
                "a requested change is ambiguous, ask one concise clarification "
                "question. This context contains selections only, never "
                "financial values."
            )
        return "\n\n".join(sections)

    @staticmethod
    def _deterministic_task_response(
        state: AgentGraphState,
    ) -> str | None:
        """Return safety-critical task clarification without provider variance."""
        context = state.get("task_context")
        if not isinstance(context, dict):
            return None
        intent = str(context.get("intent") or "").strip().upper()
        phase = str(context.get("phase") or "").strip().upper()
        if intent == "CANCEL_OPERATION":
            return (
                "I stopped planning the current conversational task. No new "
                "Oracle operation was submitted. If an Oracle job was already "
                "submitted, it may continue unless its operation supports "
                "cancellation."
            )
        if phase != "COLLECTING_INFORMATION":
            return None
        if intent not in {
            "MONTH_CLOSE",
            "METADATA_LOAD",
            "DATA_LOAD",
            "FORECAST_SEEDING",
            "VARIANCE_REPORTING",
        }:
            return None
        prompt = str(context.get("clarification_prompt") or "").strip()
        return prompt or None

    @staticmethod
    def _deterministic_task_operation_call(
        state: AgentGraphState,
    ) -> AgentToolCall | None:
        """Continue a clarified task into the existing governed operation flow."""
        context = state.get("task_context")
        if not isinstance(context, dict):
            return None
        if str(context.get("phase") or "").upper() != "READY_FOR_PLAN":
            return None
        allowed = {
            str(item).strip() for item in state.get("allowed_tool_names", [])
        }
        if "prepare_operation_action" not in allowed:
            return None
        operation_code = {
            "METADATA_LOAD": "metadata-import",
        }.get(str(context.get("intent") or "").strip().upper())
        if operation_code is None:
            return None
        return AgentToolCall(
            name="prepare_operation_action",
            arguments={
                "operation_code": operation_code,
                "objective": str(context.get("objective") or "").strip()
                or "Prepare the clarified Oracle EPM task.",
            },
            call_id=f"deterministic-task-{operation_code}",
        )

    @staticmethod
    def _route_after_model(
        state: AgentGraphState,
    ) -> Literal["approval", "tools", "end"]:
        calls = state.get("pending_tool_calls", [])
        if any(
            item.get("name") in {
                "prepare_operation_action",
                "prepare_standalone_flow_action",
                "prepare_schedule_action",
            }
            for item in calls
        ):
            return "approval"
        return "tools" if calls else "end"

    @classmethod
    def _deterministic_execution_evidence_call(
        cls,
        state: AgentGraphState,
    ) -> AgentToolCall | None:
        """Inspect retained evidence without relying on model tool choice."""
        allowed = {
            str(item).strip()
            for item in state.get("allowed_tool_names", [])
        }
        if "get_execution_evidence" not in allowed:
            return None
        user_messages = [
            str(item.get("content") or "").strip()
            for item in state.get("messages", [])
            if item.get("role") == AgentMessageRole.USER.value
        ]
        if not user_messages:
            return None
        latest = user_messages[-1]
        if not cls._is_execution_evidence_request(latest):
            return None

        execution_id = cls._execution_id_from_text(latest)
        arguments = (
            {"execution_id": execution_id}
            if execution_id
            else {
                "selector": (
                    "latest_failed"
                    if re.search(
                        r"\b(fail(?:ed|ure)?|error)\b",
                        latest,
                        re.IGNORECASE,
                    )
                    else "latest"
                )
            }
        )
        return AgentToolCall(
            name="get_execution_evidence",
            arguments=arguments,
            call_id="deterministic-execution-evidence",
        )

    @classmethod
    def _deterministic_saved_data_view_call(
        cls,
        state: AgentGraphState,
    ) -> AgentToolCall | None:
        """Load an exact saved-view choice emitted by the trusted UI card."""
        allowed = {
            str(item).strip()
            for item in state.get("allowed_tool_names", [])
        }
        if "review_saved_data_view" not in allowed:
            return None
        user_messages = [
            str(item.get("content") or "").strip()
            for item in state.get("messages", [])
            if item.get("role") == AgentMessageRole.USER.value
        ]
        if not user_messages:
            return None
        latest = user_messages[-1]
        prefix = "use saved data explorer view "
        suffix = " and load its current oracle data."
        lowered = latest.casefold()
        if not lowered.startswith(prefix) or not lowered.endswith(suffix):
            return None
        name = latest[len(prefix) : len(latest) - len(suffix)].strip()
        if not name:
            return None
        return AgentToolCall(
            name="review_saved_data_view",
            arguments={"name": name},
            call_id="deterministic-saved-data-view",
        )

    @staticmethod
    def _deterministic_data_explorer_views_call(
        state: AgentGraphState,
    ) -> AgentToolCall | None:
        """List saved Data Explorer layouts for an explicit saved-view request."""
        allowed = {
            str(item).strip()
            for item in state.get("allowed_tool_names", [])
        }
        if "list_data_explorer_views" not in allowed:
            return None
        user_messages = [
            str(item.get("content") or "").strip()
            for item in state.get("messages", [])
            if item.get("role") == AgentMessageRole.USER.value
        ]
        if not user_messages:
            return None
        latest = " ".join(user_messages[-1].casefold().split())
        if "saved" not in latest or "view" not in latest:
            return None
        if not any(word in latest for word in ("show", "list", "choose", "open", "review")):
            return None
        return AgentToolCall(
            name="list_data_explorer_views",
            arguments={},
            call_id="deterministic-data-explorer-views",
        )

    @classmethod
    def _deterministic_data_review_cube_call(
        cls,
        state: AgentGraphState,
    ) -> AgentToolCall | None:
        """Continue a guided Data Review directly from a selected live cube.

        Cube cards are a trusted UI selection, not an open-ended model request.
        Routing the selection here prevents the provider from rediscovering the
        cube catalog and guarantees that the next step inspects dimensions for
        the exact cube the user chose.
        """
        allowed = {
            str(item).strip()
            for item in state.get("allowed_tool_names", [])
        }
        if "list_cube_dimensions" not in allowed:
            return None
        user_messages = [
            str(item.get("content") or "").strip()
            for item in state.get("messages", [])
            if item.get("role") == AgentMessageRole.USER.value
        ]
        if not user_messages:
            return None
        latest = user_messages[-1]
        match = re.search(
            r"\buse\s+cube\s+[`'\"]?"
            r"([A-Za-z0-9][A-Za-z0-9_. -]{0,119}?)"
            r"[`'\"]?\s+for\s+(?:this\s+)?data\s+(?:review|explorer)\b",
            latest,
            re.IGNORECASE,
        )
        if match is None:
            return None
        cube = match.group(1).strip(" `'\".,;:?!")
        if not cube:
            return None
        return AgentToolCall(
            name="list_cube_dimensions",
            arguments={"cube": cube},
            call_id="deterministic-data-review-dimensions",
        )

    @classmethod
    def _deterministic_data_review_slice_call(
        cls,
        state: AgentGraphState,
    ) -> AgentToolCall | None:
        """Load a UI-validated exact slice without provider interpretation."""
        allowed = {
            str(item).strip()
            for item in state.get("allowed_tool_names", [])
        }
        if "review_data_slice" not in allowed:
            return None
        user_messages = [
            str(item.get("content") or "").strip()
            for item in state.get("messages", [])
            if item.get("role") == AgentMessageRole.USER.value
        ]
        if not user_messages:
            return None
        arguments = cls._exact_data_review_arguments(user_messages[-1])
        if arguments is None:
            return None
        return AgentToolCall(
            name="review_data_slice",
            arguments=arguments,
            call_id="deterministic-data-review-slice",
        )

    @staticmethod
    def _exact_data_review_arguments(text: str) -> dict[str, Any] | None:
        """Parse the exact mapping format emitted by the guided UI card."""
        lines = [
            line.strip()
            for line in str(text or "").replace("\r\n", "\n").split("\n")
            if line.strip()
        ]
        markers = {
            "run an exact data review using this validated layout.",
            "run an exact data explorer query using this validated layout.",
        }
        if not lines or lines[0].casefold() not in markers:
            return None

        cube = ""
        section: str | None = None
        pov: list[dict[str, str]] = []
        rows: list[dict[str, Any]] = []
        columns: list[dict[str, Any]] = []
        sections = {"pov:": "pov", "rows:": "rows", "columns:": "columns"}
        for line in lines[1:]:
            lowered = line.casefold()
            if lowered.startswith("cube:"):
                cube = line.split(":", 1)[1].strip()
                continue
            if lowered in sections:
                section = sections[lowered]
                continue
            if section is None or "=" not in line:
                return None
            dimension, raw_members = (
                value.strip() for value in line.split("=", 1)
            )
            if not dimension or not raw_members:
                return None
            if section == "pov":
                pov.append({"dimension": dimension, "member": raw_members})
                continue
            members = [
                member.strip()
                for member in raw_members.split("|")
                if member.strip()
            ]
            if not members:
                return None
            target = rows if section == "rows" else columns
            target.append({"dimension": dimension, "members": members})
        if not cube or not pov or not rows or not columns:
            return None
        return {
            "cube": cube,
            "pov": pov,
            "rows": rows,
            "columns": columns,
        }

    @classmethod
    def _deterministic_business_rule_call(
        cls,
        state: AgentGraphState,
    ) -> AgentToolCall | None:
        """Enter the governed rule flow for an explicit preparation request.

        Provider token limits or tool-choice variance must not prevent a
        clearly requested Business Rule from reaching live artifact selection.
        Questions and general guidance still go to the configured model.
        """
        allowed = {
            str(item).strip()
            for item in state.get("allowed_tool_names", [])
        }
        if "prepare_operation_action" not in allowed:
            return None
        user_messages = [
            str(item.get("content") or "").strip()
            for item in state.get("messages", [])
            if item.get("role") == AgentMessageRole.USER.value
        ]
        if not user_messages:
            return None
        latest = user_messages[-1]
        source_request = latest
        if not cls._is_explicit_business_rule_preparation(latest):
            if (
                len(user_messages) < 2
                or not cls._is_artifact_confirmation_reply(latest)
                or not cls._mentions_business_rule(user_messages[-2])
            ):
                return None
            source_request = user_messages[-2]
        objective = " ".join(source_request.split())[:500]
        return AgentToolCall(
            name="prepare_operation_action",
            arguments={
                "operation_code": "business-rules",
                "objective": objective or "Prepare the requested Business Rule.",
            },
            call_id="deterministic-business-rule-preparation",
        )

    @classmethod
    def _deterministic_pipeline_call(
        cls,
        state: AgentGraphState,
    ) -> AgentToolCall | None:
        """Enter live Pipeline preflight for an explicit Pipeline request."""
        allowed = {
            str(item).strip()
            for item in state.get("allowed_tool_names", [])
        }
        if "prepare_operation_action" not in allowed:
            return None
        user_messages = [
            str(item.get("content") or "").strip()
            for item in state.get("messages", [])
            if item.get("role") == AgentMessageRole.USER.value
        ]
        if not user_messages:
            return None
        latest = user_messages[-1]
        source_request = latest
        if not cls._is_explicit_pipeline_preparation(latest):
            if (
                len(user_messages) < 2
                or not cls._is_artifact_confirmation_reply(latest)
                or not cls._mentions_pipeline(user_messages[-2])
            ):
                return None
            source_request = user_messages[-2]
        objective = " ".join(source_request.split())[:500]
        return AgentToolCall(
            name="prepare_operation_action",
            arguments={
                "operation_code": "pipelines",
                "objective": objective or "Prepare the requested Pipeline.",
            },
            call_id="deterministic-pipeline-preparation",
        )

    @classmethod
    def _deterministic_schedule_call(
        cls,
        state: AgentGraphState,
    ) -> AgentToolCall | None:
        """Route explicit schedule changes without provider interpretation."""
        allowed = {
            str(item).strip()
            for item in state.get("allowed_tool_names", [])
        }
        if "prepare_schedule_action" not in allowed:
            return None
        user_messages = [
            str(item.get("content") or "").strip()
            for item in state.get("messages", [])
            if item.get("role") == AgentMessageRole.USER.value
        ]
        if not user_messages:
            return None
        latest = user_messages[-1]
        normalized = cls._normalize_artifact_text(latest)
        padded = f" {normalized} "
        mentions_schedule = any(
            phrase in padded
            for phrase in (
                " schedule ",
                " scheduled ",
                " recurrence ",
                " recurring ",
            )
        )
        if not mentions_schedule or normalized.startswith(
            ("how ", "why ", "what ", "which ", "can ", "should ", "explain ")
        ):
            return None
        if any(word in set(normalized.split()) for word in {"pause", "disable", "stop"}):
            action = "PAUSE"
        elif any(word in set(normalized.split()) for word in {"resume", "enable", "restart"}):
            action = "RESUME"
        elif any(
            word in set(normalized.split())
            for word in {"schedule", "create", "add", "run", "start"}
        ):
            action = "CREATE"
        else:
            return None
        return AgentToolCall(
            name="prepare_schedule_action",
            arguments={
                "action": action,
                "objective": " ".join(latest.split())[:500],
            },
            call_id=f"deterministic-schedule-{action.casefold()}",
        )

    def _deterministic_standalone_flow_call(
        self,
        state: AgentGraphState,
    ) -> AgentToolCall | None:
        """Enter guided configuration for an explicitly standalone flow."""
        cls = type(self)
        allowed = {
            str(item).strip()
            for item in state.get("allowed_tool_names", [])
        }
        if "prepare_standalone_flow_action" not in allowed:
            return None
        user_messages = [
            str(item.get("content") or "").strip()
            for item in state.get("messages", [])
            if item.get("role") == AgentMessageRole.USER.value
        ]
        if not user_messages:
            return None
        latest = user_messages[-1]
        normalized = cls._normalize_artifact_text(latest)
        if not cls._requests_standalone_flow(normalized):
            return None
        if not (
            "configure and execute" in normalized
            or re.search(r"\b(?:execute|run)\b.*\bstandalone flow\b", normalized)
        ):
            return None
        step_text = cls._standalone_flow_step_text(latest)
        steps = self._requested_multi_step_codes_with_artifacts(step_text)
        if len(steps) < 2:
            return None
        return AgentToolCall(
            name="prepare_standalone_flow_action",
            arguments={
                "objective": " ".join(latest.split())[:500],
                "requested_steps": list(steps),
            },
            call_id="deterministic-standalone-flow-preparation",
        )

    def _deterministic_multi_step_plan_call(
        self,
        state: AgentGraphState,
    ) -> AgentToolCall | None:
        """Plan multi-step work without constructing a shadow workflow."""
        cls = type(self)
        allowed = {
            str(item).strip()
            for item in state.get("allowed_tool_names", [])
        }
        if "plan_multi_step_request" not in allowed:
            return None
        user_messages = [
            str(item.get("content") or "").strip()
            for item in state.get("messages", [])
            if item.get("role") == AgentMessageRole.USER.value
        ]
        if not user_messages:
            return None
        latest = user_messages[-1]
        normalized = cls._normalize_artifact_text(latest)
        if not cls._contains_preparation_action(normalized):
            return None
        steps = self._requested_multi_step_codes_with_artifacts(normalized)
        if len(steps) < 2:
            return None
        return AgentToolCall(
            name="plan_multi_step_request",
            arguments={
                "objective": " ".join(latest.split())[:1000],
                "requested_steps": list(steps),
                "prefer_standalone": cls._requests_standalone_flow(normalized),
            },
            call_id="deterministic-multi-step-planning",
        )

    @staticmethod
    def _requested_multi_step_codes(text: str) -> tuple[str, ...]:
        return tuple(
            code
            for _position, code in AgentGraphOrchestrator._requested_multi_step_occurrences(
                text
            )
        )

    @staticmethod
    def _requested_multi_step_occurrences(text: str) -> list[tuple[int, str]]:
        """Return ordered operation mentions, including repeated operations."""
        source = str(text or "").casefold().replace("->", " then ").replace(
            "→",
            " then ",
        )
        normalized = " ".join(re.sub(r"[\W_]+", " ", source).split())
        patterns: dict[str, str] = {
            "pipelines": r"\b(?:oracle\s+)?pipelines?\b",
            "metadata-import": (
                r"\b(?:metadata\s+import|import\s+metadata|metadata\s+load|"
                r"load\s+metadata|update\s+metadata)\b"
            ),
            "data-integrations": (
                r"\b(?:data\s+integrations?|integration\s+load|integration)\b"
            ),
            "data-import": (
                r"\b(?:data\s+import|import\s+data|data\s+load|load\s+data|"
                r"(?:load|import)(?:\s+(?:a|the|planning|forecast|approved|"
                r"source))?\s+data)\b"
            ),
            "business-rules": (
                r"\b(?:business\s+rules?|calculation\s+rules?|calc\s+rules?|"
                r"calculate|calculation|(?:run|execute|prepare)\s+"
                r"(?:another\s+|the\s+|a\s+)?rule|rules?)\b"
            ),
            "data-maps": (
                r"\b(?:data\s+maps?|push\s+data|data\s+push|smart\s*push|"
                r"publish\s+data|push\s+to\s+reporting)\b"
            ),
            "cube-refresh": (
                r"\b(?:cube\s+refresh|refresh\s+cube|database\s+refresh|"
                r"refresh\s+database)\b"
            ),
            "report-generation": (
                r"\b(?:generate\s+report|create\s+report|export\s+report|"
                r"generate\s+workbook)\b"
            ),
            "substitution-variables": (
                r"\b(?:substitution|subst)\s+(?:variables?|vars?)\b"
            ),
            "user-variables": r"\buser\s+(?:variables?|vars?)\b",
        }
        matches: list[tuple[int, str]] = []
        integration_matches = tuple(
            re.finditer(patterns["data-integrations"], normalized)
        )
        for code, pattern in patterns.items():
            for match in re.finditer(pattern, normalized):
                if code == "pipelines" and re.search(
                    r"(?:without(?:\s+an?)?(?:\s+oracle)?|"
                    r"do\s+not\s+use(?:\s+an?)?(?:\s+oracle)?|"
                    r"don\s+t\s+use(?:\s+an?)?(?:\s+oracle)?)\s+$",
                    normalized[max(0, match.start() - 45) : match.start()],
                ):
                    continue
                if code == "data-import" and any(
                    abs(match.start() - integration.start()) <= 45
                    and not re.search(
                        r"\b(?:then|next|after\s+that|followed\s+by)\b",
                        normalized[
                            min(match.start(), integration.start()) :
                            max(match.end(), integration.end())
                        ],
                    )
                    for integration in integration_matches
                ):
                    # "Revenue Load Data Integration" describes one
                    # Integration. Keep a native Data Import only when the
                    # user explicitly sequences it separately.
                    continue
                matches.append((match.start(), code))
        # Overlapping synonyms for one operation describe one step, not two.
        matches.sort(key=lambda item: (item[0], item[1]))
        distinct: list[tuple[int, str]] = []
        for position, code in matches:
            prior_position = next(
                (
                    item_position
                    for item_position, item_code in reversed(distinct)
                    if item_code == code
                ),
                None,
            )
            if prior_position is not None:
                between = normalized[prior_position:position]
                explicitly_sequenced = bool(
                    re.search(
                        r"\b(?:then|next|after|followed\s+by)\b|->",
                        between,
                    )
                )
                if not explicitly_sequenced:
                    continue
            distinct.append((position, code))
        return distinct

    def _requested_multi_step_codes_with_artifacts(
        self,
        text: str,
    ) -> tuple[str, ...]:
        """Expand an ordered request using exact live artifact mentions.

        A business user commonly writes ``Run Revenue_Load, BR_Calc, then
        Forecast_to_Reporting``. Only the rule name may contain a generic
        operation word, so restricting artifact discovery to already detected
        operation types silently loses the Integration and Data Map. For text
        that clearly describes a sequence, inspect every supported catalog and
        add only exact, unambiguous artifact mentions.
        """
        occurrences = self._requested_multi_step_occurrences(text)
        multi_step_signal = self._has_multi_step_signal(text, occurrences)
        if len(occurrences) < 2 and not multi_step_signal:
            return tuple(code for _position, code in occurrences)
        operation_codes = {code for _position, code in occurrences}
        if multi_step_signal:
            operation_codes.update(MULTI_STEP_ARTIFACT_OPERATION_CODES)

        mentions_by_code: dict[str, tuple[tuple[int, str], ...]] = {}
        for operation_code in operation_codes:
            try:
                catalog = self._gateway.artifact_catalog(operation_code)
            except Exception as exc:
                self._logger.debug(
                    "Artifact-aware step parsing skipped for '%s': %s",
                    operation_code,
                    exc,
                )
                continue
            mentions_by_code[operation_code] = (
                self._artifact_mentions_in_user_text(catalog, text)
            )

        # If the same exact text span exists in more than one Oracle catalog,
        # do not guess its operation type. A generic operation mention still
        # remains available to drive a governed artifact-choice card.
        absent_codes_by_position: dict[int, set[str]] = {}
        for operation_code, mentions in mentions_by_code.items():
            if any(code == operation_code for _position, code in occurrences):
                continue
            for position, _identifier in mentions:
                absent_codes_by_position.setdefault(position, set()).add(
                    operation_code
                )

        for operation_code, mentions in mentions_by_code.items():
            existing = [
                item for item in occurrences if item[1] == operation_code
            ]
            if len(mentions) <= len(existing):
                continue
            if existing:
                occurrences = [
                    item for item in occurrences if item[1] != operation_code
                ]
                occurrences.extend(
                    (position, operation_code)
                    for position, _identifier in mentions
                )
                continue
            occurrences.extend(
                (position, operation_code)
                for position, _identifier in mentions
                if len(absent_codes_by_position.get(position, ())) == 1
            )
        occurrences.sort(key=lambda item: item[0])
        return tuple(code for _position, code in occurrences)

    @staticmethod
    def _has_multi_step_signal(
        text: str,
        occurrences: Sequence[tuple[int, str]],
    ) -> bool:
        """Return whether a request safely warrants cross-catalog inspection."""
        if len(occurrences) >= 2:
            return True
        source = str(text or "")
        normalized = " ".join(source.casefold().split())
        if re.search(
            r"(?:->|→)|\b(?:then|next|followed\s+by|after\s+that)\b",
            normalized,
        ):
            return True
        action_count = len(
            re.findall(
                r"\b(?:prepare|run|execute|start|calculate|launch|push|"
                r"publish|import|load|update|set|change|assign|create|"
                r"refresh|generate|export)\b",
                normalized,
            )
        )
        repeated_operation_list = bool(
            re.search(
                r"\b(?:pipelines|business\s+rules|calculation\s+rules|"
                r"data\s+maps|data\s+integrations|substitution\s+variables|"
                r"user\s+variables)\b.*\band\b",
                normalized,
            )
        )
        return repeated_operation_list or action_count >= 2 or (
            action_count >= 1 and ("," in source or ";" in source)
        )

    @staticmethod
    def _requests_standalone_flow(text: str) -> bool:
        normalized = " ".join(
            re.sub(r"[\W_]+", " ", str(text or "").casefold()).split()
        )
        padded = f" {normalized} "
        return any(
            phrase in padded
            for phrase in (
                " standalone flow ",
                " platform managed flow ",
                " without pipeline ",
                " do not use an oracle pipeline ",
                " don t use an oracle pipeline ",
            )
        )

    @staticmethod
    def _standalone_flow_step_text(text: str) -> str:
        """Isolate the UI-generated ordered step list from its objective."""
        normalized = " ".join(str(text or "").split())
        match = re.search(
            r"\bfor these operations\s*:?\s*(.+?)"
            r"(?:\s+objective\s*:?|$)",
            normalized,
            re.IGNORECASE,
        )
        return match.group(1).strip() if match is not None else normalized

    @classmethod
    def _deterministic_data_integration_call(
        cls,
        state: AgentGraphState,
    ) -> AgentToolCall | None:
        """Enter governed Data Integration inputs without model tool choice."""
        allowed = {
            str(item).strip()
            for item in state.get("allowed_tool_names", [])
        }
        if "prepare_operation_action" not in allowed:
            return None
        user_messages = [
            str(item.get("content") or "").strip()
            for item in state.get("messages", [])
            if item.get("role") == AgentMessageRole.USER.value
        ]
        if not user_messages:
            return None
        latest = user_messages[-1]
        source_request = latest
        if not cls._is_explicit_data_integration_preparation(latest):
            if (
                len(user_messages) < 2
                or not cls._is_artifact_confirmation_reply(latest)
                or not cls._mentions_data_integration(user_messages[-2])
            ):
                return None
            source_request = user_messages[-2]
        objective = " ".join(source_request.split())[:500]
        return AgentToolCall(
            name="prepare_operation_action",
            arguments={
                "operation_code": "data-integrations",
                "objective": (
                    objective or "Prepare the requested Data Integration."
                ),
            },
            call_id="deterministic-data-integration-preparation",
        )

    @classmethod
    def _deterministic_data_map_call(
        cls,
        state: AgentGraphState,
    ) -> AgentToolCall | None:
        """Route an explicit Planning data push into governed Data Maps."""
        allowed = {
            str(item).strip()
            for item in state.get("allowed_tool_names", [])
        }
        if "prepare_operation_action" not in allowed:
            return None
        user_messages = [
            str(item.get("content") or "").strip()
            for item in state.get("messages", [])
            if item.get("role") == AgentMessageRole.USER.value
        ]
        if not user_messages:
            return None
        latest = user_messages[-1]
        source_request = latest
        if not cls._is_explicit_data_map_preparation(latest):
            if (
                len(user_messages) < 2
                or not cls._is_artifact_confirmation_reply(latest)
                or not cls._mentions_data_map(user_messages[-2])
            ):
                return None
            source_request = user_messages[-2]
        objective = " ".join(source_request.split())[:500]
        return AgentToolCall(
            name="prepare_operation_action",
            arguments={
                "operation_code": "data-maps",
                "objective": objective or "Prepare the requested Data Map.",
            },
            call_id="deterministic-data-map-preparation",
        )

    @classmethod
    def _deterministic_metadata_import_call(
        cls,
        state: AgentGraphState,
    ) -> AgentToolCall | None:
        """Enter governed Metadata Import without model tool selection."""
        allowed = {
            str(item).strip()
            for item in state.get("allowed_tool_names", [])
        }
        if "prepare_operation_action" not in allowed:
            return None
        user_messages = [
            str(item.get("content") or "").strip()
            for item in state.get("messages", [])
            if item.get("role") == AgentMessageRole.USER.value
        ]
        if not user_messages:
            return None
        latest = user_messages[-1]
        source_request = latest
        if not cls._is_explicit_metadata_import_preparation(latest):
            if (
                len(user_messages) < 2
                or not cls._is_artifact_confirmation_reply(latest)
                or not cls._mentions_metadata_import(user_messages[-2])
            ):
                return None
            source_request = user_messages[-2]
        objective = " ".join(source_request.split())[:500]
        return AgentToolCall(
            name="prepare_operation_action",
            arguments={
                "operation_code": "metadata-import",
                "objective": (
                    objective or "Prepare the requested Metadata Import."
                ),
            },
            call_id="deterministic-metadata-import-preparation",
        )

    def _deterministic_variable_call(
        self,
        state: AgentGraphState,
    ) -> AgentToolCall | None:
        """Route explicit variable changes without relying on model tool choice.

        A typed variable request is sufficient to select its operation. For the
        common shorthand ``Update CurYr to FY27``, the name must match exactly
        one current substitution variable before this deterministic path is
        used. General questions and unmatched update language remain with the
        configured model.
        """
        allowed = {
            str(item).strip()
            for item in state.get("allowed_tool_names", [])
        }
        if "prepare_operation_action" not in allowed:
            return None
        user_messages = [
            str(item.get("content") or "").strip()
            for item in state.get("messages", [])
            if item.get("role") == AgentMessageRole.USER.value
        ]
        if not user_messages:
            return None
        latest = user_messages[-1]
        source_request = latest
        normalized = self._normalize_artifact_text(latest)
        if not self._contains_preparation_action(normalized):
            if (
                len(user_messages) < 2
                or not self._is_artifact_confirmation_reply(latest)
            ):
                return None
            source_request = user_messages[-2]
            normalized = self._normalize_artifact_text(source_request)
            if not self._contains_preparation_action(normalized):
                return None

        operation_code: str | None = None
        artifact_name: str | None = None
        if self._mentions_user_variable(source_request):
            operation_code = "user-variables"
        elif self._mentions_substitution_variable(source_request):
            operation_code = "substitution-variables"
        else:
            # Business users commonly omit the words "substitution variable".
            # Accept that shorthand only after an exact live-name match.
            try:
                catalog = self._gateway.artifact_catalog(
                    "substitution-variables"
                )
                choices = tuple(
                    identifier
                    for identifier, _display_name in catalog
                    if identifier.casefold()
                    != "create a new substitution variable"
                )
                artifact_name = self._artifact_named_in_user_text(
                    choices,
                    source_request,
                )
            except Exception as exc:
                self._logger.warning(
                    "Live substitution-variable intent matching is unavailable: %s",
                    exc,
                )
            if artifact_name:
                operation_code = "substitution-variables"
        if operation_code is None:
            return None

        if artifact_name is None:
            try:
                artifact_name = self._artifact_named_in_user_text(
                    tuple(
                        identifier
                        for identifier, _display_name in self._gateway.artifact_catalog(
                            operation_code
                        )
                    ),
                    source_request,
                )
            except Exception as exc:
                self._logger.warning(
                    "Live %s intent matching is unavailable: %s",
                    operation_code,
                    exc,
                )
        objective = " ".join(source_request.split())[:500]
        arguments: dict[str, Any] = {
            "operation_code": operation_code,
            "objective": objective or "Prepare the requested variable change.",
        }
        if artifact_name:
            arguments["artifact_name"] = artifact_name
        return AgentToolCall(
            name="prepare_operation_action",
            arguments=arguments,
            call_id=f"deterministic-{operation_code}-preparation",
        )

    @classmethod
    def _is_explicit_business_rule_preparation(cls, text: str) -> bool:
        normalized = cls._normalize_artifact_text(text)
        if not normalized or not cls._mentions_business_rule(normalized):
            return False
        return cls._contains_preparation_action(normalized)

    @classmethod
    def _is_explicit_pipeline_preparation(cls, text: str) -> bool:
        normalized = cls._normalize_artifact_text(text)
        if (
            not normalized
            or cls._requests_standalone_flow(normalized)
            or not cls._mentions_pipeline(normalized)
        ):
            return False
        return cls._contains_preparation_action(normalized)

    @classmethod
    def _is_explicit_data_integration_preparation(cls, text: str) -> bool:
        normalized = cls._normalize_artifact_text(text)
        if not normalized or not cls._mentions_data_integration(normalized):
            return False
        return cls._contains_preparation_action(normalized)

    @classmethod
    def _is_explicit_data_map_preparation(cls, text: str) -> bool:
        normalized = cls._normalize_artifact_text(text)
        if not normalized or not cls._mentions_data_map(normalized):
            return False
        return cls._contains_preparation_action(normalized)

    @classmethod
    def _is_explicit_metadata_import_preparation(cls, text: str) -> bool:
        normalized = cls._normalize_artifact_text(text)
        if not normalized or not cls._mentions_metadata_import(normalized):
            return False
        return cls._contains_preparation_action(normalized)

    @staticmethod
    def _contains_preparation_action(normalized: str) -> bool:
        if normalized.startswith(
            (
                "how ",
                "why ",
                "what is ",
                "what are ",
                "which ",
                "explain ",
                "describe ",
                "list ",
                "show ",
            )
        ):
            return False
        action_words = {
            "prepare",
            "run",
            "execute",
            "start",
            "calculate",
            "launch",
            "push",
            "publish",
            "import",
            "load",
            "update",
            "set",
            "change",
            "assign",
            "create",
        }
        return bool(action_words & set(normalized.split()))

    @classmethod
    def _mentions_business_rule(cls, text: str) -> bool:
        normalized = cls._normalize_artifact_text(text)
        return any(
            phrase in f" {normalized} "
            for phrase in (
                " business rule ",
                " business rules ",
                " calculation rule ",
                " calculation rules ",
                " calc rule ",
                " calc rules ",
            )
        )

    @classmethod
    def _mentions_pipeline(cls, text: str) -> bool:
        normalized = cls._normalize_artifact_text(text)
        return " pipeline " in f" {normalized} " or " pipelines " in (
            f" {normalized} "
        )

    @classmethod
    def _mentions_data_integration(cls, text: str) -> bool:
        normalized = cls._normalize_artifact_text(text)
        padded = f" {normalized} "
        return (
            " data integration " in padded
            or " data integrations " in padded
            or " integration " in padded
            or " integrations " in padded
        )

    @classmethod
    def _mentions_data_map(cls, text: str) -> bool:
        normalized = cls._normalize_artifact_text(text)
        padded = f" {normalized} "
        return any(
            phrase in padded
            for phrase in (
                " data map ",
                " data maps ",
                " data push ",
                " push data ",
                " publish planning data ",
            )
        )

    @classmethod
    def _mentions_metadata_import(cls, text: str) -> bool:
        normalized = cls._normalize_artifact_text(text)
        padded = f" {normalized} "
        return any(
            phrase in padded
            for phrase in (
                " metadata import ",
                " import metadata ",
                " metadata load ",
                " load metadata ",
                " metadata job ",
            )
        )

    @classmethod
    def _mentions_substitution_variable(cls, text: str) -> bool:
        normalized = cls._normalize_artifact_text(text)
        padded = f" {normalized} "
        return any(
            phrase in padded
            for phrase in (
                " substitution variable ",
                " substitution variables ",
                " substitution var ",
                " substitution vars ",
                " subst variable ",
                " subst var ",
            )
        )

    @classmethod
    def _mentions_user_variable(cls, text: str) -> bool:
        normalized = cls._normalize_artifact_text(text)
        padded = f" {normalized} "
        return any(
            phrase in padded
            for phrase in (
                " user variable ",
                " user variables ",
                " user var ",
                " user vars ",
            )
        )

    @staticmethod
    def _is_execution_evidence_request(text: str) -> bool:
        """Recognize requests for facts about a retained execution."""
        normalized = " ".join(str(text or "").casefold().split())
        if not normalized:
            return False
        patterns = (
            r"\bhow many records?\b.*\b(read|processed|rejected)\b",
            r"\b(records? read|records? processed|records? rejected)\b.*\b(latest|last|run|job|execution|how many|show|tell)\b",
            r"\b(load statistics|record statistics|load counts?|record counts?)\b",
            r"\bwhy (?:did|has|was)\b.*\b(run|job|execution|load|import|pipeline)\b.*\b(fail|failed|error|reject)\b",
            r"\b(show|inspect|check|explain|get)\b.*\b(latest|last|failed)\b.*\b(run|job|execution)\b",
            r"\b(status of|job status|execution status)\b.*\b(run|job|execution|latest|last)\b",
            r"\b(?:execution|run)\s+id\s*[:#]?\s*[a-z0-9][a-z0-9._-]{5,}\b",
        )
        return any(re.search(pattern, normalized) for pattern in patterns)

    @staticmethod
    def _execution_id_from_text(text: str) -> str | None:
        match = re.search(
            r"\b(?:execution|run)\s+id\s*[:#]?\s*"
            r"[`'\"]?([A-Za-z0-9][A-Za-z0-9._-]{5,127})[`'\"]?",
            str(text or ""),
            re.IGNORECASE,
        )
        if match is None:
            return None
        candidate = match.group(1).strip("`'\".,;:?!")
        if candidate.casefold() in {
            "latest",
            "failed",
            "status",
        }:
            return None
        return candidate

    @staticmethod
    def _deterministic_completion_text(
        operation_code: str,
        activities: Sequence[dict[str, Any]],
    ) -> str:
        if operation_code == "execution-evidence":
            return AgentGraphOrchestrator._execution_evidence_completion_text(
                activities
            )
        if operation_code == "multi-step-plan":
            activity = next(
                (
                    item
                    for item in reversed(activities)
                    if item.get("name") == "plan_multi_step_request"
                ),
                None,
            )
            result = activity.get("result") if isinstance(activity, dict) else None
            if isinstance(result, dict) and result.get("executable"):
                pipeline = result.get("pipeline")
                name = (
                    str(pipeline.get("display_name") or pipeline.get("code"))
                    if isinstance(pipeline, dict)
                    else "the matched Pipeline"
                )
                return (
                    f"I found one clear Oracle Pipeline match: **{name}**. "
                    "Review its live stages below, then continue to its governed "
                    "inputs and single execution approval."
                )
            if (
                isinstance(result, dict)
                and result.get("resolution") == "STANDALONE_FLOW_DRAFT"
            ):
                return (
                    "I prepared the requested **standalone flow draft** without "
                    "using the suggested Oracle Pipeline. Review the ordered "
                    "steps below; nothing has executed."
                )
            return (
                "I could not prove that one registered Oracle Pipeline covers "
                "the complete request. The ordered design is shown below, but "
                "it cannot execute until an administrator configures or identifies "
                "the correct Pipeline."
            )
        if operation_code == "standalone-flow":
            activity = next(
                (
                    item
                    for item in reversed(activities)
                    if item.get("name") == "prepare_standalone_flow_action"
                ),
                None,
            )
            if isinstance(activity, dict) and str(
                activity.get("status") or ""
            ).upper() == "SUCCESS":
                return (
                    "The approved **standalone Planning flow** has been queued "
                    "for monitored execution. Its operations run in the reviewed "
                    "order and the flow stops automatically after the first "
                    "failed step."
                )
            return (
                "The standalone Planning flow was cancelled. No Oracle "
                "operation was started."
            )
        if operation_code == "data-review-dimensions":
            activity = next(
                (
                    item
                    for item in reversed(activities)
                    if item.get("name") == "list_cube_dimensions"
                ),
                None,
            )
            cube = "the selected cube"
            if isinstance(activity, dict):
                arguments = activity.get("arguments")
                if isinstance(arguments, dict):
                    cube = str(arguments.get("cube") or cube)
            if isinstance(activity, dict) and str(
                activity.get("status") or ""
            ).upper() == "SUCCESS":
                return (
                    f"I loaded the live dimensions for **{cube}**. Choose "
                    "where each dimension belongs in the guided card below."
                )
            return (
                f"**{cube} remains selected.** This Planning environment did "
                "not expose discoverable dimensions for that cube, so use the "
                "exact-layout compatibility step below. You will not need to "
                "choose the cube again."
            )
        if operation_code == "data-explorer-views":
            activity = next(
                (
                    item
                    for item in reversed(activities)
                    if item.get("name") == "list_data_explorer_views"
                ),
                None,
            )
            if isinstance(activity, dict) and str(
                activity.get("status") or ""
            ).upper() == "SUCCESS":
                return (
                    "I loaded the reusable Data Explorer views saved in this "
                    "platform. Choose one below to retrieve its current Oracle "
                    "values, or start a fresh cube layout in Data Explorer."
                )
            return "The saved Data Explorer views could not be loaded."
        if operation_code == "saved-data-view":
            activity = next(
                (
                    item
                    for item in reversed(activities)
                    if item.get("name") == "review_saved_data_view"
                ),
                None,
            )
            if isinstance(activity, dict) and str(
                activity.get("status") or ""
            ).upper() == "SUCCESS":
                return (
                    "I reopened the saved layout and retrieved its current "
                    "Oracle values. Review the read-only Data Explorer grid "
                    "below or export it to Excel or CSV."
                )
            error = ""
            if isinstance(activity, dict) and isinstance(
                activity.get("result"), dict
            ):
                error = str(activity["result"].get("error") or "").strip()
            return (
                "The saved Data Explorer view could not be loaded. "
                + (f"The platform returned: {error}" if error else "")
            ).strip()
        if operation_code == "data-review-slice":
            activity = next(
                (
                    item
                    for item in reversed(activities)
                    if item.get("name") == "review_data_slice"
                ),
                None,
            )
            if isinstance(activity, dict) and str(
                activity.get("status") or ""
            ).upper() == "SUCCESS":
                return (
                    "I loaded the requested live Planning slice. Review the "
                    "read-only Data Explorer grid below or export it."
                )
            error = ""
            if isinstance(activity, dict) and isinstance(
                activity.get("result"), dict
            ):
                error = str(activity["result"].get("error") or "").strip()
            return (
                "The exact Planning slice could not be loaded. "
                + (f"Oracle returned: {error} " if error else "")
                + "Check the exact dimension and member names; the selected "
                "cube and layout remain visible in the review card."
            )
        if operation_code in PIPELINE_SCHEDULE_ACTIONS:
            label = {
                PIPELINE_SCHEDULE_CREATE: "Pipeline schedule",
                PIPELINE_SCHEDULE_PAUSE: "schedule pause",
                PIPELINE_SCHEDULE_RESUME: "schedule resume",
            }[operation_code]
            statuses = {
                str(item.get("status") or "").strip().upper()
                for item in activities
            }
            if "FAILED" in statuses:
                return f"The {label} could not be applied. Review the validated error."
            if "CANCELLED" in statuses:
                return f"The {label} was cancelled. No schedule changed."
            return f"The approved {label} was applied through the governed scheduler."
        display_name = {
            "business-rules": "Business Rule",
            "pipelines": "Pipeline",
            "data-integrations": "Data Integration",
            "data-maps": "Data Map",
            "metadata-import": "Metadata Import",
            "substitution-variables": "Substitution Variable change",
            "user-variables": "User Variable change",
        }.get(operation_code, "operation")
        statuses = {
            str(item.get("status") or "").strip().upper()
            for item in activities
        }
        if "FAILED" in statuses:
            return (
                f"The {display_name} request could not be prepared. Review "
                "the verified error details and try again."
            )
        if "CANCELLED" in statuses:
            return (
                f"{display_name} preparation was cancelled. No Oracle "
                "operation was started."
            )
        return (
            f"The approved {display_name} passed governed preparation and "
            "is ready for monitored execution."
        )

    @staticmethod
    def _execution_evidence_completion_text(
        activities: Sequence[dict[str, Any]],
    ) -> str:
        activity = next(
            (
                item
                for item in reversed(activities)
                if item.get("name") == "get_execution_evidence"
            ),
            None,
        )
        if not isinstance(activity, dict):
            return "No retained execution evidence was returned."
        result = activity.get("result")
        if not isinstance(result, dict) or not isinstance(
            result.get("execution"), dict
        ):
            error = (
                str(result.get("error") or "").strip()
                if isinstance(result, dict)
                else ""
            )
            return error or "No matching retained execution was found."

        execution = result["execution"]
        workflow = str(execution.get("workflow_name") or "Execution")
        status = str(execution.get("status") or "UNKNOWN")
        execution_id = str(execution.get("execution_id") or "")
        diagnosis = str(execution.get("diagnosis") or "").strip()
        statistics = execution.get("record_statistics")
        lines: list[str] = ["### Execution evidence", ""]
        if isinstance(statistics, dict):
            read = int(statistics.get("records_read") or 0)
            processed = int(statistics.get("records_processed") or 0)
            rejected = int(statistics.get("records_rejected") or 0)
            lines.extend(
                [
                    f"Oracle reported **{read:,} records read**, "
                    f"**{processed:,} processed**, and "
                    f"**{rejected:,} rejected**.",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "Oracle did not expose record statistics for this "
                    "execution. This is normal for operations that do not "
                    "load metadata or data.",
                    "",
                ]
            )
        lines.extend(
            [
                f"- **Operation:** {workflow}",
                f"- **Status:** {status}",
                f"- **Execution ID:** `{execution_id}`",
            ]
        )
        if diagnosis:
            lines.extend(["", f"**Result:** {diagnosis}"])
        failed_steps = [
            item
            for item in execution.get("steps", [])
            if isinstance(item, dict) and item.get("status") == "FAILED"
        ]
        if failed_steps:
            lines.extend(["", "### Failed step"])
            for step in failed_steps:
                name = str(step.get("name") or "Unknown step")
                error = str(step.get("error_message") or "").strip()
                lines.append(
                    f"- **{name}:** {error or 'No retained Oracle error was available.'}"
                )
        return "\n".join(lines)

    def _approval_node(self, state: AgentGraphState) -> AgentGraphState:
        if any(
            item.get("name") == "prepare_standalone_flow_action"
            for item in state.get("pending_tool_calls", [])
        ):
            return self._standalone_flow_approval_node(state)
        calls = tuple(
            self._call_from_payload(item)
            for item in state.get("pending_tool_calls", [])
            if item.get("name") in {
                "prepare_operation_action",
                "prepare_schedule_action",
            }
        )
        if len(calls) != 1:
            raise AgentProviderError(
                "The assistant must prepare one governed operation at a time."
            )
        call = calls[0]
        user_messages = [
            str(item.get("content") or "")
            for item in state.get("messages", [])
            if item.get("role") == AgentMessageRole.USER.value
        ]
        latest_user_text_original = (
            user_messages[-1] if user_messages else ""
        )
        previous_user_text = (
            user_messages[-2] if len(user_messages) > 1 else ""
        )
        latest_user_text = latest_user_text_original.casefold()
        previous_assistant_text = next(
            (
                str(item.get("content") or "")
                for item in reversed(state.get("messages", []))
                if item.get("role") == AgentMessageRole.ASSISTANT.value
            ),
            "",
        )
        if call.name == "prepare_operation_action":
            resolved_operation = self._resolve_explicit_operation_intent(
                latest_user_text,
                str(call.arguments.get("operation_code") or ""),
            )
            if resolved_operation != str(
                call.arguments.get("operation_code") or ""
            ).strip().casefold():
                call = AgentToolCall(
                    name=call.name,
                    arguments={
                        **call.arguments,
                        "operation_code": resolved_operation,
                        "artifact_name": None,
                    },
                    call_id=call.call_id,
                )
        # This gateway operation only validates and describes a draft. It does
        # not call Oracle or persist anything.
        proposal = self._execute_allowed_tool(call, state).get("action_draft")
        if not isinstance(proposal, dict):
            raise AgentProviderError("The proposed operation could not be resolved.")
        pending_payloads = [
            self._call_payload(call)
            if item.get("name") == call.name
            else item
            for item in state.get("pending_tool_calls", [])
        ]
        operation_code = str(proposal.get("target_code") or "").casefold()
        try:
            snapshot = state.get("artifact_catalog_snapshot")
            if (
                isinstance(snapshot, dict)
                and str(snapshot.get("operation_code") or "").casefold()
                == operation_code
                and isinstance(snapshot.get("items"), list)
            ):
                artifact_catalog = tuple(
                    (str(item[0]), str(item[1]))
                    for item in snapshot["items"]
                    if isinstance(item, (list, tuple)) and len(item) == 2
                )
            else:
                artifact_catalog = self._gateway.artifact_catalog(
                    operation_code
                )
            if (
                operation_code == "substitution-variables"
                and not self._is_substitution_variable_creation_request(
                    previous_user_text
                    if self._is_artifact_confirmation_reply(latest_user_text)
                    else latest_user_text_original
                )
            ):
                # "Create a new substitution variable" is a deliberate action,
                # not a fuzzy fallback for an update request. Excluding the
                # sentinel here lets an existing scoped variable be selected
                # or clarified without silently changing the requested action.
                artifact_catalog = tuple(
                    item
                    for item in artifact_catalog
                    if item[0].casefold()
                    != "create a new substitution variable"
                )
            choices = tuple(item[0] for item in artifact_catalog)
            display_names = dict(artifact_catalog)
        except Exception as exc:
            self._logger.warning(
                "Agent artifact clarification is unavailable for '%s': %s",
                proposal.get("target_code"),
                exc,
            )
            choices = ()
            display_names = {}
        recovery = self._gateway.artifact_recovery_definition(operation_code)
        requested_artifact = str(proposal.get("artifact_name") or "").strip()
        confirmed_prior_artifact = (
            self._artifact_alias_named_in_user_text(
                display_names,
                previous_assistant_text,
            )
            or self._artifact_named_in_user_text(
                choices,
                previous_assistant_text,
            )
            if self._is_artifact_confirmation_reply(latest_user_text)
            else None
        )
        explicitly_named = self._artifact_alias_named_in_user_text(
            display_names,
            latest_user_text,
        ) or self._artifact_named_in_user_text(
            choices,
            latest_user_text,
        ) or self._artifact_selected_by_ordinal_reply(
            choices,
            latest_user_text,
            previous_assistant_text,
        ) or confirmed_prior_artifact
        canonical_requested = explicitly_named or next(
            (
                item
                for item in choices
                if item.casefold() == requested_artifact.casefold()
            ),
            None,
        )
        search_context = " ".join(
            part
            for part in (
                previous_user_text
                if self._is_artifact_confirmation_reply(latest_user_text)
                else "",
                latest_user_text,
                str(proposal.get("objective") or ""),
            )
            if part
        ).strip()
        recommendation_codes = {
            "business-rules",
            "data-maps",
            "pipelines",
            "data-integrations",
            "data-import",
            "metadata-import",
            "substitution-variables",
        }
        recommendations = (
            recommend_artifacts(
                search_context,
                tuple(
                    (item, display_names.get(item, item))
                    for item in choices
                ),
            )
            if operation_code in recommendation_codes
            else ()
        )
        current_choice_names = {item.casefold() for item in choices}
        recommendations = tuple(
            item
            for item in recommendations
            if item.name.casefold() in current_choice_names
        )
        recommended_artifact = (
            self._unambiguous_recommendation(recommendations)
            if operation_code in {
                "data-import",
                "metadata-import",
                "substitution-variables",
            }
            and not explicitly_named
            else None
        )
        if recommended_artifact is not None:
            canonical_requested = recommended_artifact
        user_explicitly_named_artifact = bool(
            explicitly_named or recommended_artifact
        )
        if (choices or recovery) and not user_explicitly_named_artifact:
            selection_display_name = {
                PIPELINE_SCHEDULE_CREATE: "Oracle Pipelines",
                PIPELINE_SCHEDULE_PAUSE: "active schedules",
                PIPELINE_SCHEDULE_RESUME: "paused schedules",
            }.get(operation_code, str(proposal.get("display_name") or "Operation"))
            selection_prompt = {
                PIPELINE_SCHEDULE_CREATE: "Choose the Oracle Pipeline to schedule.",
                PIPELINE_SCHEDULE_PAUSE: "Choose the active schedule to pause.",
                PIPELINE_SCHEDULE_RESUME: "Choose the paused schedule to resume.",
            }.get(
                operation_code,
                f"Choose the {proposal.get('display_name')} artifact to prepare.",
            )
            clarification = interrupt(
                {
                    "kind": "operation_artifact_selection",
                    "operation_code": proposal.get("target_code"),
                    "display_name": selection_display_name,
                    "prompt": selection_prompt,
                    "options": list(choices),
                    "option_labels": display_names,
                    "allows_cancel": True,
                    "recommendations": [
                        item.as_payload() for item in recommendations
                    ],
                    "catalog_recovery": recovery,
                    "search_context": search_context,
                }
            )
            selected = str(
                clarification.get("value", "")
                if isinstance(clarification, dict)
                else clarification
            ).strip()
            if not selected:
                return {**state, "approval_decision": "reject"}
            canonical = self._gateway.resolve_artifact_choice(
                operation_code,
                selected,
            )
            if canonical is None:
                raise AgentProviderError(
                    "The selected Oracle artifact is no longer available."
                )
            call = AgentToolCall(
                name=call.name,
                arguments={**call.arguments, "artifact_name": canonical},
                call_id=call.call_id,
            )
            pending_payloads = [
                self._call_payload(call)
                if item.get("name") == call.name
                else item
                for item in pending_payloads
            ]
            proposal = self._execute_allowed_tool(call, state).get(
                "action_draft"
            )
            if not isinstance(proposal, dict):
                raise AgentProviderError(
                    "The selected operation could not be resolved."
                )
        elif canonical_requested and canonical_requested != requested_artifact:
            call = AgentToolCall(
                name=call.name,
                arguments={
                    **call.arguments,
                    "artifact_name": canonical_requested,
                },
                call_id=call.call_id,
            )
            pending_payloads = [
                self._call_payload(call)
                if item.get("name") == call.name
                else item
                for item in pending_payloads
            ]
            proposal = self._execute_allowed_tool(call, state).get("action_draft")
        elif requested_artifact and not user_explicitly_named_artifact:
            # Never carry a model-invented artifact into approval when live
            # discovery is unavailable. The governed screen will own selection.
            call = AgentToolCall(
                name=call.name,
                arguments={**call.arguments, "artifact_name": None},
                call_id=call.call_id,
            )
            pending_payloads = [
                self._call_payload(call)
                if item.get("name") == call.name
                else item
                for item in pending_payloads
            ]
            proposal = self._execute_allowed_tool(call, state).get("action_draft")
        artifact_name = str(proposal.get("artifact_name") or "").strip()
        if (
            operation_code in RESOLVED_ARTIFACT_REQUIRED_CODES
            and not artifact_name
        ):
            raise AgentProviderError(
                f"The agent could not resolve a current Oracle "
                f"{proposal.get('display_name')} artifact. Refresh the Oracle "
                "artifact catalog and try again; no approval was created."
            )
        guided = (
            self._gateway.guided_input_definition(
                str(proposal.get("target_code") or ""),
                artifact_name,
            )
            if artifact_name
            else None
        )
        input_source_text = (
            previous_user_text
            if self._is_artifact_confirmation_reply(latest_user_text)
            else latest_user_text_original
        )
        if guided is not None and operation_code == "substitution-variables":
            context = dict(guided.get("context") or {})
            creating_variable = str(context.get("action") or "") == "CREATE"
            if creating_variable:
                prefill = self._substitution_variable_creation_prefill(
                    input_source_text,
                    tuple(str(item) for item in context.get("scopes", ())),
                )
            else:
                replacement = self._variable_replacement_prefill(
                    input_source_text
                )
                prefill = {"new_value": replacement} if replacement else {}
            if prefill:
                context["prefill"] = prefill
                guided = {**guided, "context": context}
            if creating_variable and {
                "scope",
                "variable_name",
                "new_value",
            }.issubset(prefill):
                input_values = self._gateway.normalize_guided_inputs(
                    operation_code,
                    artifact_name,
                    prefill,
                )
                call = AgentToolCall(
                    name=call.name,
                    arguments={**call.arguments, "input_values": input_values},
                    call_id=call.call_id,
                )
                pending_payloads = [
                    self._call_payload(call)
                    if item.get("name") == call.name
                    else item
                    for item in pending_payloads
                ]
                proposal = self._execute_allowed_tool(call, state).get(
                    "action_draft"
                )
                guided = None
        elif guided is not None and operation_code == "user-variables":
            replacement = self._variable_replacement_prefill(input_source_text)
            if replacement:
                context = dict(guided.get("context") or {})
                context["prefill"] = {"new_member": replacement}
                guided = {**guided, "context": context}
        elif guided is not None and operation_code == "business-rules":
            context = dict(guided.get("context") or {})
            rtp_definition = context.get("rtp_definition")
            prefill = self._business_rule_rtp_prefill(
                input_source_text,
                rtp_definition if isinstance(rtp_definition, dict) else {},
            )
            if prefill:
                context["prefill"] = {
                    "runtime_prompt_mode": "Provide runtime prompt values",
                    "runtime_prompts": prefill,
                }
                guided = {**guided, "context": context}
        elif guided is not None and operation_code == PIPELINE_SCHEDULE_CREATE:
            context = dict(guided.get("context") or {})
            context["prefill"] = {
                "frequency": self._schedule_frequency_prefill(
                    input_source_text
                ),
            }
            guided = {**guided, "context": context}
        if guided is not None:
            input_response = interrupt(
                {"kind": "operation_input_collection", **guided}
            )
            raw_values = (
                input_response.get("values")
                if isinstance(input_response, dict)
                else None
            )
            if raw_values is None:
                return {**state, "approval_decision": "reject"}
            input_values = self._gateway.normalize_guided_inputs(
                str(proposal.get("target_code") or ""),
                artifact_name,
                raw_values,
            )
            call = AgentToolCall(
                name=call.name,
                arguments={**call.arguments, "input_values": input_values},
                call_id=call.call_id,
            )
            pending_payloads = [
                self._call_payload(call)
                if item.get("name") == call.name
                else item
                for item in pending_payloads
            ]
            proposal = self._execute_allowed_tool(call, state).get("action_draft")
            if not isinstance(proposal, dict):
                raise AgentProviderError(
                    "The guided operation inputs could not be resolved."
                )
        response = interrupt(
            {
                "kind": "governed_operation_preparation",
                "operation_code": proposal.get("target_code"),
                "display_name": proposal.get("display_name"),
                "objective": proposal.get("objective"),
                "artifact_name": proposal.get("artifact_name"),
                "category": proposal.get("category"),
                "risk_level": proposal.get("risk_level"),
                "route": proposal.get("route"),
                "effect": self._approval_effect(
                    str(proposal.get("target_code") or "")
                ),
                "input_values": proposal.get("input_values") or {},
            }
        )
        decision = str(
            response.get("decision", "") if isinstance(response, dict) else response
        ).strip().casefold()
        if decision not in {"approve", "reject"}:
            raise AgentProviderError("The approval decision is invalid.")
        return {
            **state,
            "pending_tool_calls": pending_payloads,
            "approval_decision": decision,
        }

    def _standalone_flow_approval_node(
        self,
        state: AgentGraphState,
    ) -> AgentGraphState:
        """Resolve every flow step, then request one execution approval."""
        calls = tuple(
            self._call_from_payload(item)
            for item in state.get("pending_tool_calls", [])
            if item.get("name") == "prepare_standalone_flow_action"
        )
        if len(calls) != 1:
            raise AgentProviderError(
                "The assistant must prepare one standalone flow at a time."
            )
        flow_call = calls[0]
        prepared = self._execute_allowed_tool(flow_call, state).get(
            "standalone_flow"
        )
        if not isinstance(prepared, dict):
            raise AgentProviderError(
                "The standalone flow could not be prepared."
            )
        objective = str(prepared.get("objective") or "").strip()
        requested_steps = tuple(
            str(item).strip().casefold()
            for item in prepared.get("requested_steps", ())
            if str(item).strip()
        )
        configured_steps: list[dict[str, Any]] = []
        occurrence_by_operation: dict[str, int] = {}
        for index, operation_code in enumerate(requested_steps, start=1):
            operation_occurrence = occurrence_by_operation.get(
                operation_code,
                0,
            )
            occurrence_by_operation[operation_code] = operation_occurrence + 1
            step_call = AgentToolCall(
                name="prepare_operation_action",
                arguments={
                    "operation_code": operation_code,
                    "objective": objective,
                },
                call_id=f"standalone-flow-step-{index}",
            )
            step_result = self._execute_allowed_tool(step_call, state).get(
                "action_draft"
            )
            if not isinstance(step_result, dict):
                raise AgentProviderError(
                    f"Standalone flow step {index} could not be resolved."
                )
            try:
                artifact_catalog = self._gateway.artifact_catalog(
                    operation_code
                )
            except Exception as exc:
                self._logger.warning(
                    "Standalone flow artifact discovery failed for '%s': %s",
                    operation_code,
                    exc,
                )
                artifact_catalog = ()
            choices = tuple(item[0] for item in artifact_catalog)
            labels = dict(artifact_catalog)
            mentioned_artifacts = tuple(
                identifier
                for _position, identifier in self._artifact_mentions_in_user_text(
                    artifact_catalog,
                    objective,
                )
            )
            selected = (
                mentioned_artifacts[operation_occurrence]
                if operation_occurrence < len(mentioned_artifacts)
                else (
                    self._artifact_alias_named_in_user_text(labels, objective)
                    or self._artifact_named_in_user_text(choices, objective)
                )
            )
            if selected is None and len(choices) == 1:
                selected = choices[0]
            recovery = self._gateway.artifact_recovery_definition(
                operation_code
            )
            if selected is None and (choices or recovery):
                recommendations = recommend_artifacts(
                    objective,
                    tuple(
                        (item, labels.get(item, item)) for item in choices
                    ),
                )
                answer = interrupt(
                    {
                        "kind": "operation_artifact_selection",
                        "operation_code": operation_code,
                        "display_name": step_result.get("display_name"),
                        "prompt": (
                            f"Step {index} of {len(requested_steps)}: choose "
                            f"the {step_result.get('display_name')} artifact."
                        ),
                        "options": list(choices),
                        "option_labels": labels,
                        "allows_cancel": True,
                        "recommendations": [
                            item.as_payload() for item in recommendations
                        ],
                        "catalog_recovery": recovery,
                        "search_context": objective,
                    }
                )
                selected = str(
                    answer.get("value", "")
                    if isinstance(answer, dict)
                    else answer
                ).strip()
                if not selected:
                    return {**state, "approval_decision": "reject"}
            if not selected:
                raise AgentProviderError(
                    f"No current Oracle artifact is available for standalone "
                    f"flow step {index} ({step_result.get('display_name')})."
                )
            canonical = self._gateway.resolve_artifact_choice(
                operation_code,
                selected,
            )
            if canonical is None:
                raise AgentProviderError(
                    f"The Oracle artifact selected for flow step {index} is "
                    "no longer available."
                )
            guided = self._gateway.guided_input_definition(
                operation_code,
                canonical,
            )
            input_values: dict[str, Any] = {}
            if guided is not None:
                context = dict(guided.get("context") or {})
                context["flow_step"] = {
                    "sequence": index,
                    "total": len(requested_steps),
                }
                answer = interrupt(
                    {
                        "kind": "operation_input_collection",
                        **guided,
                        "context": context,
                    }
                )
                raw_values = (
                    answer.get("values")
                    if isinstance(answer, dict)
                    else None
                )
                if raw_values is None:
                    return {**state, "approval_decision": "reject"}
                input_values = self._gateway.normalize_guided_inputs(
                    operation_code,
                    canonical,
                    raw_values,
                )
            validated_call = AgentToolCall(
                name="prepare_operation_action",
                arguments={
                    "operation_code": operation_code,
                    "objective": objective,
                    "artifact_name": canonical,
                    "input_values": input_values,
                },
                call_id=f"standalone-flow-step-{index}-validated",
            )
            validated = self._execute_allowed_tool(
                validated_call,
                state,
            ).get("action_draft")
            if not isinstance(validated, dict):
                raise AgentProviderError(
                    f"Standalone flow step {index} inputs are invalid."
                )
            configured_steps.append(
                {
                    "sequence": index,
                    "operation_code": operation_code,
                    "display_name": str(
                        validated.get("display_name") or operation_code
                    ),
                    "artifact_name": canonical,
                    "input_values": dict(
                        validated.get("input_values") or {}
                    ),
                    "risk_level": str(
                        validated.get("risk_level") or "Controlled"
                    ),
                }
            )
        configured_call = AgentToolCall(
            name=flow_call.name,
            arguments={
                **flow_call.arguments,
                "configured_steps": configured_steps,
            },
            call_id=flow_call.call_id,
        )
        response = interrupt(
            {
                "kind": "governed_operation_preparation",
                "operation_code": "standalone-flow",
                "display_name": "Standalone Planning Flow",
                "objective": objective,
                "artifact_name": (
                    f"{len(configured_steps)} configured operations"
                ),
                "category": "Orchestration",
                "risk_level": "Elevated",
                "route": "/app/assistant",
                "effect": (
                    "Queue the reviewed operations in this exact order, wait "
                    "for each one to finish, and stop before all remaining "
                    "steps if any operation fails."
                ),
                "input_values": {"steps": configured_steps},
            }
        )
        decision = str(
            response.get("decision", "")
            if isinstance(response, dict)
            else response
        ).strip().casefold()
        if decision not in {"approve", "reject"}:
            raise AgentProviderError("The approval decision is invalid.")
        pending_payloads = [
            self._call_payload(configured_call)
            if item.get("name") == "prepare_standalone_flow_action"
            else item
            for item in state.get("pending_tool_calls", [])
        ]
        return {
            **state,
            "pending_tool_calls": pending_payloads,
            "approval_decision": decision,
        }

    def _artifact_catalog_snapshot(
        self,
        calls: Sequence[AgentToolCall],
    ) -> dict[str, Any] | None:
        """Freeze artifact choices before an interrupt can mutate the catalog."""
        preparation_calls = tuple(
            call for call in calls if call.name == "prepare_operation_action"
        )
        if len(preparation_calls) != 1:
            return None
        operation_code = str(
            preparation_calls[0].arguments.get("operation_code") or ""
        ).strip().casefold()
        if not operation_code:
            return None
        try:
            catalog = self._gateway.artifact_catalog(operation_code)
        except Exception as exc:
            self._logger.warning(
                "Agent artifact snapshot is unavailable for '%s': %s",
                operation_code,
                exc,
            )
            catalog = ()
        return {
            "operation_code": operation_code,
            "items": [[identifier, label] for identifier, label in catalog],
        }

    @classmethod
    def _artifact_named_in_user_text(
        cls,
        choices: Sequence[str],
        user_text: str,
    ) -> str | None:
        """Resolve one unambiguous live artifact explicitly named by the user.

        Oracle artifacts commonly contain underscores while business users type
        spaces. Matching therefore normalizes punctuation and whitespace, but
        never performs fuzzy selection. When two unrelated live names are
        present, the user must still choose explicitly.
        """
        normalized_text = cls._normalize_artifact_text(user_text)
        if not normalized_text:
            return None
        padded_text = f" {normalized_text} "
        matches = [
            choice
            for choice in choices
            if (normalized := cls._normalize_artifact_text(choice))
            and f" {normalized} " in padded_text
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            return None
        matches.sort(
            key=lambda choice: len(cls._normalize_artifact_text(choice)),
            reverse=True,
        )
        longest = cls._normalize_artifact_text(matches[0])
        if all(
            f" {cls._normalize_artifact_text(item)} "
            in f" {longest} "
            for item in matches[1:]
        ):
            return matches[0]
        return None

    @classmethod
    def _artifact_mentions_in_user_text(
        cls,
        catalog: Sequence[tuple[str, str]],
        user_text: str,
    ) -> tuple[tuple[int, str], ...]:
        """Return exact live artifacts in the order the user mentioned them.

        Identifier and display-name aliases at the same text span count once.
        A longer artifact name wins over another catalog name contained inside
        it, while genuinely repeated mentions remain separate flow steps.
        """
        normalized_text = cls._normalize_artifact_text(user_text)
        if not normalized_text:
            return ()
        candidates: list[tuple[int, int, int, str]] = []
        for identifier, display_name in catalog:
            aliases = {
                cls._normalize_artifact_text(identifier),
                cls._normalize_artifact_text(display_name),
            }
            for alias in aliases:
                if not alias:
                    continue
                for match in re.finditer(
                    rf"(?<!\w){re.escape(alias)}(?!\w)",
                    normalized_text,
                ):
                    candidates.append(
                        (match.start(), match.end(), -len(alias), identifier)
                    )
        candidates.sort(key=lambda item: (item[0], item[2], item[1]))
        accepted: list[tuple[int, int, str]] = []
        for start, end, _negative_length, identifier in candidates:
            if any(
                start >= prior_start and end <= prior_end
                for prior_start, prior_end, _prior_identifier in accepted
            ):
                continue
            accepted.append((start, end, identifier))
        accepted.sort(key=lambda item: item[0])
        return tuple((start, identifier) for start, _end, identifier in accepted)

    @classmethod
    def _artifact_alias_named_in_user_text(
        cls,
        display_names: dict[str, str],
        user_text: str,
    ) -> str | None:
        """Resolve one exact business-facing name to its Oracle identifier."""
        normalized_text = cls._normalize_artifact_text(user_text)
        if not normalized_text:
            return None
        padded_text = f" {normalized_text} "
        matches = [
            identifier
            for identifier, display_name in display_names.items()
            if (normalized := cls._normalize_artifact_text(display_name))
            and f" {normalized} " in padded_text
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _normalize_artifact_text(value: str) -> str:
        """Normalize a user-entered or Oracle artifact name for exact matching."""
        return " ".join(re.sub(r"[\W_]+", " ", value.casefold()).split())

    @classmethod
    def _is_artifact_confirmation_reply(cls, user_text: str) -> bool:
        """Recognize a short confirmation without treating a new task as one.

        The prior assistant response is allowed to supply an artifact only
        when this reply is made entirely from confirmation/action words. The
        eventual resolver still requires exactly one current live artifact in
        that response, so conversational carry-over cannot bypass the Oracle
        catalog boundary.
        """
        words = cls._normalize_artifact_text(user_text).split()
        if not words or len(words) > 6:
            return False
        allowed = {
            "yes",
            "yeah",
            "yep",
            "ok",
            "okay",
            "sure",
            "please",
            "prepare",
            "run",
            "execute",
            "start",
            "it",
            "that",
            "one",
            "do",
            "go",
            "ahead",
            "continue",
            "proceed",
        }
        confirmation_words = {
            "yes",
            "yeah",
            "yep",
            "ok",
            "okay",
            "sure",
            "prepare",
            "run",
            "execute",
            "start",
            "do",
            "continue",
            "proceed",
        }
        return all(word in allowed for word in words) and any(
            word in confirmation_words for word in words
        )

    @staticmethod
    def _substitution_variable_creation_prefill(
        user_text: str,
        scopes: Sequence[str],
    ) -> dict[str, str]:
        """Extract only explicit create-variable details from user language.

        The parser deliberately accepts a small set of clear business phrases.
        Ambiguous values remain for the guided card rather than being guessed.
        """
        text = str(user_text or "").strip()
        if not re.search(r"\bcreat(?:e|ing)\b", text, re.IGNORECASE) or not re.search(
            r"\bsubstitution\s+variable\b", text, re.IGNORECASE
        ):
            return {}
        result: dict[str, str] = {}
        pair = re.search(
            r"\bsubstitution\s+variable\s+([A-Za-z][\w.-]{0,79})\s*=\s*([^\s,;.]+)",
            text,
            re.IGNORECASE,
        )
        if pair:
            result["variable_name"] = pair.group(1)
            result["new_value"] = pair.group(2)
        else:
            name_match = re.search(
                r"\b(?:variable\s+)?(?:name(?:d)?|called)\s*(?:is|=|:)?\s*[\"']?([A-Za-z][\w.-]{0,79})",
                text,
                re.IGNORECASE,
            ) or re.search(
                r"\bsubstitution\s+variable\s+([A-Za-z][\w.-]{0,79})(?=\s+(?:with|and|having)\s+(?:an?\s+)?(?:initial\s+)?value\b)",
                text,
                re.IGNORECASE,
            )
            value_match = re.search(
                r"\b(?:initial\s+)?value\s*(?:is|=|:|of)?\s*[\"']?([^\s,;.\"']+)",
                text,
                re.IGNORECASE,
            )
            if name_match:
                result["variable_name"] = name_match.group(1)
            if value_match:
                result["new_value"] = value_match.group(1)
        scope_by_name = {scope.casefold(): scope for scope in scopes}
        if re.search(r"\b(?:application[- ]wide|scope\s*(?:is|=|:)?\s*ALL)\b", text, re.IGNORECASE):
            if "all" in scope_by_name:
                result["scope"] = scope_by_name["all"]
        else:
            scope_match = re.search(
                r"\b(?:scope|cube|plan\s+type)\s*(?:is|=|:)?\s*([A-Za-z][\w.-]{0,99})",
                text,
                re.IGNORECASE,
            )
            if scope_match:
                canonical = scope_by_name.get(scope_match.group(1).casefold())
                if canonical:
                    result["scope"] = canonical
        if "scope" not in result and "all" in scope_by_name:
            # Application scope is the product default for an otherwise
            # complete create request. The final approval still displays ALL.
            result["scope"] = scope_by_name["all"]
        return result

    @staticmethod
    def _is_substitution_variable_creation_request(user_text: str) -> bool:
        """Require explicit create language before exposing creation."""
        text = str(user_text or "")
        return bool(
            re.search(r"\bcreat(?:e|es|ing)\b", text, re.IGNORECASE)
            and re.search(
                r"\b(?:new\s+)?substitution\s+variable\b",
                text,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _variable_replacement_prefill(user_text: str) -> str | None:
        """Extract one explicitly stated replacement value or member.

        Only imperative variable-change phrases are accepted. The text after
        the final ``to``, ``as``, or ``=`` marker is preserved so Planning
        members containing spaces remain valid. Ambiguous prose is left for the
        guided card instead of being guessed.
        """
        text = " ".join(str(user_text or "").strip().split())
        if not text or not re.search(
            r"\b(?:update|set|change|assign)\b",
            text,
            re.IGNORECASE,
        ):
            return None
        matches = tuple(
            re.finditer(r"\s(?:to|as)\s+|\s*=\s*", text, re.IGNORECASE)
        )
        if not matches:
            return None
        value = text[matches[-1].end() :].strip()
        value = re.sub(r"[.!?]+$", "", value).strip().strip("\"'")
        if not value or len(value) > 255:
            return None
        if re.search(r"\b(?:and|then)\s+(?:run|execute|start)\b", value, re.IGNORECASE):
            return None
        return value

    @staticmethod
    def _schedule_frequency_prefill(user_text: str) -> str:
        """Map explicit business recurrence wording to a safe UI default."""
        normalized = " ".join(str(user_text or "").casefold().split())
        if re.search(r"\b(one[ -]?time|once|single run)\b", normalized):
            return "ONE_TIME"
        if re.search(r"\b(daily|every day|each day)\b", normalized):
            return "DAILY"
        if re.search(r"\b(weekly|every week|each week)\b", normalized):
            return "WEEKLY"
        if re.search(r"\b(monthly|every month|each month)\b", normalized):
            return "MONTHLY"
        return "MONTHLY"

    @staticmethod
    def _business_rule_rtp_prefill(
        user_text: str,
        definition: dict[str, Any],
    ) -> dict[str, str]:
        """Extract only values explicitly paired with registered RTP names."""
        text = " ".join(str(user_text or "").strip().split())
        raw_prompts = definition.get("prompts")
        if not text or not isinstance(raw_prompts, list):
            return {}
        result: dict[str, str] = {}
        for raw_prompt in raw_prompts:
            if not isinstance(raw_prompt, dict):
                continue
            name = str(raw_prompt.get("name") or "").strip()
            if not name:
                continue
            aliases = {
                name,
                str(raw_prompt.get("label") or "").strip(),
                str(raw_prompt.get("dimension") or "").strip(),
            }
            aliases.discard("")
            value: str | None = None
            for alias in sorted(aliases, key=len, reverse=True):
                match = re.search(
                    rf"\b{re.escape(alias)}\b\s*(?:=|:|\bis\b|\bto\b)\s*"
                    r"(?:\"([^\"]+)\"|'([^']+)'|([^,;]+?))"
                    r"(?=\s+(?:and|then)\s+|[,;]|$)",
                    text,
                    re.IGNORECASE,
                )
                if match:
                    value = next(
                        (group for group in match.groups() if group is not None),
                        None,
                    )
                    break
            if value is None and any(
                alias.casefold() == "year" for alias in aliases
            ):
                year_match = re.search(r"\bFY\d{2,4}\b", text, re.IGNORECASE)
                if year_match:
                    value = year_match.group(0)
            normalized_value = str(value or "").strip().strip("\"'").rstrip(".!?")
            if normalized_value and len(normalized_value) <= 500:
                result[name] = normalized_value
        return result

    @staticmethod
    def _unambiguous_recommendation(
        recommendations: Sequence[BusinessRuleMatch],
    ) -> str | None:
        """Return one clearly dominant match without guessing an artifact."""
        if not recommendations:
            return None
        top = recommendations[0]
        if top.confidence != "Strong match" or top.score < 65:
            return None
        other_strong = [
            item
            for item in recommendations[1:]
            if item.confidence == "Strong match"
        ]
        if other_strong and top.score - other_strong[0].score < 20:
            return None
        runner_up = recommendations[1] if len(recommendations) > 1 else None
        if runner_up is not None and top.score - runner_up.score < 15:
            return None
        return top.name

    @classmethod
    def _artifact_selected_by_ordinal_reply(
        cls,
        choices: Sequence[str],
        user_text: str,
        previous_assistant_text: str,
    ) -> str | None:
        """Resolve short replies such as 'first' from the prior agent list."""
        ordinal = cls._ordinal_selection(user_text)
        if ordinal is None or not previous_assistant_text:
            return None
        normalized_response = cls._normalize_artifact_text(
            previous_assistant_text
        )
        matches: list[tuple[int, int, int, str]] = []
        for choice in choices:
            normalized_choice = cls._normalize_artifact_text(choice)
            for match in re.finditer(
                rf"(?:^|\s){re.escape(normalized_choice)}(?:\s|$)",
                normalized_response,
            ):
                matches.append(
                    (
                        match.start(),
                        match.end(),
                        -len(normalized_choice),
                        choice,
                    )
                )
        matches.sort(key=lambda item: (item[0], item[2]))
        claimed_spans: list[tuple[int, int]] = []
        mentioned: list[tuple[int, str]] = []
        seen: set[str] = set()
        for start, end, _negative_length, choice in matches:
            if any(
                start >= claimed_start and end <= claimed_end
                for claimed_start, claimed_end in claimed_spans
            ):
                continue
            claimed_spans.append((start, end))
            normalized_choice = choice.casefold()
            if normalized_choice not in seen:
                mentioned.append((start, choice))
                seen.add(normalized_choice)
        mentioned.sort(key=lambda item: item[0])
        ordered = [item[1] for item in mentioned]
        if ordinal == -1:
            return ordered[-1] if ordered else None
        return ordered[ordinal] if ordinal < len(ordered) else None

    @classmethod
    def _ordinal_selection(cls, user_text: str) -> int | None:
        """Return a zero-based ordinal only for an unambiguous short reply."""
        normalized = cls._normalize_artifact_text(user_text)
        words = normalized.split()
        if not words or len(words) > 7:
            return None
        filler = {
            "the",
            "one",
            "option",
            "please",
            "use",
            "select",
            "choose",
            "pick",
            "with",
            "go",
        }
        meaningful = [word for word in words if word not in filler]
        if len(meaningful) != 1:
            return None
        ordinals = {
            "first": 0,
            "1": 0,
            "1st": 0,
            "second": 1,
            "2": 1,
            "2nd": 1,
            "third": 2,
            "3": 2,
            "3rd": 2,
            "fourth": 3,
            "4": 3,
            "4th": 3,
            "fifth": 4,
            "5": 4,
            "5th": 4,
            "last": -1,
        }
        return ordinals.get(meaningful[0])

    @staticmethod
    def _resolve_explicit_operation_intent(
        user_text: str,
        proposed_operation: str,
    ) -> str:
        """Correct the common Data Push versus file-import ambiguity.

        Data Push is Oracle Planning data movement through a Data Map. Data
        Import loads an external file into a cube. Only strong, explicit push
        language can override a model-proposed loading operation.
        """
        normalized = " ".join(
            re.sub(r"[\W_]+", " ", user_text.casefold()).split()
        )
        proposed = proposed_operation.strip().casefold()
        data_push = any(
            re.search(pattern, normalized)
            for pattern in (
                r"\bdata push\b",
                r"\bpush(?:ing)? (?:the )?data\b",
                r"\bpublish(?:ing)? (?:the )?(?:planning )?data\b",
                r"\brun(?:ning)? (?:the )?(?:data )?map\b",
                r"\bexecute (?:the )?(?:data )?map\b",
            )
        )
        if data_push and proposed in {"data-import", "data-integrations"}:
            return "data-maps"
        return proposed

    @staticmethod
    def _approval_effect(operation_code: str) -> str:
        """Describe the exact effect of one explicit assistant approval."""
        normalized = operation_code.strip().casefold()
        if normalized == "business-rules":
            return (
                "Start the selected Business Rule in Oracle and monitor its "
                "execution."
            )
        if normalized == "data-maps":
            return (
                "Start the selected Data Map in Oracle with the reviewed "
                "clear-target and override choices, then monitor its execution."
            )
        if normalized == "pipelines":
            return (
                "Start the selected Oracle Pipeline with the reviewed live "
                "variables and file choices, then monitor the complete run."
            )
        if normalized == "data-integrations":
            return (
                "Start the selected Data Integration with the reviewed period "
                "range, modes, and source file, then monitor the complete load."
            )
        if normalized == "data-import":
            return (
                "Start the selected saved Oracle Planning Import Data job with "
                "the reviewed source file and error-output choice, then monitor "
                "the complete load."
            )
        if normalized == "metadata-import":
            return (
                "Start the selected saved Oracle Planning Import Metadata job "
                "with the reviewed source file, then run the selected Cube "
                "Refresh only if the import succeeds."
            )
        if normalized == "cube-refresh":
            return (
                "Start the exact saved application-wide Cube Refresh job in "
                "Oracle, synchronize Planning metadata with the underlying "
                "cube, and monitor the operation to completion."
            )
        if normalized == "substitution-variables":
            return (
                "Apply exactly one reviewed substitution-variable change in "
                "Oracle and verify the resulting value. Existing-variable "
                "updates are blocked if Oracle changed after selection."
            )
        if normalized == "user-variables":
            return (
                "Apply exactly one reviewed user-variable member assignment "
                "for the selected Oracle user and verify the resulting value."
            )
        if normalized == PIPELINE_SCHEDULE_CREATE:
            return (
                "Create one governed unattended Oracle Pipeline schedule with "
                "the reviewed recurrence, timezone, and input policy."
            )
        if normalized == PIPELINE_SCHEDULE_PAUSE:
            return "Pause the selected schedule; no future occurrence will be claimed."
        if normalized == PIPELINE_SCHEDULE_RESUME:
            return (
                "Revalidate the selected Pipeline against Oracle and resume "
                "its future occurrences."
            )
        return "Prepare a governed handoff; no Oracle action will run."

    def _get_provider(self) -> AgentProvider:
        if self._provider is not None:
            return self._provider
        with self._provider_lock:
            if self._provider is None:
                self._provider = self._provider_factory()
            return self._provider

    def _tool_definitions(
        self,
        state: AgentGraphState,
    ) -> tuple[AgentToolDefinition, ...]:
        allowed = set(state.get("allowed_tool_names", []))
        return tuple(
            item
            for item in self._gateway.definitions()
            if item.name in GRAPH_TOOL_NAMES and item.name in allowed
        )

    def _execute_allowed_tool(
        self,
        call: AgentToolCall,
        state: AgentGraphState,
    ) -> dict[str, Any]:
        allowed = set(state.get("allowed_tool_names", []))
        if call.name not in GRAPH_TOOL_NAMES or call.name not in allowed:
            raise AgentProviderError(
                f"Agent capability '{call.name}' is not permitted for this user."
            )
        return self._gateway.execute(call)

    def delete_thread(self, *, conversation_id: str, user_id: int) -> None:
        """Remove durable graph state when its conversation is deleted."""
        saver = self._checkpoints.get()
        delete = getattr(saver, "delete_thread", None)
        if callable(delete):
            delete(self._thread_id(conversation_id, user_id))

    def _config(self, conversation_id: str, user_id: int) -> dict[str, Any]:
        return {
            "configurable": {
                "thread_id": self._thread_id(conversation_id, user_id)
            }
        }

    def _thread_id(self, conversation_id: str, user_id: int) -> str:
        return f"epm-agent:{self._environment_key}:{user_id}:{conversation_id}"

    @classmethod
    def _approval_from_result(
        cls,
        state: dict[str, Any],
    ) -> AgentApprovalRequest | None:
        pending = state.get("__interrupt__", ())
        if not pending:
            return None
        item = pending[0]
        return cls._approval_from_interrupt(item.id, item.value)

    @classmethod
    def _clarification_from_result(
        cls,
        state: dict[str, Any],
    ) -> AgentClarificationRequest | None:
        pending = state.get("__interrupt__", ())
        if not pending:
            return None
        item = pending[0]
        return cls._clarification_from_interrupt(item.id, item.value)

    @staticmethod
    def _approval_from_interrupt(
        request_id: str,
        value: Any,
    ) -> AgentApprovalRequest | None:
        if not isinstance(value, dict) or value.get("kind") != (
            "governed_operation_preparation"
        ):
            return None
        return AgentApprovalRequest(
            request_id=str(request_id),
            operation_code=str(value.get("operation_code") or ""),
            display_name=str(value.get("display_name") or "Operation"),
            objective=str(value.get("objective") or ""),
            artifact_name=(
                str(value["artifact_name"])
                if value.get("artifact_name")
                else None
            ),
            category=str(value.get("category") or "Operation"),
            risk_level=str(value.get("risk_level") or "Controlled"),
            route=str(value.get("route") or ""),
            effect=str(value.get("effect") or "Prepare a governed handoff."),
            input_values=(
                dict(value.get("input_values") or {})
                if isinstance(value.get("input_values"), dict)
                else {}
            ),
        )

    @staticmethod
    def _clarification_from_interrupt(
        request_id: str,
        value: Any,
    ) -> AgentClarificationRequest | None:
        if not isinstance(value, dict) or value.get("kind") != (
            "operation_artifact_selection"
        ):
            return None
        options = tuple(
            str(item).strip()
            for item in value.get("options", ())
            if str(item).strip()
        )
        recovery = (
            dict(value.get("catalog_recovery") or {})
            if isinstance(value.get("catalog_recovery"), dict)
            else {}
        )
        if not options and not recovery:
            return None
        return AgentClarificationRequest(
            request_id=str(request_id),
            operation_code=str(value.get("operation_code") or ""),
            display_name=str(value.get("display_name") or "Operation"),
            prompt=str(value.get("prompt") or "Choose an Oracle artifact."),
            options=options,
            allows_cancel=bool(value.get("allows_cancel", True)),
            recommendations=tuple(
                dict(item)
                for item in value.get("recommendations", ())
                if isinstance(item, dict)
                and str(item.get("name") or "").strip()
            ),
            option_labels=(
                {
                    str(name): str(label)
                    for name, label in value.get("option_labels", {}).items()
                }
                if isinstance(value.get("option_labels"), dict)
                else {}
            ),
            catalog_recovery=recovery,
            search_context=str(value.get("search_context") or ""),
        )

    @classmethod
    def _input_from_result(
        cls,
        state: dict[str, Any],
    ) -> AgentInputRequest | None:
        pending = state.get("__interrupt__", ())
        if not pending:
            return None
        item = pending[0]
        return cls._input_from_interrupt(item.id, item.value)

    @staticmethod
    def _input_from_interrupt(
        request_id: str,
        value: Any,
    ) -> AgentInputRequest | None:
        if not isinstance(value, dict) or value.get("kind") != (
            "operation_input_collection"
        ):
            return None
        fields = tuple(
            dict(item)
            for item in value.get("fields", ())
            if isinstance(item, dict)
        )
        if not fields:
            return None
        return AgentInputRequest(
            request_id=str(request_id),
            operation_code=str(value.get("operation_code") or ""),
            display_name=str(value.get("display_name") or "Operation"),
            artifact_name=str(value.get("artifact_name") or ""),
            title=str(value.get("title") or "Provide operation inputs"),
            description=str(value.get("description") or ""),
            fields=fields,
            context=(
                dict(value.get("context"))
                if isinstance(value.get("context"), dict)
                else {}
            ),
        )

    def _completed_result(self, state: dict[str, Any]) -> AgentProviderResult:
        text = str(state.get("assistant_text") or "").strip()
        if not text:
            raise AgentProviderError(
                "The EPM Assistant completed without a response. Try again."
            )
        return AgentProviderResult(
            text=text,
            tool_activity=tuple(
                self._activity_from_payload(item)
                for item in state.get("tool_activity", [])
            ),
        )

    @staticmethod
    def _message_payload(message: AgentMessage) -> dict[str, Any]:
        return {
            "message_id": message.message_id,
            "conversation_id": message.conversation_id,
            "role": message.role.value,
            "content": message.content,
            "created_at": message.created_at.isoformat(),
        }

    @staticmethod
    def _message_from_payload(payload: dict[str, Any]) -> AgentMessage:
        return AgentMessage(
            message_id=int(payload["message_id"]),
            conversation_id=str(payload["conversation_id"]),
            role=AgentMessageRole(str(payload["role"])),
            content=str(payload["content"]),
            created_at=datetime.fromisoformat(str(payload["created_at"])),
        )

    @staticmethod
    def _call_payload(call: AgentToolCall) -> dict[str, Any]:
        return {
            "name": call.name,
            "arguments": call.arguments,
            "call_id": call.call_id,
        }

    @staticmethod
    def _call_from_payload(payload: dict[str, Any]) -> AgentToolCall:
        return AgentToolCall(
            name=str(payload["name"]),
            arguments=dict(payload.get("arguments", {})),
            call_id=(str(payload["call_id"]) if payload.get("call_id") else None),
        )

    @staticmethod
    def _activity_payload(activity: AgentToolActivity) -> dict[str, Any]:
        return {
            "name": activity.name,
            "arguments": activity.arguments,
            "status": activity.status,
            "summary": activity.summary,
            "result": activity.result,
        }

    @staticmethod
    def _activity_from_payload(payload: dict[str, Any]) -> AgentToolActivity:
        result = payload.get("result")
        return AgentToolActivity(
            name=str(payload["name"]),
            arguments=dict(payload.get("arguments", {})),
            status=str(payload["status"]),
            summary=str(payload["summary"]),
            result=dict(result) if isinstance(result, dict) else None,
        )

    @staticmethod
    def _tool_summary(result: dict[str, Any]) -> str:
        if "error" in result:
            return str(result["error"])[:240]
        if "action_draft" in result:
            return "Action draft prepared; no Oracle action was executed."
        count = result.get("count")
        if isinstance(count, int):
            return f"Returned {count} item{'s' if count != 1 else ''}."
        return "Read-only platform information returned."

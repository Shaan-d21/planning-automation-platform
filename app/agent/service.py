"""Application service coordinating conversations, providers, and tools."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from pathlib import Path, PurePath
from urllib.parse import urlparse

from app.agent.capabilities import (
    AgentCapabilityGateway,
    PIPELINE_SCHEDULE_ACTIONS,
    PIPELINE_SCHEDULE_CREATE,
    PIPELINE_SCHEDULE_PAUSE,
    PIPELINE_SCHEDULE_RESUME,
)
from app.agent.gemini_provider import GeminiAgentProvider
from app.agent.groq_provider import GroqAgentProvider
from app.agent.checkpoints import AgentCheckpointStore
from app.agent.context import AgentContextResolver
from app.agent.canonical import recognize_explicit_capability
from app.agent.graph import AgentGraphOrchestrator
from app.agent.intent import AgentIntentRouter
from app.agent.semantic_interpreter import (
    AgentSemanticInterpreter,
    semantic_task_understanding,
)
from app.agent.task_state import AgentTaskIntent, AgentTaskInterpreter
from app.agent.models import (
    AgentActionDecision,
    AgentApprovalRequest,
    AgentClarificationRequest,
    AgentInputRequest,
    AgentMessageRole,
    AgentProviderResult,
    AgentToolActivity,
    AgentToolCall,
)
from app.agent.observability import log_agent_decision
from app.agent.preflight import AgentActionPreflightService
from app.agent.provider import AgentProvider
from app.agent.repository import SQLAgentRepository
from app.agent.rule_matching import recommend_artifacts
from app.application.action_inputs import (
    action_input_schema,
    action_input_schema_payload,
    normalize_action_inputs,
)
from app.application.operation_execution_manager import OperationExecutionManager
from app.application.automation_schedule_targets import AutomationScheduleCoordinator
from app.application.automation_scheduling import AutomationScheduleApplicationService
from app.application.standalone_flow import (
    StandaloneFlowInput,
    StandaloneFlowStepInput,
)
from app.application.operations import (
    OPERATION_DEFINITIONS,
    BusinessRuleOperationInput,
    CubeRefreshOperationInput,
    DataImportOperationInput,
    DataIntegrationOperationInput,
    DataMapOperationInput,
    MetadataImportOperationInput,
    PipelineOperationInput,
)
from app.application.substitution_variables import (
    SubstitutionVariableAction,
    SubstitutionVariableOperationInput,
)
from app.application.user_variables import UserVariableOperationInput
from app.models.data_integration import (
    DataIntegrationFileReference,
    DataIntegrationPeriodRange,
)
from app.models.automation_schedule import (
    AutomationConcurrencyPolicy,
    AutomationInputPolicy,
    AutomationMisfirePolicy,
    AutomationScheduleFrequency,
    AutomationScheduleInput,
    AutomationTargetType,
)
from app.models.oracle_artifact import OracleEnvironment
from app.services.data_integration_service import DataIntegrationService
from app.services.data_service import DataService
from app.services.agent_execution_followup_service import (
    AgentExecutionFollowUpService,
)
from app.services.metadata_service import MetadataService
from app.config.settings import Settings
from app.models.access_control import (
    ExecutionActor,
    Permission,
    RoleCode,
    TriggerSource,
    UserAccount,
)
from app.utils.exceptions import (
    AgentCapabilityError,
    AgentConfigurationError,
    AgentConversationError,
    AgentError,
    ConfigurationError,
    EPMError,
)


ProviderFactory = Callable[[], AgentProvider]

_ROLE_LABELS = {
    RoleCode.SERVICE_ADMINISTRATOR: "Service Administrator",
    RoleCode.POWER_USER: "Power User",
    RoleCode.USER: "User",
    RoleCode.VIEWER: "Viewer",
}

_PERMISSION_LABELS = {
    Permission.PROCESS_RUN: "Run assigned Planning processes",
    Permission.PROCESS_DESIGN: "Design and administer Planning processes",
    Permission.SCHEDULE_MANAGE: "Create and manage automation schedules",
    Permission.OPERATION_EXECUTE: "Execute governed Oracle operations",
    Permission.VARIABLE_UPDATE: "Update substitution variables",
    Permission.USER_VARIABLE_UPDATE: "Update Planning user variables",
    Permission.DATA_REVIEW: "Review authorized Planning data",
    Permission.REPORT_GENERATE: "Generate and export reports",
    Permission.HISTORY_VIEW: "View execution history and evidence",
    Permission.USER_MANAGE: "Manage platform users and access mappings",
    Permission.CATALOG_MANAGE: "Manage registered Oracle artifact catalogs",
    Permission.AGENT_USE: "Use EPM Assistant",
}

SYSTEM_INSTRUCTION = """
You are the governed operational assistant inside BISP Solutions Oracle EPM
Automation. Help Oracle EPM consultants, planners, finance users, and
administrators accomplish business tasks conversationally, while the platform's
deterministic services remain responsible for validation and execution. Use the
provided tools when the answer depends on configured or live platform
information. Clearly distinguish tool-confirmed facts from general Oracle EPM
guidance.

Safety rules:
- You cannot execute, schedule, modify, upload, delete, approve, or retry work
  autonomously. A deterministic platform workflow may submit a supported
  operation or save a supported Pipeline schedule only after the user explicitly
  approves the exact reviewed inputs.
- When a user clearly wants to perform an operation, use the preparation tool
  to create an exact reviewable proposal. Supported direct operations run only
  after explicit platform approval; all others become governed action drafts.
- Preserve the current structured business-task context across short answers
  and corrections. If required information is missing, ask only one focused
  question at a time instead of presenting a long technical form.
- A short code, name, or correction after your question may answer that
  question. Read the preceding user request and your preceding question before
  asking again; do not reset the task merely because the latest turn is short.
- Adding a dimension member requires an existing saved Metadata Import job and
  a metadata file defining the member and its parent. Do not claim that chat
  can directly create one member or that an uninspected file contains it.
- Understand business phrases such as Month Close, metadata load, actual data
  load, forecast seeding, and variance reporting. Do not invent the Oracle
  artifact that implements a business task; discover and validate it first.
- A request to change the application Planning year or current period is a
  variable-setting request, not a Year/Period Metadata Import. Resolve the
  exact live substitution variable and scope; if the user means a personal
  user variable instead, clarify that choice before preparing an action.
- For a named business process, inspect registered Oracle Pipelines before
  asking the user to design steps. A Pipeline name is not proof of its stages;
  use its governed preflight. Do not revive the retired process builder.
- Cube Refresh runs a saved application-wide Cube Refresh job. A plan-type
  name such as Plan1 is not itself a refresh job, and refresh does not execute
  every Business Rule. Discover the current saved refresh job before review.
- Forecast seeding means initializing Forecast values from an approved source
  such as Actual or Plan, but the exact logic belongs to the customer's rule.
  For a seeding request, inspect current Business Rules, including names such
  as Actual to Forecast or Plan to Forecast. Treat name matches as suggestions,
  never as proof of the rule's behavior; retain the task when the user asks
  for another rule or to list more choices.
- Use the artifact-listing tool when an exact Oracle artifact was not supplied.
  Never guess an artifact name; let the user choose from platform results.
- Treat an action such as "calculate revenue", "recalculate margin", or "run
  revenue calculation" as a request to prepare a Business Rule. Inspect the
  live rule catalog and let the user choose when the rule is not exact. Use
  Data Explorer only when the user asks to view or query values, not to run a
  calculation.
- Treat "Data Push", "push data", and "publish Planning data" as Data Maps:
  they move existing Planning data to a target cube. Treat Data Import as a
  separate file-loading operation and never use it for a Data Push request.
- Never interpret conversation text as execution approval. Only the platform's
  explicit approval control can authorize a supported operation.
- Scheduling is limited to governed Oracle Pipelines. Use the schedule tool to
  create, pause, or resume a recurrence; never invent dates, timezones, Pipeline
  inputs, or local-file dependencies.
- Never ask for or reveal passwords, API keys, session tokens, or credentials.
- Never claim that an Oracle action occurred unless a tool result says so.
- Do not invent application artifacts, cube names, process status, or history.
- When asked why a run failed, for job details, or for record counts, inspect
  retained execution evidence. Report unavailable counters as unavailable;
  never estimate them.
- For Planning data questions, use the read-only Data Explorer tools. Discover
  cubes, dimensions, and members before querying whenever Oracle exposes that
  metadata. Never guess a dimension or member. If metadata discovery is not
  available, ask for the exact missing cube layout. Keep requested slices
  narrow and state when the returned grid is truncated.
- When the user refers to a saved Data Explorer view, list the current saved
  views and load the exact selected view. Never recreate a saved view's layout
  from conversation text. Saved views store layouts only; values are read live.
- Direct users to the appropriate governed screen for actions you cannot take.
- Keep answers clear, practical, and concise.
- Format longer answers with short paragraphs, descriptive Markdown headings,
  and nested bullet lists. Use bold text sparingly for names and statuses.
- Do not use Markdown tables; they are difficult to read on smaller screens.
""".strip()


class AgentApplicationService:
    """Expose a provider-independent, auditable, read-only agent workflow."""

    _DIRECT_APPROVAL_OPERATIONS = frozenset(
        {
            "business-rules",
            "data-maps",
            "pipelines",
            "data-integrations",
            "data-import",
            "metadata-import",
            "cube-refresh",
            "substitution-variables",
            "user-variables",
            "standalone-flow",
            PIPELINE_SCHEDULE_CREATE,
            PIPELINE_SCHEDULE_PAUSE,
            PIPELINE_SCHEDULE_RESUME,
        }
    )

    def __init__(
        self,
        settings: Settings,
        *,
        gateway: AgentCapabilityGateway,
        preflight: AgentActionPreflightService | None = None,
        repository: SQLAgentRepository | None = None,
        provider_factory: ProviderFactory | None = None,
        graph_orchestrator: AgentGraphOrchestrator | None = None,
        operation_manager: OperationExecutionManager | None = None,
        schedule_service: AutomationScheduleApplicationService | None = None,
        schedule_coordinator: AutomationScheduleCoordinator | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings
        self._gateway = gateway
        self._preflight = preflight
        self._repository = repository or SQLAgentRepository(
            settings.database_target
        )
        self._provider_factory = provider_factory or self._default_provider
        self._logger = logger or logging.getLogger(__name__)
        self._graph = graph_orchestrator
        self._operation_manager = operation_manager
        self._execution_followups = AgentExecutionFollowUpService(
            settings.database_target,
            repository=self._repository,
            logger=self._logger.getChild("execution_followups"),
        )
        self._schedule_service = schedule_service
        self._schedule_coordinator = schedule_coordinator
        self._schedule_environment_key = OracleEnvironment.from_settings(
            settings.epm_base_url,
            settings.application_name,
        ).key
        if self._graph is None and settings.agent_orchestrator == "langgraph":
            self._graph = AgentGraphOrchestrator(
                provider_factory=self._provider_factory,
                gateway=self._gateway,
                checkpointer=AgentCheckpointStore(
                    settings.database_target,
                    logger=self._logger.getChild("checkpoints"),
                ),
                system_instruction=SYSTEM_INSTRUCTION,
                max_tool_rounds=settings.agent_max_tool_rounds,
                environment_key=(
                    f"{settings.epm_base_url}|{settings.application_name}"
                ),
                logger=self._logger.getChild("graph"),
            )

    def status(self) -> dict[str, object]:
        provider_keys = {
            "gemini": self._settings.gemini_api_key,
            "groq": self._settings.groq_api_key,
        }
        configured = bool(provider_keys.get(self._settings.agent_provider))
        supported = self._settings.agent_provider in provider_keys
        key_name = (
            "GROQ_API_KEY"
            if self._settings.agent_provider == "groq"
            else "GEMINI_API_KEY"
        )
        return {
            "enabled": configured and supported,
            "configured": configured,
            "provider": self._settings.agent_provider,
            "model": self._settings.agent_model,
            "orchestrator": self._settings.agent_orchestrator,
            "mode": "governed",
            "message": (
                "Agent is ready."
                if configured and supported
                else f"Add {key_name} to .env and restart the application."
                if supported
                else f"Agent provider '{self._settings.agent_provider}' is not installed."
            ),
        }

    def list_conversations(self, user: UserAccount):
        self._require_agent_use(user)
        return self._repository.list_conversations(user.user_id)

    def create_conversation(self, user: UserAccount):
        self._require_agent_use(user)
        return self._repository.create_conversation(
            user_id=user.user_id,
            provider=self._settings.agent_provider,
            model=self._settings.agent_model,
        )

    def get_messages(self, conversation_id: str, user: UserAccount):
        self._require_agent_use(user)
        try:
            self._execution_followups.publish_for_conversation(
                conversation_id,
                user.user_id,
            )
        except Exception:
            self._logger.exception(
                "Unable to reconcile agent execution follow-ups for "
                "conversation '%s'.",
                conversation_id,
            )
        return self._repository.list_messages(conversation_id, user.user_id)

    def get_action_drafts(self, conversation_id: str, user: UserAccount):
        self._require_agent_use(user)
        return tuple(
            self._with_input_schema(item)
            for item in self._repository.list_action_drafts(
                conversation_id, user.user_id
            )
        )

    def preflight_action_draft(self, draft_id: str, user: UserAccount):
        """Validate one owned draft without executing its target action."""
        self._require_agent_use(user)
        if self._preflight is None:
            raise AgentConfigurationError(
                "Agent action preflight is not configured."
            )
        draft = self._repository.get_action_draft(draft_id, user.user_id)
        status, checks = self._preflight.preflight(draft, user)
        validated = self._repository.record_action_preflight(
            draft_id=draft.draft_id,
            user_id=user.user_id,
            status=status,
            checks=checks,
        )
        self._logger.info(
            "Agent action preflight completed: user='%s', draft='%s', "
            "status='%s'.",
            user.username,
            draft.draft_id,
            status,
        )
        return self._with_input_schema(validated)

    def update_action_draft_inputs(
        self,
        draft_id: str,
        user: UserAccount,
        values: dict[str, object],
    ):
        """Save structured inputs without executing or approving the draft."""
        self._require_agent_use(user)
        draft = self._repository.get_action_draft(draft_id, user.user_id)
        try:
            schema = action_input_schema(draft.action_type, draft.target_code)
            normalized = normalize_action_inputs(schema, values)
        except ConfigurationError as exc:
            raise AgentConversationError(str(exc)) from exc
        if draft.action_type == "process":
            for legacy_key in ("runtime_variables", "inbox_files"):
                legacy_value = draft.input_values.get(legacy_key)
                if isinstance(legacy_value, dict) and legacy_value:
                    normalized[legacy_key] = dict(legacy_value)
        updated = self._repository.update_action_inputs(
            draft_id=draft.draft_id,
            user_id=user.user_id,
            input_values=normalized,
        )
        self._logger.info(
            "Agent action inputs saved: user='%s', draft='%s'.",
            user.username,
            draft.draft_id,
        )
        return self._with_input_schema(updated)

    @staticmethod
    def _with_input_schema(draft):
        """Apply the current contract and migrate legacy process values."""
        try:
            schema = action_input_schema(draft.action_type, draft.target_code)
        except ConfigurationError:
            return draft
        values = dict(draft.input_values)
        if draft.action_type == "process":
            runtime = values.get("runtime_variables", {})
            if isinstance(runtime, dict):
                aliases = {
                    "planning_year": ("YEAR",),
                    "start_period": ("STARTPERIOD", "START PERIOD"),
                    "end_period": ("ENDPERIOD", "END PERIOD"),
                }
                folded = {
                    str(name).strip().casefold(): str(value).strip()
                    for name, value in runtime.items()
                }
                for field, names in aliases.items():
                    if values.get(field):
                        continue
                    match = next(
                        (
                            folded[name.casefold()]
                            for name in names
                            if folded.get(name.casefold())
                        ),
                        None,
                    )
                    if match:
                        values[field] = match
        return replace(
            draft,
            input_schema=action_input_schema_payload(schema),
            input_values=values,
            required_inputs=(
                (
                    "Planning year",
                    "Start period when the process uses a period range",
                    "End period when the process uses a period range",
                    "Final review and approval on the governed screen",
                )
                if draft.action_type == "process"
                else draft.required_inputs
            ),
        )

    def resolve_action_handoff(
        self,
        *,
        draft_id: str,
        user: UserAccount,
        expected_target_code: str,
        request_path: str,
    ):
        """Resolve a validated, user-owned draft for one governed page."""
        self._require_agent_use(user)
        draft = self._repository.get_action_draft(draft_id, user.user_id)
        if draft.preflight_status != "READY_FOR_GOVERNED_REVIEW":
            raise AgentConversationError(
                "Validate this action preparation in EPM Assistant before "
                "opening its governed screen."
            )
        if draft.target_code.casefold() != expected_target_code.casefold():
            raise AgentConversationError(
                "This action preparation does not match the requested screen."
            )
        if urlparse(draft.route).path != request_path:
            raise AgentConversationError(
                "This action preparation has an invalid governed destination."
            )
        return self._with_input_schema(draft)

    def resolve_operation_handoff(
        self,
        *,
        draft_id: str,
        user: UserAccount,
        target_code: str,
    ):
        """Resolve one validated draft for the modern operation workspace."""
        self._require_agent_use(user)
        draft = self._repository.get_action_draft(draft_id, user.user_id)
        if draft.preflight_status != "READY_FOR_GOVERNED_REVIEW":
            raise AgentConversationError(
                "Validate this action preparation in EPM Assistant before "
                "opening its governed screen."
            )
        definition = next(
            (
                item
                for item in OPERATION_DEFINITIONS
                if item.code.casefold() == target_code.strip().casefold()
            ),
            None,
        )
        if definition is None or draft.target_code.casefold() != (
            definition.code.casefold()
        ):
            raise AgentConversationError(
                "This action preparation does not match the requested operation."
            )
        if urlparse(draft.route).path != definition.route:
            raise AgentConversationError(
                "This action preparation has an invalid governed destination."
            )
        return self._with_input_schema(draft)

    def delete_conversation(self, conversation_id: str, user: UserAccount) -> None:
        self._require_agent_use(user)
        if self._repository.get_conversation(
            conversation_id,
            user.user_id,
        ) is None:
            raise AgentConversationError("Agent conversation was not found.")
        if self._graph is not None:
            self._graph.delete_thread(
                conversation_id=conversation_id,
                user_id=user.user_id,
            )
        self._repository.delete_conversation(conversation_id, user.user_id)

    def get_pending_approval(
        self,
        conversation_id: str,
        user: UserAccount,
    ):
        """Return a durable approval interrupt owned by this user."""
        self._require_agent_use(user)
        if self._repository.get_conversation(
            conversation_id,
            user.user_id,
        ) is None:
            raise AgentConversationError("Agent conversation was not found.")
        if self._graph is None:
            return None
        return self._graph.pending_approval(
            conversation_id=conversation_id,
            user_id=user.user_id,
        )

    def get_pending_clarification(
        self,
        conversation_id: str,
        user: UserAccount,
    ):
        """Return a durable structured choice owned by this user."""
        self._require_agent_use(user)
        if self._repository.get_conversation(
            conversation_id,
            user.user_id,
        ) is None:
            raise AgentConversationError("Agent conversation was not found.")
        if self._graph is None:
            return None
        pending = self._graph.pending_clarification(
            conversation_id=conversation_id,
            user_id=user.user_id,
        )
        return self._refresh_clarification_catalog(pending, user)

    def synchronize_clarification_catalog(
        self,
        *,
        conversation_id: str,
        user: UserAccount,
        request_id: str,
    ) -> AgentClarificationRequest:
        """Synchronize safely discoverable artifacts for a pending choice."""
        pending = self._require_recoverable_clarification(
            conversation_id=conversation_id,
            user=user,
            request_id=request_id,
        )
        self._require_catalog_management(user)
        try:
            sync_result = self._gateway.synchronize_artifact_catalog()
        except AgentError:
            raise
        except EPMError as exc:
            raise AgentConversationError(str(exc)) from exc
        if getattr(sync_result, "oracle_available", True) is False:
            raise AgentConversationError(
                str(
                    getattr(sync_result, "message", "")
                    or "Oracle could not be reached for catalog synchronization."
                )
            )
        refreshed = self._refresh_clarification_catalog(pending, user)
        assert refreshed is not None
        return refreshed

    def register_clarification_artifact(
        self,
        *,
        conversation_id: str,
        user: UserAccount,
        request_id: str,
        identifier: str,
    ) -> dict[str, object]:
        """Register one exact artifact, then continue the interrupted graph."""
        pending = self._require_recoverable_clarification(
            conversation_id=conversation_id,
            user=user,
            request_id=request_id,
        )
        self._require_catalog_management(user)
        try:
            canonical = self._gateway.register_artifact(
                pending.operation_code,
                identifier,
            )
        except AgentError:
            raise
        except EPMError as exc:
            raise AgentConversationError(str(exc)) from exc
        return self.resolve_clarification(
            conversation_id=conversation_id,
            user=user,
            request_id=request_id,
            value=canonical,
        )

    def _require_recoverable_clarification(
        self,
        *,
        conversation_id: str,
        user: UserAccount,
        request_id: str,
    ) -> AgentClarificationRequest:
        pending = self.get_pending_clarification(conversation_id, user)
        if pending is None:
            raise AgentConversationError(
                "No agent artifact choice is awaiting catalog recovery."
            )
        if pending.request_id != request_id:
            raise AgentConversationError(
                "This artifact choice is stale. Refresh the conversation."
            )
        if not pending.catalog_recovery.get("enabled"):
            raise AgentConversationError(
                "Catalog recovery is not supported for this operation."
            )
        return pending

    @staticmethod
    def _require_catalog_management(user: UserAccount) -> None:
        if not user.has_permission(Permission.CATALOG_MANAGE):
            raise AgentConversationError(
                "Catalog synchronization and registration require the "
                "Catalog Manager permission. Ask a Service Administrator "
                "to register this Oracle artifact."
            )

    def _refresh_clarification_catalog(
        self,
        pending: AgentClarificationRequest | None,
        user: UserAccount,
    ) -> AgentClarificationRequest | None:
        """Overlay the latest environment-scoped catalog on an interrupt."""
        if pending is None or not pending.catalog_recovery.get("enabled"):
            return pending
        catalog = self._gateway.artifact_catalog(pending.operation_code)
        options = tuple(identifier for identifier, _ in catalog)
        labels = dict(catalog)
        recommendations = tuple(
            item.as_payload()
            for item in recommend_artifacts(pending.search_context, catalog)
        )
        recovery = {
            **pending.catalog_recovery,
            "can_manage": user.has_permission(Permission.CATALOG_MANAGE),
        }
        return replace(
            pending,
            options=options,
            option_labels=labels,
            recommendations=recommendations,
            catalog_recovery=recovery,
        )

    def get_pending_input(
        self,
        conversation_id: str,
        user: UserAccount,
    ):
        """Return a durable structured input request owned by this user."""
        self._require_agent_use(user)
        if self._repository.get_conversation(
            conversation_id,
            user.user_id,
        ) is None:
            raise AgentConversationError("Agent conversation was not found.")
        if self._graph is None:
            return None
        pending = self._graph.pending_input(
            conversation_id=conversation_id,
            user_id=user.user_id,
        )
        return self._personalize_input_request(pending, user)

    def get_data_review_context(
        self,
        conversation_id: str,
        user: UserAccount,
    ) -> dict[str, object] | None:
        """Return the latest selection-only context for conversation resume."""
        self._require_agent_use(user)
        if self._repository.get_conversation(
            conversation_id,
            user.user_id,
        ) is None:
            raise AgentConversationError("Agent conversation was not found.")
        return self._latest_data_review_context(conversation_id, user)

    def send_message(
        self,
        *,
        conversation_id: str,
        user: UserAccount,
        content: str,
    ) -> dict[str, object]:
        self._require_agent_use(user)
        prompt = content.strip()
        if not prompt:
            raise AgentConfigurationError("Enter a question for the agent.")
        if len(prompt) > 4_000:
            raise AgentConfigurationError(
                "Agent messages cannot exceed 4,000 characters."
            )
        self._logger.info(
            "Agent turn started: conversation_id='%s', user_id=%s, "
            "application='%s', message_chars=%d.",
            conversation_id,
            user.user_id,
            self._settings.application_name,
            len(prompt),
        )
        pending_approval = self.get_pending_approval(conversation_id, user)
        pending_clarification = self.get_pending_clarification(
            conversation_id, user
        )
        pending_input = self.get_pending_input(conversation_id, user)
        switch = re.fullmatch(
            r"\s*(?:actually\s+)?cancel\s+(?:this|the\s+current)"
            r"(?:\s+(?:task|request|proposal))?\s+and\s+(.+?)\s*",
            prompt,
            re.IGNORECASE,
        )
        if switch and (pending_approval or pending_clarification or pending_input):
            next_request = switch.group(1).strip()
            if pending_approval is not None:
                self.resolve_approval(
                    conversation_id=conversation_id,
                    user=user,
                    request_id=pending_approval.request_id,
                    decision="reject",
                )
            elif pending_clarification is not None:
                self.resolve_clarification(
                    conversation_id=conversation_id,
                    user=user,
                    request_id=pending_clarification.request_id,
                    value=None,
                )
            else:
                self.resolve_input(
                    conversation_id=conversation_id,
                    user=user,
                    request_id=pending_input.request_id,
                    values=None,
                )
            return self.send_message(
                conversation_id=conversation_id,
                user=user,
                content=next_request,
            )
        if pending_clarification is not None or pending_input is not None:
            continuation = self._continue_pending_chat_reply(
                conversation_id=conversation_id,
                user=user,
                prompt=prompt,
                clarification=pending_clarification,
                input_request=pending_input,
            )
            if continuation is not None:
                return continuation
        if pending_approval or pending_clarification or pending_input:
            raise AgentConversationError(
                "Complete or cancel the pending review card before sending "
                "another message. Chat cannot approve an Oracle operation."
            )
        self._repository.add_message(
            conversation_id=conversation_id,
            user_id=user.user_id,
            role=AgentMessageRole.USER,
            content=prompt,
        )
        if self._is_current_user_access_request(prompt):
            return self._persist_agent_result(
                conversation_id=conversation_id,
                user=user,
                result=self._current_user_access_result(user),
            )
        messages = self._repository.list_messages(
            conversation_id,
            user.user_id,
            limit=self._settings.agent_history_messages,
        )
        data_review_context = self._latest_data_review_context(
            conversation_id,
            user,
        )
        prior_task_context = (
            self._graph.current_task_context(
                conversation_id=conversation_id,
                user_id=user.user_id,
            )
            if self._graph is not None
            and callable(getattr(self._graph, "current_task_context", None))
            else None
        )
        task_understanding = AgentTaskInterpreter.interpret(
            messages,
            prior_context=prior_task_context,
        )
        semantic_interpretation = None
        explicit_canonical_interpretation = None
        active_prior_context = (
            isinstance(prior_task_context, dict)
            and str(prior_task_context.get("intent") or "").upper()
            not in {"", "UNKNOWN"}
            and str(prior_task_context.get("phase") or "").upper()
            not in {"COMPLETED", "FAILED", "CANCELLED"}
        )
        retain_active_context = (
            task_understanding.intent is AgentTaskIntent.UNKNOWN
            and active_prior_context
        )
        if (
            task_understanding.intent is AgentTaskIntent.UNKNOWN
            and not retain_active_context
        ):
            explicit_canonical_interpretation = recognize_explicit_capability(
                prompt
            )
            semantic_interpretation = explicit_canonical_interpretation
            if semantic_interpretation is None:
                semantic_interpretation = AgentSemanticInterpreter.interpret(
                    provider=self._provider_factory(),
                    messages=messages,
                    prior_context=prior_task_context,
                    logger=self._logger,
                )
            if (
                semantic_interpretation is not None
                and explicit_canonical_interpretation is None
            ):
                task_understanding = semantic_task_understanding(
                    semantic_interpretation,
                    fallback_objective=prompt,
                )
        if retain_active_context:
            # The active checkpoint is authoritative while waiting for an
            # answer. An unclear reply must not create a new task or trigger a
            # second provider call; the graph will repeat the focused prompt.
            task_context = dict(prior_task_context)
        else:
            canonical_context = AgentContextResolver.resolve(
                task_understanding.to_payload(),
                prior_context=prior_task_context,
                canonical_override=(
                    semantic_interpretation.capability
                    if semantic_interpretation is not None else None
                ),
                action_override=(
                    semantic_interpretation.action_mode
                    if semantic_interpretation is not None else None
                ),
            )
            task_context = canonical_context.model_dump(mode="json")
        if semantic_interpretation is not None:
            task_context["execution_requested"] = (
                semantic_interpretation.execution_requested
            )
            task_context["negated"] = semantic_interpretation.negated
            task_context["referenced_entity"] = (
                semantic_interpretation.referenced_entity
            )
            if semantic_interpretation.entity_name:
                task_context["resolved_entity"] = {
                    "entity_type": semantic_interpretation.entity_type,
                    "candidate_name": semantic_interpretation.entity_name,
                    "canonical_name": None,
                    "status": "unresolved",
                    "candidates": [],
                    "catalog_source": None,
                }
        if task_understanding.intent.value == "CANCEL_OPERATION":
            stopped = self._stop_latest_active_flow(
                conversation_id=conversation_id,
                user=user,
            )
            if stopped is not None:
                return stopped
        if self._settings.agent_orchestrator == "langgraph":
            if self._graph is None:
                raise AgentConfigurationError(
                    "LangGraph orchestration is not configured."
                )
            authorized_executions = self._authorized_execution_ids(
                conversation_id, user.user_id
            )
            intent = AgentIntentRouter.route(
                prompt,
                self._allowed_tool_names(user),
                has_data_review_context=data_review_context is not None,
                has_execution_context=bool(authorized_executions),
                task_intent=task_understanding.intent.value,
                canonical_capability=str(
                    task_context.get("canonical_capability") or "unknown"
                ),
                action_mode=str(task_context.get("action_mode") or "unknown"),
            )
            self._logger.info(
                "Agent intent classified: conversation_id='%s', user_id=%s, "
                "intent='%s', task_intent='%s', canonical_capability='%s', "
                "task_id='%s', task_phase='%s', task_confidence='%s', "
                "missing_parameters=%s.",
                conversation_id,
                user.user_id,
                intent.intent.value,
                task_understanding.intent.value,
                str(task_context.get("canonical_capability") or "unknown"),
                str(task_context.get("task_id") or "legacy-checkpoint"),
                str(task_context.get("phase") or task_understanding.phase.value),
                str(
                    task_context.get("confidence")
                    or task_understanding.confidence.value
                ),
                list(task_context.get("missing_parameters") or ()),
            )
            log_agent_decision(
                self._logger,
                conversation_id=conversation_id,
                user_id=user.user_id,
                task_context=task_context,
                routed_intent=intent.intent.value,
                allowed_tools=intent.tool_names,
            )
            result = self._graph.invoke(
                conversation_id=conversation_id,
                user_id=user.user_id,
                messages=messages,
                allowed_tool_names=intent.tool_names,
                data_review_context=data_review_context,
                task_context=task_context,
                authorized_execution_ids=authorized_executions,
            )
        else:
            provider = self._provider_factory()
            allowed = self._allowed_tool_names(user)
            result = provider.respond(
                messages=messages,
                system_instruction=self._instruction_with_data_review_context(
                    data_review_context
                ),
                tools=tuple(
                    item
                    for item in self._gateway.definitions()
                    if item.name in allowed
                ),
                execute_tool=lambda call: self._execute_user_tool(
                    call,
                    allowed,
                    conversation_id=conversation_id,
                    user_id=user.user_id,
                ),
            )
        return self._persist_agent_result(
            conversation_id=conversation_id,
            user=user,
            result=result,
        )

    def _continue_pending_chat_reply(
        self,
        *,
        conversation_id: str,
        user: UserAccount,
        prompt: str,
        clarification: AgentClarificationRequest | None,
        input_request: AgentInputRequest | None,
    ) -> dict[str, object] | None:
        """Resume only unambiguous, non-approval interrupt answers from chat."""
        normalized = prompt.strip().casefold()
        cancel = normalized in {
            "cancel", "cancel this", "cancel this task", "never mind", "nevermind"
        }
        if clarification is not None:
            choice = next(
                (
                    option
                    for option in clarification.options
                    if option.casefold() == normalized
                ),
                None,
            )
            if not cancel and choice is None:
                return None
            self._repository.add_message(
                conversation_id=conversation_id,
                user_id=user.user_id,
                role=AgentMessageRole.USER,
                content=prompt,
            )
            return self.resolve_clarification(
                conversation_id=conversation_id,
                user=user,
                request_id=clarification.request_id,
                value=None if cancel else choice,
            )
        if input_request is None:
            return None
        if cancel:
            values: dict[str, object] | None = None
        elif input_request.operation_code == "business-rules":
            definition = input_request.context.get("rtp_definition")
            prompts = (
                definition.get("prompts")
                if isinstance(definition, dict)
                else None
            )
            visible = [
                item for item in prompts
                if isinstance(item, dict) and not item.get("hidden")
            ] if isinstance(prompts, list) else []
            match = re.fullmatch(
                r"\s*([+-]?\d+(?:\.\d+)?)\s*(?:%|percent)?\s*",
                prompt,
                re.IGNORECASE,
            )
            if len(visible) != 1 or match is None:
                return None
            name = str(visible[0].get("name") or "").strip()
            if not name:
                return None
            values = {
                "runtime_prompt_mode": "Provide runtime prompt values",
                "runtime_prompts": {name: match.group(1)},
            }
        else:
            values = self._single_pending_field_values(input_request, prompt)
            if values is None:
                return None
        self._repository.add_message(
            conversation_id=conversation_id,
            user_id=user.user_id,
            role=AgentMessageRole.USER,
            content=prompt,
        )
        return self.resolve_input(
            conversation_id=conversation_id,
            user=user,
            request_id=input_request.request_id,
            values=values,
        )

    @staticmethod
    def _single_pending_field_values(
        input_request: AgentInputRequest,
        prompt: str,
    ) -> dict[str, object] | None:
        """Fill one simple pending field; complex/file inputs remain on the card."""
        context = (
            input_request.context
            if isinstance(input_request.context, dict)
            else {}
        )
        prefill = context.get("prefill")
        values: dict[str, object] = (
            dict(prefill) if isinstance(prefill, dict) else {}
        )
        unsupported_kinds = {
            "file_reference",
            "pipeline_file",
            "pipeline_review",
            "pipeline_variable",
            "schedule",
        }
        pending = []
        for field in input_request.fields:
            if not isinstance(field, dict) or not field.get("required"):
                continue
            key = str(field.get("key") or "").strip()
            if not key or key in values:
                continue
            kind = str(field.get("kind") or "text").strip().casefold()
            if kind in unsupported_kinds:
                return None
            pending.append(field)
        if len(pending) != 1:
            return None
        field = pending[0]
        key = str(field.get("key") or "").strip()
        kind = str(field.get("kind") or "text").strip().casefold()
        raw = str(prompt or "").strip()
        if not raw or len(raw) > 500:
            return None
        if kind == "choice":
            options = [str(item) for item in field.get("options", ())]
            value = next(
                (item for item in options if item.casefold() == raw.casefold()),
                None,
            )
            if value is None:
                return None
        elif kind == "boolean":
            normalized = raw.casefold()
            if normalized in {"yes", "y", "true", "1"}:
                value = True
            elif normalized in {"no", "n", "false", "0"}:
                value = False
            else:
                return None
        elif kind in {"number", "integer"}:
            if re.fullmatch(r"[+-]?\d+", raw) is None:
                return None
            value = int(raw)
        elif kind == "key_value":
            pairs: dict[str, str] = {}
            for line in raw.splitlines():
                name, separator, item_value = line.partition("=")
                if not separator or not name.strip() or not item_value.strip():
                    return None
                pairs[name.strip()] = item_value.strip()
            value = pairs
        elif kind == "text":
            value = raw
        else:
            return None
        values[key] = value
        return values

    def _latest_data_review_context(
        self,
        conversation_id: str,
        user: UserAccount,
    ) -> dict[str, object] | None:
        """Load only prior validated slice selections, never grid values."""
        if not user.has_permission(Permission.DATA_REVIEW):
            return None
        activity = self._repository.latest_successful_tool_activity(
            conversation_id=conversation_id,
            user_id=user.user_id,
            tool_names=(
                "review_data_slice",
                "review_saved_data_view",
                "review_saved_variance",
                "compare_data_slices",
            ),
        )
        if activity is None or not activity.arguments:
            return None
        selection: dict[str, object] = activity.arguments
        if activity.name == "review_saved_data_view":
            name = str(activity.arguments.get("name") or "").strip()
            if not name:
                return None
            try:
                selection = self._gateway.saved_data_view_selection_payload(name)
            except (AgentCapabilityError, EPMError):
                return None
        return {
            "tool": activity.name,
            "selection": selection,
        }

    @staticmethod
    def _instruction_with_data_review_context(
        context: dict[str, object] | None,
    ) -> str:
        """Provide the same safe refinement context to every model provider."""
        if not context:
            return SYSTEM_INSTRUCTION
        serialized = json.dumps(
            context,
            ensure_ascii=True,
            separators=(",", ":"),
        )[:6_000]
        return (
            f"{SYSTEM_INSTRUCTION}\n\n"
            "Current tool-validated Data Explorer context:\n"
            f"{serialized}\n"
            "Preserve all prior selections except fields explicitly changed "
            "by the user. Re-run the complete updated read-only slice or "
            "comparison. Ask one concise question when the change is ambiguous."
        )

    def resolve_approval(
        self,
        *,
        conversation_id: str,
        user: UserAccount,
        request_id: str,
        decision: str,
        operation_uploads: Mapping[str, Path] | None = None,
        operation_cleanup: Callable[[], None] | None = None,
    ) -> dict[str, object]:
        """Resume a durable graph interrupt after an explicit human decision."""
        self._require_agent_use(user)
        if self._repository.get_conversation(
            conversation_id,
            user.user_id,
        ) is None:
            raise AgentConversationError("Agent conversation was not found.")
        if self._graph is None:
            raise AgentConfigurationError(
                "Human approval requires LangGraph orchestration."
            )
        normalized = decision.strip().casefold()
        if normalized not in {"approve", "reject"}:
            raise AgentConversationError(
                "Approval decision must be approve or reject."
            )
        if normalized == "approve" and not self._can_prepare_agent_actions(user):
            raise AgentConversationError(
                "You do not have permission to prepare governed operations."
            )
        existing = self._repository.get_action_decision(
            request_id=request_id,
            user_id=user.user_id,
            conversation_id=conversation_id,
        )
        if existing is not None:
            return self._replay_action_decision(existing, user)

        pending = self.get_pending_approval(conversation_id, user)
        if pending is None or pending.request_id != request_id:
            raise AgentConversationError(
                "This approval is stale. Refresh the conversation."
            )
        audit_payload = self._approval_audit_payload(pending)
        checksum = self._approval_payload_checksum(audit_payload)
        decision_record, reserved = self._repository.reserve_action_decision(
            request_id=request_id,
            conversation_id=conversation_id,
            user_id=user.user_id,
            username=user.username,
            operation_code=pending.operation_code,
            artifact_name=pending.artifact_name,
            decision=normalized.upper(),
            payload_checksum=checksum,
            payload_snapshot=self._audit_safe_value(audit_payload),
        )
        if not reserved:
            return self._replay_action_decision(decision_record, user)

        try:
            result = self._graph.resume_approval(
                conversation_id=conversation_id,
                user_id=user.user_id,
                request_id=request_id,
                decision=normalized,
            )
            response = self._persist_agent_result(
                conversation_id=conversation_id,
                user=user,
                result=result,
                execute_approved=(normalized == "approve"),
                operation_uploads=operation_uploads,
                operation_cleanup=operation_cleanup,
            )
            execution = response.get("execution")
            schedule = response.get("schedule")
            execution_id = (
                str(execution.get("execution_id"))
                if isinstance(execution, dict)
                and execution.get("execution_id")
                else f"schedule:{schedule.get('schedule_id')}"
                if isinstance(schedule, dict) and schedule.get("schedule_id")
                else None
            )
            outcome = (
                "REJECTED"
                if normalized == "reject"
                else "SUBMITTED" if execution_id else "APPROVED"
            )
            finalized = self._repository.finalize_action_decision(
                request_id=request_id,
                user_id=user.user_id,
                conversation_id=conversation_id,
                outcome_status=outcome,
                execution_id=execution_id,
            )
            response["decision"] = finalized
            return response
        except Exception as exc:
            try:
                self._repository.finalize_action_decision(
                    request_id=request_id,
                    user_id=user.user_id,
                    conversation_id=conversation_id,
                    outcome_status="FAILED",
                    failure_summary=str(exc),
                )
            except Exception:
                self._logger.exception(
                    "Unable to finalize failed agent decision '%s'.",
                    request_id,
                )
            raise

    def resolve_clarification(
        self,
        *,
        conversation_id: str,
        user: UserAccount,
        request_id: str,
        value: str | None,
    ) -> dict[str, object]:
        """Resume one durable artifact choice or cancel its preparation."""
        self._require_agent_use(user)
        if not self._can_prepare_agent_actions(user):
            raise AgentConversationError(
                "You do not have permission to prepare governed operations."
            )
        if self._repository.get_conversation(
            conversation_id,
            user.user_id,
        ) is None:
            raise AgentConversationError("Agent conversation was not found.")
        if self._graph is None:
            raise AgentConfigurationError(
                "Structured clarification requires LangGraph orchestration."
            )
        result = self._graph.resume_clarification(
            conversation_id=conversation_id,
            user_id=user.user_id,
            request_id=request_id,
            value=value,
        )
        return self._persist_agent_result(
            conversation_id=conversation_id,
            user=user,
            result=result,
        )

    def resolve_input(
        self,
        *,
        conversation_id: str,
        user: UserAccount,
        request_id: str,
        values: dict[str, object] | None,
    ) -> dict[str, object]:
        """Validate and resume one guided operation-input request."""
        self._require_agent_use(user)
        if not self._can_prepare_agent_actions(user):
            raise AgentConversationError(
                "You do not have permission to prepare governed operations."
            )
        if self._repository.get_conversation(
            conversation_id,
            user.user_id,
        ) is None:
            raise AgentConversationError("Agent conversation was not found.")
        if self._graph is None:
            raise AgentConfigurationError(
                "Guided operation inputs require LangGraph orchestration."
            )
        # Preserve ``None`` as the explicit cancellation signal. Converting it
        # to an empty mapping makes LangGraph treat Cancel as a submitted form
        # and run operation-specific validation.
        supplied_values = None if values is None else dict(values)
        if supplied_values is not None and "user_name" in supplied_values:
            requested_user = str(supplied_values.get("user_name") or "").strip()
            if not user.has_permission(Permission.USER_MANAGE):
                if requested_user and requested_user.casefold() != user.username.casefold():
                    raise AgentConversationError(
                        "Your platform role can update only your own user variables."
                    )
                supplied_values["user_name"] = user.username
        result = self._graph.resume_input(
            conversation_id=conversation_id,
            user_id=user.user_id,
            request_id=request_id,
            values=supplied_values,
        )
        return self._persist_agent_result(
            conversation_id=conversation_id,
            user=user,
            result=result,
        )

    def _persist_agent_result(
        self,
        *,
        conversation_id: str,
        user: UserAccount,
        result,
        execute_approved: bool = False,
        operation_uploads: Mapping[str, Path] | None = None,
        operation_cleanup: Callable[[], None] | None = None,
    ) -> dict[str, object]:
        """Persist one completed or interrupted graph response."""
        clarification_request = self._refresh_clarification_catalog(
            result.clarification_request,
            user,
        )
        response_text = result.text
        if result.approval_request is not None:
            schedule_action = (
                result.approval_request.operation_code.casefold()
                in PIPELINE_SCHEDULE_ACTIONS
            )
            direct_run = (
                result.approval_request.operation_code.casefold()
                in self._DIRECT_APPROVAL_OPERATIONS
            )
            response_text = (
                f"I can apply **{result.approval_request.display_name}** with "
                "the reviewed schedule details below. Nothing runs until an "
                "enabled occurrence becomes due."
                if schedule_action
                else f"I can run **{result.approval_request.display_name}** with "
                "the reviewed inputs below. Approving will queue the selected "
                "operation in Oracle and start monitored execution."
                if direct_run
                else f"I can prepare a governed handoff for "
                f"**{result.approval_request.display_name}**. Review the "
                "proposal below and approve or reject it. No Oracle operation "
                "will run from this approval."
            )
        elif clarification_request is not None:
            if clarification_request.recommendations:
                response_text = (
                    "I compared your task description with the live Oracle "
                    f"{clarification_request.display_name} catalog "
                    "and found likely matches. Review "
                    "the recommendations below, then select the appropriate "
                    "artifact. No Oracle operation has started."
                )
            elif clarification_request.catalog_recovery:
                response_text = (
                    f"I could not identify a matching registered "
                    f"**{clarification_request.display_name}** artifact. "
                    "Use the recovery choices below to synchronize the "
                    "catalog or register an exact Oracle identifier. No "
                    "Oracle operation has started."
                )
            else:
                response_text = (
                    f"Choose the Oracle artifact for "
                    f"**{clarification_request.display_name}** below. "
                    "The choices were retrieved by the platform; no Oracle "
                    "operation has started."
                )
        elif result.input_request is not None:
            response_text = (
                f"I found **{result.input_request.artifact_name}**. Choose "
                "the required run options below. No Oracle operation has "
                "started."
            )
            if result.input_request.operation_code == PIPELINE_SCHEDULE_CREATE:
                response_text = (
                    f"I found Pipeline **{result.input_request.artifact_name}**. "
                    "Choose its recurrence and unattended inputs below. No "
                    "schedule has been saved and no Pipeline has started."
                )
        draft_payloads = tuple(
            activity.result["action_draft"]
            for activity in result.tool_activity
            if activity.status == "SUCCESS"
            and activity.name in {
                "prepare_operation_action",
                "prepare_schedule_action",
            }
            and isinstance(activity.result, dict)
            and isinstance(activity.result.get("action_draft"), dict)
        )
        flow_payloads = tuple(
            activity.result["standalone_flow"]
            for activity in result.tool_activity
            if activity.status == "SUCCESS"
            and activity.name == "prepare_standalone_flow_action"
            and isinstance(activity.result, dict)
            and isinstance(activity.result.get("standalone_flow"), dict)
        )
        execution = None
        schedule = None
        executed_operation = False
        if execute_approved:
            flow_payload = next(
                (
                    payload
                    for payload in flow_payloads
                    if payload.get("status") == "READY_FOR_APPROVAL"
                ),
                None,
            )
            direct_payload = next(
                (
                    payload
                    for payload in draft_payloads
                    if str(payload.get("target_code") or "").casefold()
                    in self._DIRECT_APPROVAL_OPERATIONS
                ),
                None,
            )
            if flow_payload is not None:
                execution = self._submit_approved_flow(
                    flow_payload,
                    user,
                    operation_uploads=operation_uploads,
                    operation_cleanup=operation_cleanup,
                )
                executed_operation = True
            elif direct_payload is not None and str(
                direct_payload.get("target_code") or ""
            ).casefold() in PIPELINE_SCHEDULE_ACTIONS:
                schedule = self._apply_approved_schedule_action(
                    direct_payload,
                    user,
                )
                executed_operation = True
            elif direct_payload is not None:
                execution = self._submit_approved_operation(
                    direct_payload,
                    user,
                    operation_uploads=operation_uploads,
                    operation_cleanup=operation_cleanup,
                )
                executed_operation = True
            if execution is not None:
                response_text = (
                    f"Approved **{execution['target_name']}** and queued it "
                    f"for monitored Oracle execution. Execution ID: "
                    f"`{execution['execution_id']}`."
                )
            elif schedule is not None:
                action = str(direct_payload.get("target_code") or "")
                verb = {
                    PIPELINE_SCHEDULE_CREATE: "created",
                    PIPELINE_SCHEDULE_PAUSE: "paused",
                    PIPELINE_SCHEDULE_RESUME: "resumed",
                }.get(action, "updated")
                response_text = (
                    f"Schedule **{schedule['name']}** was {verb}. "
                    + (
                        f"Its next run is `{schedule['next_run_at']}`."
                        if schedule.get("next_run_at")
                        else "It has no active future occurrence."
                    )
                )
        assistant = self._repository.add_message(
            conversation_id=conversation_id,
            user_id=user.user_id,
            role=AgentMessageRole.ASSISTANT,
            content=response_text,
        )
        self._repository.record_tool_activity(
            conversation_id=conversation_id,
            user_id=user.user_id,
            activities=result.tool_activity,
        )
        action_drafts = tuple(
            self._with_input_schema(
                self._repository.create_action_draft(
                    conversation_id=conversation_id,
                    user_id=user.user_id,
                    message_id=assistant.message_id,
                    payload=activity.result["action_draft"],
                )
            )
            for activity in result.tool_activity
            if not executed_operation
            and activity.status == "SUCCESS"
            and activity.name in {
                "prepare_operation_action",
                "prepare_schedule_action",
            }
            and isinstance(activity.result, dict)
            and isinstance(activity.result.get("action_draft"), dict)
        )
        self._logger.info(
            "Agent response completed: conversation_id='%s', user_id=%s, "
            "provider='%s', tools=%d, interrupted='%s', execution_id='%s'.",
            conversation_id,
            user.user_id,
            self._settings.agent_provider,
            len(result.tool_activity),
            (
                "approval" if result.approval_request is not None
                else "clarification" if clarification_request is not None
                else "input" if result.input_request is not None
                else "none"
            ),
            (
                execution.get("execution_id")
                if isinstance(execution, dict)
                else ""
            ),
        )
        return {
            "message": assistant,
            "tool_activity": result.tool_activity,
            "action_drafts": action_drafts,
            "approval_request": result.approval_request,
            "clarification_request": clarification_request,
            "input_request": self._personalize_input_request(result.input_request, user),
            "execution": execution,
            "schedule": schedule,
            "decision": None,
        }

    @staticmethod
    def _is_current_user_access_request(prompt: str) -> bool:
        """Recognize questions about the authenticated user's own access."""
        normalized = " ".join(str(prompt or "").casefold().split())
        permission = r"permis{1,2}ions?"
        patterns = (
            rf"\bmy\b.{{0,30}}\b(?:role|roles|{permission}|access)\b",
            rf"\b(?:role|roles|{permission}|access)\b.{{0,30}}"
            r"\b(?:do i have|assigned to me|for me)\b",
            r"\bwhat\s+am\s+i\s+allowed\s+to\s+do\b",
            r"\bam\s+i\s+allowed\b",
            r"\bwho\s+am\s+i\b",
        )
        return any(re.search(pattern, normalized) for pattern in patterns)

    @staticmethod
    def _current_user_access_result(user: UserAccount) -> AgentProviderResult:
        """Return session-backed identity facts without asking the model."""
        roles = tuple(_ROLE_LABELS.get(role, role.value) for role in user.roles)
        permissions = tuple(
            (permission, _PERMISSION_LABELS.get(permission, permission.value))
            for permission in sorted(user.permissions, key=lambda item: item.value)
        )
        role_text = ", ".join(roles) if roles else "No platform role assigned"
        permission_lines = (
            "\n".join(
                f"- **{label}** (`{permission.value}`)"
                for permission, label in permissions
            )
            if permissions
            else "- No active platform permissions are assigned."
        )
        status = "Active" if user.active else "Inactive"
        content = (
            "**Your current platform access**\n\n"
            f"- **User:** {user.display_name} (`{user.username}`)\n"
            f"- **Account status:** {status}\n"
            f"- **Platform role:** {role_text}\n\n"
            "**Granted permissions**\n\n"
            f"{permission_lines}\n\n"
            "These values come from your authenticated platform session and "
            "are the permissions enforced by this application. They describe "
            "your account, not the EPM Assistant. Platform roles are shown "
            "here; this response does not claim to list raw Oracle groups or "
            "Oracle-native role assignments."
        )
        activity = AgentToolActivity(
            name="get_current_user_access",
            arguments={},
            status="SUCCESS",
            summary="Authenticated user access returned.",
            result={
                "username": user.username,
                "display_name": user.display_name,
                "active": user.active,
                "roles": [role.value for role in user.roles],
                "permissions": [permission.value for permission, _ in permissions],
            },
        )
        return AgentProviderResult(text=content, tool_activity=(activity,))

    def _submit_approved_flow(
        self,
        payload: dict[str, object],
        user: UserAccount,
        *,
        operation_uploads: Mapping[str, Path] | None = None,
        operation_cleanup: Callable[[], None] | None = None,
    ) -> dict[str, str]:
        """Queue one fully reviewed standalone flow as a durable execution."""
        if self._operation_manager is None:
            raise AgentConversationError(
                "Agent operation execution is not configured."
            )
        raw_steps = payload.get("configured_steps")
        if not isinstance(raw_steps, list) or not 2 <= len(raw_steps) <= 12:
            raise AgentConversationError(
                "The standalone flow does not contain a valid reviewed sequence."
            )
        uploads = operation_uploads or {}
        resolved_steps: list[StandaloneFlowStepInput] = []
        for index, raw_step in enumerate(raw_steps, start=1):
            if not isinstance(raw_step, dict):
                raise AgentConversationError(
                    f"Standalone flow step {index} is invalid."
                )
            operation_code = str(
                raw_step.get("operation_code") or ""
            ).strip().casefold()
            artifact_name = str(
                raw_step.get("artifact_name") or ""
            ).strip()
            values = raw_step.get("input_values")
            input_values = values if isinstance(values, dict) else {}
            step_prefix = f"step_{index}:"
            step_uploads = {
                key[len(step_prefix) :]: path
                for key, path in uploads.items()
                if key.startswith(step_prefix)
            }
            operation_input = self._standalone_flow_operation_input(
                operation_code,
                artifact_name,
                input_values,
                user,
                step_uploads,
            )
            resolved_steps.append(
                StandaloneFlowStepInput(
                    operation_code=operation_code,
                    display_name=str(
                        raw_step.get("display_name") or operation_code
                    ),
                    artifact_name=artifact_name,
                    operation_input=operation_input,
                )
            )
        objective = str(payload.get("objective") or "").strip()
        flow_name = " → ".join(
            step.display_name for step in resolved_steps
        )[:180]
        try:
            execution = self._operation_manager.submit_flow(
                StandaloneFlowInput(
                    name=flow_name,
                    objective=objective,
                    steps=tuple(resolved_steps),
                ),
                cleanup=operation_cleanup,
                actor=ExecutionActor(
                    username=user.username,
                    display_name=user.display_name,
                    trigger_source=TriggerSource.AI_AGENT,
                ),
            )
        except EPMError as exc:
            raise AgentConversationError(str(exc)) from exc
        return {
            "execution_id": execution.execution_id,
            "operation_code": "standalone-flow",
            "target_name": "Standalone Planning Flow",
            "status": execution.status.value,
        }

    def _standalone_flow_operation_input(
        self,
        operation_code: str,
        requested_artifact: str,
        input_values: dict[str, object],
        user: UserAccount,
        operation_uploads: Mapping[str, Path],
    ):
        """Revalidate one reviewed flow step without submitting it separately."""
        if operation_code not in {
            "substitution-variables",
            "user-variables",
        } and not user.has_permission(Permission.OPERATION_EXECUTE):
            raise AgentConversationError(
                "You do not have permission to execute Oracle operations."
            )
        if operation_code == "substitution-variables" and not user.has_permission(
            Permission.VARIABLE_UPDATE
        ):
            raise AgentConversationError(
                "You do not have permission to update substitution variables."
            )
        if operation_code == "user-variables" and not user.has_permission(
            Permission.USER_VARIABLE_UPDATE
        ):
            raise AgentConversationError(
                "You do not have permission to update user variables."
            )
        canonical = self._gateway.resolve_artifact_choice(
            operation_code,
            requested_artifact,
        )
        if canonical is None:
            raise AgentConversationError(
                f"'{requested_artifact}' is no longer available for "
                f"{operation_code.replace('-', ' ')}. Review the flow again."
            )
        if operation_code == "business-rules":
            prompts = input_values.get("runtime_prompts", {})
            if not isinstance(prompts, dict):
                raise AgentConversationError(
                    "Business Rule runtime prompts are invalid."
                )
            normalized_prompts = (
                self._gateway.normalize_business_rule_runtime_prompts(
                    canonical,
                    prompts,
                )
            )
            return BusinessRuleOperationInput(
                rule_name=canonical,
                runtime_prompts=normalized_prompts,
            )
        if operation_code == "pipelines":
            return self._pipeline_operation_input(
                canonical,
                input_values,
                operation_uploads,
            )
        if operation_code == "data-maps":
            members = input_values.get("member_overrides", {})
            exclusions = input_values.get("exclusion_overrides", {})
            clear_target = input_values.get("clear_target")
            if (
                not isinstance(members, dict)
                or not isinstance(exclusions, dict)
                or not isinstance(clear_target, bool)
            ):
                raise AgentConversationError(
                    "The reviewed Data Map controls are invalid."
                )
            return DataMapOperationInput(
                data_map_name=canonical,
                clear_target=clear_target,
                member_overrides={
                    str(name): str(value) for name, value in members.items()
                },
                exclusion_overrides={
                    str(name): str(value)
                    for name, value in exclusions.items()
                },
            )
        if operation_code == "data-integrations":
            return self._data_integration_operation_input(
                canonical, input_values, operation_uploads
            )
        if operation_code == "data-import":
            return self._data_import_operation_input(
                canonical, input_values, operation_uploads
            )
        if operation_code == "metadata-import":
            return self._metadata_import_operation_input(
                canonical, input_values, operation_uploads
            )
        if operation_code == "cube-refresh":
            return CubeRefreshOperationInput(job_name=canonical)
        if operation_code == "substitution-variables":
            try:
                action = SubstitutionVariableAction(
                    str(input_values.get("action") or "")
                )
            except ValueError as exc:
                raise AgentConversationError(
                    "The reviewed substitution-variable action is invalid."
                ) from exc
            return SubstitutionVariableOperationInput(
                action=action,
                scope=str(input_values.get("scope") or ""),
                name=str(input_values.get("variable_name") or ""),
                value=str(input_values.get("new_value") or ""),
                expected_current_value=(
                    str(input_values.get("expected_current_value"))
                    if action is SubstitutionVariableAction.UPDATE
                    else None
                ),
            )
        if operation_code == "user-variables":
            target_user = str(input_values.get("user_name") or "").strip()
            if (
                target_user.casefold() != user.username.casefold()
                and not user.has_permission(Permission.USER_MANAGE)
            ):
                raise AgentConversationError(
                    "Your platform role can update only your own user variables."
                )
            return UserVariableOperationInput(
                user_name=target_user,
                name=str(input_values.get("variable_name") or ""),
                dimension=str(input_values.get("dimension") or ""),
                member=str(input_values.get("new_member") or ""),
                expected_current_member=(
                    None
                    if input_values.get("expected_current_member") is None
                    else str(input_values.get("expected_current_member"))
                ),
            )
        raise AgentConversationError(
            f"Operation '{operation_code}' is not supported in a standalone flow."
        )

    def get_action_decisions(
        self,
        conversation_id: str,
        user: UserAccount,
    ) -> tuple[AgentActionDecision, ...]:
        """Return append-only approval evidence owned by this user."""
        self._require_agent_use(user)
        return self._repository.list_action_decisions(
            conversation_id,
            user.user_id,
        )

    def _stop_latest_active_flow(
        self,
        *,
        conversation_id: str,
        user: UserAccount,
    ) -> dict[str, object] | None:
        """Apply a conversational safe-stop to this user's latest active flow."""
        if self._operation_manager is None:
            return None
        decisions = self._repository.list_action_decisions(
            conversation_id,
            user.user_id,
        )
        for decision in reversed(decisions):
            if (
                decision.operation_code != "standalone-flow"
                or not decision.execution_id
                or decision.outcome_status != "SUBMITTED"
            ):
                continue
            managed = self._operation_manager.get(decision.execution_id)
            if managed is None or managed.status.value not in {"QUEUED", "RUNNING"}:
                continue
            stopped = self._operation_manager.request_flow_stop(
                decision.execution_id,
                requested_by=user.username,
            )
            cancelled = stopped.status.value == "CANCELLED"
            assistant = self._repository.add_message(
                conversation_id=conversation_id,
                user_id=user.user_id,
                role=AgentMessageRole.ASSISTANT,
                content=(
                    "The standalone flow was still queued, so I cancelled it "
                    "before any Oracle step started."
                    if cancelled
                    else "I requested a safe stop. The current Oracle job will "
                    "finish normally, and no later flow step will start."
                ),
            )
            return {
                "message": assistant,
                "tool_activity": (),
                "action_drafts": self._repository.list_action_drafts(
                    conversation_id,
                    user.user_id,
                ),
                "approval_request": None,
                "clarification_request": None,
                "input_request": None,
                "execution": {
                    "execution_id": stopped.execution_id,
                    "operation_code": "standalone-flow",
                    "target_name": stopped.target_name,
                    "status": stopped.status.value,
                },
                "schedule": None,
                "decision": None,
            }
        return None

    def _replay_action_decision(
        self,
        decision: AgentActionDecision,
        user: UserAccount,
    ) -> dict[str, object]:
        """Return the first decision outcome without submitting work again."""
        execution = None
        schedule = None
        if decision.execution_id and decision.execution_id.startswith("schedule:"):
            try:
                schedule_id = int(decision.execution_id.split(":", 1)[1])
                item = (
                    self._schedule_service.get(schedule_id)
                    if self._schedule_service is not None
                    else None
                )
                if item is not None:
                    schedule = self._schedule_payload(item)
            except (EPMError, ValueError):
                schedule = None
        elif decision.execution_id:
            get_execution = (
                getattr(self._operation_manager, "get", None)
                if self._operation_manager is not None
                else None
            )
            managed = (
                get_execution(decision.execution_id)
                if callable(get_execution)
                else None
            )
            execution = {
                "execution_id": decision.execution_id,
                "operation_code": decision.operation_code,
                "target_name": (
                    managed.target_name
                    if managed is not None
                    else decision.artifact_name or decision.operation_code
                ),
                "status": (
                    managed.status.value
                    if managed is not None
                    else decision.outcome_status
                ),
            }
        if decision.outcome_status == "PROCESSING":
            text = (
                "This approval is already being processed. The platform did "
                "not submit a second Oracle operation."
            )
        elif decision.outcome_status == "SUBMITTED":
            text = (
                "This reviewed schedule change was already applied. The "
                "platform returned the saved schedule instead of applying it again."
                if decision.operation_code in PIPELINE_SCHEDULE_ACTIONS
                else "This reviewed approval was already submitted. The platform "
                "returned the original execution instead of starting it again."
            )
        elif decision.outcome_status == "REJECTED":
            text = (
                "This proposal was already rejected. No Oracle operation was "
                "started."
            )
        elif decision.outcome_status == "FAILED":
            text = (
                "The previous attempt to process this approval failed and "
                "cannot be replayed from the same decision. Start a new "
                "assistant request after reviewing the error."
            )
        else:
            text = (
                "This governed proposal was already approved. The platform "
                "did not process the approval twice."
            )
        assistant = self._repository.add_message(
            conversation_id=decision.conversation_id,
            user_id=user.user_id,
            role=AgentMessageRole.ASSISTANT,
            content=text,
        )
        return {
            "message": assistant,
            "tool_activity": (),
            "action_drafts": self._repository.list_action_drafts(
                decision.conversation_id,
                user.user_id,
            ),
            "approval_request": None,
            "clarification_request": None,
            "input_request": None,
            "execution": execution,
            "schedule": schedule,
            "decision": decision,
        }

    @staticmethod
    def _approval_audit_payload(
        approval: AgentApprovalRequest,
    ) -> dict[str, object]:
        return {
            "operation_code": approval.operation_code,
            "display_name": approval.display_name,
            "objective": approval.objective,
            "artifact_name": approval.artifact_name,
            "category": approval.category,
            "risk_level": approval.risk_level,
            "route": approval.route,
            "input_values": approval.input_values,
        }

    @staticmethod
    def _approval_payload_checksum(payload: dict[str, object]) -> str:
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def _audit_safe_value(
        cls,
        value,
        *,
        key: str = "",
    ):
        """Remove credentials and ephemeral upload tokens from audit JSON."""
        sensitive = {
            "password",
            "secret",
            "token",
            "api_key",
            "apikey",
            "authorization",
            "credential",
        }
        normalized_key = key.casefold()
        if normalized_key == "uploads" and isinstance(value, Mapping):
            return {str(item_key): "[redacted]" for item_key in value}
        if any(marker in normalized_key for marker in sensitive):
            return "[redacted]"
        if isinstance(value, Mapping):
            return {
                str(item_key): cls._audit_safe_value(
                    item_value,
                    key=str(item_key),
                )
                for item_key, item_value in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [cls._audit_safe_value(item) for item in value]
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        return str(value)

    def _apply_approved_schedule_action(
        self,
        payload: dict[str, object],
        user: UserAccount,
    ) -> dict[str, object]:
        """Apply one explicitly approved change through the shared scheduler."""
        if not user.has_permission(Permission.SCHEDULE_MANAGE):
            raise AgentConversationError(
                "You do not have permission to manage automation schedules."
            )
        if self._schedule_service is None or self._schedule_coordinator is None:
            raise AgentConversationError(
                "Agent scheduling is not configured for this environment."
            )
        action = str(payload.get("target_code") or "").strip().casefold()
        values = payload.get("input_values")
        input_values = values if isinstance(values, dict) else {}
        try:
            if action == PIPELINE_SCHEDULE_CREATE:
                requested = str(payload.get("artifact_name") or "").strip()
                canonical = self._gateway.resolve_artifact_choice(
                    PIPELINE_SCHEDULE_CREATE,
                    requested,
                )
                if canonical is None:
                    raise AgentConversationError(
                        "The selected Oracle Pipeline is no longer available."
                    )
                first_run = datetime.fromisoformat(
                    str(input_values.get("first_run_local") or "")
                )
                if first_run.tzinfo is not None:
                    raise AgentConversationError(
                        "The reviewed first run must be a local date and time."
                    )
                policy = AutomationInputPolicy(
                    str(input_values.get("input_policy") or "")
                )
                variables = self._string_values(input_values.get("variables"))
                inbox_files = self._string_values(input_values.get("inbox_files"))
                schedule_input = AutomationScheduleInput(
                    environment_key=self._schedule_environment_key,
                    name=str(input_values.get("name") or "").strip(),
                    target_type=AutomationTargetType.ORACLE_PIPELINE,
                    target_key=canonical,
                    frequency=AutomationScheduleFrequency(
                        str(input_values.get("frequency") or "")
                    ),
                    timezone=str(input_values.get("timezone") or "").strip(),
                    first_run_local=first_run,
                    input_policy=policy,
                    configuration=(
                        {"variables": variables, "inbox_files": inbox_files}
                        if policy is AutomationInputPolicy.FIXED
                        else {}
                    ),
                    concurrency_policy=AutomationConcurrencyPolicy.SKIP_IF_ACTIVE,
                    misfire_policy=AutomationMisfirePolicy(
                        str(input_values.get("misfire_policy") or "RUN_ONCE")
                    ),
                    enabled=bool(input_values.get("enabled", True)),
                )
                schedule = self._schedule_coordinator.create(schedule_input)
            else:
                identifier = str(payload.get("artifact_name") or "").strip()
                canonical = self._gateway.resolve_artifact_choice(action, identifier)
                if canonical is None or not canonical.casefold().startswith("schedule:"):
                    raise AgentConversationError(
                        "The selected automation schedule is no longer available."
                    )
                schedule_id = int(canonical.split(":", 1)[1])
                schedule = self._schedule_service.get(schedule_id)
                if schedule.environment_key != self._schedule_environment_key:
                    raise AgentConversationError(
                        "The selected automation schedule belongs to another environment."
                    )
                expected_enabled = action == PIPELINE_SCHEDULE_PAUSE
                if schedule.enabled is not expected_enabled:
                    state = "active" if schedule.enabled else "paused"
                    raise AgentConversationError(
                        f"Schedule '{schedule.name}' is already {state}."
                    )
                schedule = self._schedule_coordinator.set_enabled(
                    schedule_id,
                    action == PIPELINE_SCHEDULE_RESUME,
                )
        except AgentConversationError:
            raise
        except (EPMError, ValueError) as exc:
            raise AgentConversationError(str(exc)) from exc
        return self._schedule_payload(schedule)

    @staticmethod
    def _string_values(value: object) -> dict[str, str]:
        if not isinstance(value, Mapping):
            return {}
        return {
            str(name).strip(): str(item).strip()
            for name, item in value.items()
            if str(name).strip() and str(item).strip()
        }

    @staticmethod
    def _schedule_payload(schedule) -> dict[str, object]:
        configuration = dict(schedule.configuration)
        return {
            "schedule_id": schedule.schedule_id,
            "name": schedule.name,
            "target_type": schedule.target_type.value,
            "target_key": schedule.target_key,
            "frequency": schedule.frequency.value,
            "timezone": schedule.timezone,
            "first_run_local": schedule.first_run_local.isoformat(),
            "input_policy": schedule.input_policy.value,
            "variables": dict(configuration.get("variables") or {}),
            "inbox_files": dict(configuration.get("inbox_files") or {}),
            "misfire_policy": schedule.misfire_policy.value,
            "concurrency_policy": schedule.concurrency_policy.value,
            "enabled": schedule.enabled,
            "next_run_at": (
                schedule.next_run_at.isoformat()
                if schedule.next_run_at is not None
                else None
            ),
            "last_outcome": schedule.last_outcome.value,
            "created_at": schedule.created_at.isoformat(),
            "updated_at": schedule.updated_at.isoformat(),
            "last_triggered_at": (
                schedule.last_triggered_at.isoformat()
                if schedule.last_triggered_at is not None
                else None
            ),
            "last_execution_id": schedule.last_execution_id,
            "last_error": schedule.last_error,
        }

    def _submit_approved_operation(
        self,
        payload: dict[str, object],
        user: UserAccount,
        *,
        operation_uploads: Mapping[str, Path] | None = None,
        operation_cleanup: Callable[[], None] | None = None,
    ) -> dict[str, str]:
        """Queue one exact, explicitly approved operation through the manager."""
        if self._operation_manager is None:
            raise AgentConversationError(
                "Agent operation execution is not configured."
            )
        operation_code = str(payload.get("target_code") or "").strip().casefold()
        if operation_code not in self._DIRECT_APPROVAL_OPERATIONS:
            raise AgentConversationError(
                "This operation requires the governed operation screen."
            )
        if (
            operation_code == "substitution-variables"
            and not user.has_permission(Permission.VARIABLE_UPDATE)
        ):
            raise AgentConversationError(
                "You do not have permission to update substitution variables."
            )
        if (
            operation_code == "user-variables"
            and not user.has_permission(Permission.USER_VARIABLE_UPDATE)
        ):
            raise AgentConversationError(
                "You do not have permission to update user variables."
            )
        requested = str(payload.get("artifact_name") or "").strip()
        if not requested:
            raise AgentConversationError(
                "Select a live Oracle artifact before approving execution."
            )
        try:
            canonical = self._gateway.resolve_artifact_choice(
                operation_code,
                requested,
            )
            if canonical is None:
                raise AgentConversationError(
                    f"'{requested}' is no longer available for "
                    f"{operation_code.replace('-', ' ')} in "
                    "the connected Planning application."
                )
            values = payload.get("input_values")
            input_values = values if isinstance(values, dict) else {}
            if operation_code == "business-rules":
                runtime_prompts = input_values.get("runtime_prompts", {})
                if not isinstance(runtime_prompts, dict):
                    raise AgentConversationError(
                        "Business Rule runtime prompts are invalid."
                    )
                normalized_prompts = (
                    self._gateway.normalize_business_rule_runtime_prompts(
                        canonical,
                        runtime_prompts,
                    )
                )
                operation_input = BusinessRuleOperationInput(
                    rule_name=canonical,
                    runtime_prompts=normalized_prompts,
                )
            elif operation_code == "data-maps":
                member_overrides = input_values.get("member_overrides", {})
                exclusion_overrides = input_values.get(
                    "exclusion_overrides", {}
                )
                if not isinstance(member_overrides, dict) or not isinstance(
                    exclusion_overrides, dict
                ):
                    raise AgentConversationError(
                        "Data Map member overrides are invalid."
                    )
                if not isinstance(input_values.get("clear_target"), bool):
                    raise AgentConversationError(
                        "Choose whether the Data Map should clear its target."
                    )
                operation_input = DataMapOperationInput(
                    data_map_name=canonical,
                    clear_target=input_values["clear_target"],
                    member_overrides={
                        str(name): str(value)
                        for name, value in member_overrides.items()
                    },
                    exclusion_overrides={
                        str(name): str(value)
                        for name, value in exclusion_overrides.items()
                    },
                )
            elif operation_code == "data-integrations":
                operation_input = self._data_integration_operation_input(
                    canonical,
                    input_values,
                    operation_uploads or {},
                )
            elif operation_code == "data-import":
                operation_input = self._data_import_operation_input(
                    canonical,
                    input_values,
                    operation_uploads or {},
                )
            elif operation_code == "metadata-import":
                operation_input = self._metadata_import_operation_input(
                    canonical,
                    input_values,
                    operation_uploads or {},
                )
            elif operation_code == "cube-refresh":
                operation_input = CubeRefreshOperationInput(
                    job_name=canonical,
                )
            elif operation_code == "substitution-variables":
                try:
                    variable_action = SubstitutionVariableAction(
                        str(input_values.get("action") or "")
                    )
                except ValueError as exc:
                    raise AgentConversationError(
                        "Choose whether to update or create the substitution variable."
                    ) from exc
                operation_input = SubstitutionVariableOperationInput(
                    action=variable_action,
                    scope=str(input_values.get("scope") or ""),
                    name=str(input_values.get("variable_name") or ""),
                    value=str(input_values.get("new_value") or ""),
                    expected_current_value=(
                        str(input_values.get("expected_current_value"))
                        if variable_action is SubstitutionVariableAction.UPDATE
                        else None
                    ),
                )
            elif operation_code == "user-variables":
                target_user = str(input_values.get("user_name") or "").strip()
                if (
                    target_user.casefold() != user.username.casefold()
                    and not user.has_permission(Permission.USER_MANAGE)
                ):
                    raise AgentConversationError(
                        "Your platform role can update only your own user variables."
                    )
                operation_input = UserVariableOperationInput(
                    user_name=target_user,
                    name=str(input_values.get("variable_name") or ""),
                    dimension=str(input_values.get("dimension") or ""),
                    member=str(input_values.get("new_member") or ""),
                    expected_current_member=(
                        None
                        if input_values.get("expected_current_member") is None
                        else str(input_values.get("expected_current_member"))
                    ),
                )
            else:
                operation_input = self._pipeline_operation_input(
                    canonical,
                    input_values,
                    operation_uploads or {},
                )
            submit_options = {
                "actor": ExecutionActor(
                    username=user.username,
                    display_name=user.display_name,
                    trigger_source=TriggerSource.AI_AGENT,
                )
            }
            if operation_code in {
                "pipelines",
                "data-integrations",
                "data-import",
                "metadata-import",
            } and operation_cleanup is not None:
                submit_options["cleanup"] = operation_cleanup
            execution = self._operation_manager.submit(
                operation_input,
                **submit_options,
            )
        except AgentConversationError:
            raise
        except EPMError as exc:
            raise AgentConversationError(str(exc)) from exc
        return {
            "execution_id": execution.execution_id,
            "operation_code": operation_code,
            "target_name": execution.target_name,
            "status": execution.status.value,
        }

    @staticmethod
    def _data_integration_operation_input(
        integration_name: str,
        input_values: dict[str, object],
        operation_uploads: Mapping[str, Path],
    ) -> DataIntegrationOperationInput:
        """Revalidate an approved Data Integration run before queueing it."""
        try:
            periods = DataIntegrationPeriodRange.from_period_names(
                str(input_values.get("start_period") or ""),
                str(input_values.get("end_period") or ""),
            )
            import_mode = DataIntegrationService.normalize_import_mode(
                str(input_values.get("import_mode") or "")
            )
            export_mode = DataIntegrationService.normalize_export_mode(
                str(input_values.get("export_mode") or "")
            )
        except EPMError as exc:
            raise AgentConversationError(str(exc)) from exc
        source = str(input_values.get("file_source") or "").strip()
        upload_path = operation_uploads.get("source_file")
        inbox_file = str(input_values.get("inbox_file") or "").strip() or None
        use_configured = source == "Use file configured in Oracle"
        if source == "Upload on governed screen":
            if upload_path is None or not Path(upload_path).is_file():
                raise AgentConversationError(
                    "The reviewed Data Integration upload is unavailable or expired."
                )
            inbox_file = None
        elif source == "Existing Oracle Inbox file":
            if not inbox_file:
                raise AgentConversationError(
                    "The reviewed Oracle Inbox file is missing."
                )
            try:
                inbox_file = str(
                    DataIntegrationFileReference.from_existing(inbox_file)
                )
            except EPMError as exc:
                raise AgentConversationError(str(exc)) from exc
            upload_path = None
        elif use_configured:
            upload_path = None
            inbox_file = None
        else:
            raise AgentConversationError(
                "The reviewed Data Integration file source is invalid."
            )
        return DataIntegrationOperationInput(
            integration_name=integration_name,
            start_period=periods.start_period,
            end_period=periods.end_period,
            import_mode=import_mode,
            export_mode=export_mode,
            upload_path=Path(upload_path) if upload_path is not None else None,
            inbox_file=inbox_file,
            use_configured_file=use_configured,
        )

    @staticmethod
    def _data_import_operation_input(
        job_name: str,
        input_values: dict[str, object],
        operation_uploads: Mapping[str, Path],
    ) -> DataImportOperationInput:
        """Revalidate an approved native Planning Data Import before queueing."""
        source = str(input_values.get("file_source") or "").strip()
        upload_path = operation_uploads.get("source_file")
        inbox_file = str(input_values.get("inbox_file") or "").strip() or None
        use_configured = source == "Use file configured in Oracle"
        if source == "Upload on governed screen":
            if upload_path is None or not Path(upload_path).is_file():
                raise AgentConversationError(
                    "The reviewed Planning Data Import upload is unavailable "
                    "or expired."
                )
            try:
                DataService.validate_inputs(Path(upload_path).name, job_name)
            except EPMError as exc:
                raise AgentConversationError(str(exc)) from exc
            inbox_file = None
        elif source == "Existing Oracle Inbox file":
            if not inbox_file:
                raise AgentConversationError(
                    "The reviewed Oracle Inbox data file is missing."
                )
            inbox_file = PurePath(inbox_file.replace("\\", "/")).name.strip()
            try:
                DataService.validate_inputs(inbox_file, job_name)
            except EPMError as exc:
                raise AgentConversationError(str(exc)) from exc
            upload_path = None
        elif use_configured:
            upload_path = None
            inbox_file = None
        else:
            raise AgentConversationError(
                "The reviewed Planning Data Import file source is invalid."
            )
        error_file = str(input_values.get("error_file_name") or "").strip()
        if error_file and (
            PurePath(error_file).name != error_file
            or "/" in error_file
            or "\\" in error_file
        ):
            raise AgentConversationError(
                "The reviewed error output must be a filename, not a path."
            )
        return DataImportOperationInput(
            job_name=job_name,
            upload_path=Path(upload_path) if upload_path is not None else None,
            inbox_file=inbox_file,
            use_configured_file=use_configured,
            error_file_name=error_file or None,
        )

    def _metadata_import_operation_input(
        self,
        job_name: str,
        input_values: dict[str, object],
        operation_uploads: Mapping[str, Path],
    ) -> MetadataImportOperationInput:
        """Revalidate an approved Metadata Import and optional refresh."""
        source = str(input_values.get("file_source") or "").strip()
        upload_path = operation_uploads.get("source_file")
        inbox_file = str(input_values.get("inbox_file") or "").strip() or None
        use_configured = source == "Use file configured in Oracle"
        if source == "Upload on governed screen":
            if upload_path is None or not Path(upload_path).is_file():
                raise AgentConversationError(
                    "The reviewed Metadata Import upload is unavailable or "
                    "expired."
                )
            try:
                MetadataService.validate_inputs(Path(upload_path).name, job_name)
            except EPMError as exc:
                raise AgentConversationError(str(exc)) from exc
            inbox_file = None
        elif source == "Existing Oracle Inbox file":
            if not inbox_file:
                raise AgentConversationError(
                    "The reviewed Oracle Inbox metadata file is missing."
                )
            inbox_file = PurePath(inbox_file.replace("\\", "/")).name.strip()
            try:
                MetadataService.validate_inputs(inbox_file, job_name)
            except EPMError as exc:
                raise AgentConversationError(str(exc)) from exc
            upload_path = None
        elif use_configured:
            upload_path = None
            inbox_file = None
        else:
            raise AgentConversationError(
                "The reviewed Metadata Import file source is invalid."
            )
        error_file = str(input_values.get("error_file_name") or "").strip()
        if error_file and (
            PurePath(error_file).name != error_file
            or "/" in error_file
            or "\\" in error_file
        ):
            raise AgentConversationError(
                "The reviewed metadata error output must be a filename, not a path."
            )
        refresh_after_import = input_values.get("refresh_after_import")
        if not isinstance(refresh_after_import, bool):
            raise AgentConversationError(
                "Choose whether to refresh the cube after Metadata Import."
            )
        refresh_job_name: str | None = None
        if refresh_after_import:
            requested_refresh = str(
                input_values.get("refresh_job_name") or ""
            ).strip()
            refresh_job_name = self._gateway.resolve_artifact_choice(
                "cube-refresh",
                requested_refresh,
            )
            if refresh_job_name is None:
                raise AgentConversationError(
                    "The reviewed Cube Refresh job is no longer available."
                )
        return MetadataImportOperationInput(
            job_name=job_name,
            upload_path=Path(upload_path) if upload_path is not None else None,
            inbox_file=inbox_file,
            use_configured_file=use_configured,
            error_file_name=error_file or None,
            refresh_job_name=refresh_job_name,
        )

    def _pipeline_operation_input(
        self,
        pipeline_code: str,
        input_values: dict[str, object],
        operation_uploads: Mapping[str, Path],
    ) -> PipelineOperationInput:
        """Revalidate approved Pipeline inputs against the live definition."""
        try:
            preview = self._gateway.guided_input_definition(
                "pipelines",
                pipeline_code,
            )
        except AgentCapabilityError as exc:
            raise AgentConversationError(str(exc)) from exc
        context = preview.get("context", {}) if preview else {}
        known_variables = {
            str(item.get("name") or "").casefold(): str(
                item.get("name") or ""
            )
            for item in context.get("variables", ())
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }
        requirements = {
            str(item.get("key") or "").casefold(): item
            for item in context.get("file_requirements", ())
            if isinstance(item, dict) and str(item.get("key") or "").strip()
        }
        raw_variables = input_values.get("runtime_variables", {})
        raw_upload_tokens = input_values.get("uploads", {})
        raw_inbox = input_values.get("inbox_files", {})
        raw_configured = input_values.get("configured_files", {})
        if not all(
            isinstance(item, dict)
            for item in (
                raw_variables,
                raw_upload_tokens,
                raw_inbox,
                raw_configured,
            )
        ):
            raise AgentConversationError(
                "Approved Pipeline variables or file choices are invalid."
            )
        unknown_variables = {
            str(name)
            for name in raw_variables
            if str(name).casefold() not in known_variables
        }
        unknown_files = {
            str(key)
            for key in set(raw_upload_tokens) | set(raw_inbox) | set(raw_configured)
            if str(key).casefold() not in requirements
        }
        if unknown_variables or unknown_files:
            raise AgentConversationError(
                "The Oracle Pipeline definition changed after review. "
                "Start a new assistant request to inspect its current inputs."
            )
        supplied_variable_keys = {
            str(name).casefold() for name in raw_variables
        }
        for variable in context.get("variables", ()):
            if (
                isinstance(variable, dict)
                and bool(variable.get("required"))
                and str(variable.get("name") or "").casefold()
                not in supplied_variable_keys
            ):
                raise AgentConversationError(
                    "The Oracle Pipeline definition changed after review. "
                    "Start a new assistant request to review its current "
                    "required variables."
                )
        normalized_uploads = {
            str(key).casefold(): Path(path)
            for key, path in operation_uploads.items()
        }
        token_keys = {str(key).casefold() for key in raw_upload_tokens}
        if token_keys != set(normalized_uploads):
            raise AgentConversationError(
                "A reviewed Pipeline upload is unavailable or expired."
            )
        uploads = {
            str(requirements[key]["key"]): path
            for key, path in normalized_uploads.items()
        }
        inbox_files = {
            str(requirements[str(key).casefold()]["key"]): str(value).strip()
            for key, value in raw_inbox.items()
        }
        duplicates = set(uploads) & set(inbox_files)
        if duplicates:
            raise AgentConversationError(
                "Choose only one source for each Pipeline file input."
            )
        configured_files = {
            str(requirements[str(key).casefold()]["key"]): str(value).strip()
            for key, value in raw_configured.items()
            if str(key).casefold() in requirements
        }
        selected_file_keys = (
            set(uploads) | set(inbox_files) | set(configured_files)
        )
        for key, requirement in requirements.items():
            canonical_key = str(requirement["key"])
            if (
                bool(requirement.get("required"))
                and canonical_key not in selected_file_keys
            ):
                raise AgentConversationError(
                    f"{requirement.get('display_name') or canonical_key} "
                    "is now required by Oracle. Start a new assistant request."
                )
            if canonical_key in configured_files and configured_files[
                canonical_key
            ] != str(requirement.get("configured_reference") or "").strip():
                raise AgentConversationError(
                    f"The configured file for "
                    f"{requirement.get('display_name') or canonical_key} "
                    "changed after review. Start a new assistant request."
                )
        return PipelineOperationInput(
            pipeline_code=pipeline_code,
            variables={
                known_variables[str(name).casefold()]: str(value).strip()
                for name, value in raw_variables.items()
            },
            uploads=uploads,
            inbox_files=inbox_files,
        )

    @staticmethod
    def _allowed_tool_names(user: UserAccount) -> frozenset[str]:
        allowed = {
            "get_environment_summary",
            "list_platform_operations",
        }
        if user.has_permission(Permission.HISTORY_VIEW):
            allowed.add("get_recent_execution_history")
            allowed.add("get_execution_evidence")
        if user.has_permission(Permission.DATA_REVIEW):
            allowed.update(
                {
                    "list_planning_cubes",
                    "list_cube_dimensions",
                    "search_dimension_members",
                    "list_data_explorer_views",
                    "list_variance_views",
                    "review_saved_data_view",
                    "review_saved_variance",
                    "review_data_slice",
                    "compare_data_slices",
                }
            )
        if AgentApplicationService._can_prepare_operations(user):
            allowed.add("list_operation_artifacts")
            allowed.add("plan_multi_step_request")
            allowed.add("prepare_operation_action")
            allowed.add("prepare_standalone_flow_action")
        if user.has_permission(Permission.SCHEDULE_MANAGE):
            allowed.add("prepare_schedule_action")
        return frozenset(allowed)

    @staticmethod
    def _can_prepare_operations(user: UserAccount) -> bool:
        return any(
            user.has_permission(permission)
            for permission in (
                Permission.OPERATION_EXECUTE,
                Permission.VARIABLE_UPDATE,
                Permission.USER_VARIABLE_UPDATE,
            )
        )

    @staticmethod
    def _can_prepare_agent_actions(user: UserAccount) -> bool:
        return (
            AgentApplicationService._can_prepare_operations(user)
            or user.has_permission(Permission.SCHEDULE_MANAGE)
        )

    @staticmethod
    def _personalize_input_request(request, user: UserAccount):
        if request is None or request.operation_code.casefold() != "user-variables":
            return request
        return replace(
            request,
            context={
                **request.context,
                "default_user": user.username,
                "can_manage_users": user.has_permission(Permission.USER_MANAGE),
            },
        )

    def _authorized_execution_ids(
        self, conversation_id: str, user_id: int
    ) -> tuple[str, ...]:
        return tuple(
            decision.execution_id
            for decision in reversed(
                self._repository.list_action_decisions(conversation_id, user_id)
            )
            if decision.outcome_status == "SUBMITTED"
            and decision.execution_id
            and not decision.execution_id.startswith("schedule:")
        )

    def _execute_user_tool(
        self,
        call,
        allowed: frozenset[str],
        *,
        conversation_id: str,
        user_id: int,
    ):
        if call.name not in allowed:
            raise AgentConversationError(
                f"Agent capability '{call.name}' is not permitted for this user."
            )
        if call.name in {"get_execution_evidence", "get_recent_execution_history"}:
            if self._graph is None:
                # The legacy provider has no checkpointed graph identity, but
                # it must still honor the same conversation evidence boundary.
                return self._conversation_execution_tool(
                    call, conversation_id, user_id
                )
        return self._gateway.execute(call)

    def _conversation_execution_tool(self, call, conversation_id: str, user_id: int):
        identifiers = self._authorized_execution_ids(conversation_id, user_id)
        requested = str(call.arguments.get("execution_id") or "").strip()
        if requested and requested not in identifiers:
            raise AgentConversationError(
                "This execution is not associated with this conversation."
            )
        selector = str(call.arguments.get("selector") or "").casefold()
        matches = []
        for identifier in ((requested,) if requested else identifiers[:20]):
            result = self._gateway.execute(
                AgentToolCall(
                    name="get_execution_evidence",
                    arguments={"execution_id": identifier},
                )
            )
            execution = result.get("execution")
            if isinstance(execution, dict):
                matches.append(execution)
        if call.name == "get_recent_execution_history":
            return {"count": len(matches), "runs": matches}
        for execution in matches:
            if selector != "latest_failed" or str(
                execution.get("status") or ""
            ).upper() == "FAILED":
                return {"execution": execution}
        raise AgentConversationError(
            "No matching execution was found in this conversation."
        )

    @staticmethod
    def _require_agent_use(user: UserAccount) -> None:
        if not user.has_permission(Permission.AGENT_USE):
            raise AgentConversationError(
                "You do not have permission to use EPM Assistant."
            )

    def shutdown(self) -> None:
        """Release graph-owned resources during application shutdown."""
        if self._graph is not None:
            self._graph.shutdown()

    def _default_provider(self) -> AgentProvider:
        if self._settings.agent_provider == "gemini":
            return GeminiAgentProvider(
                api_key=self._settings.gemini_api_key or "",
                model=self._settings.agent_model,
                max_tool_rounds=self._settings.agent_max_tool_rounds,
                logger=self._logger.getChild("gemini"),
            )
        if self._settings.agent_provider == "groq":
            return GroqAgentProvider(
                api_key=self._settings.groq_api_key or "",
                model=self._settings.agent_model,
                max_tool_rounds=self._settings.agent_max_tool_rounds,
                max_input_tokens=self._settings.groq_max_input_tokens,
                max_completion_tokens=(
                    self._settings.groq_max_completion_tokens
                ),
                timeout=self._settings.request_timeout,
                logger=self._logger.getChild("groq"),
            )
        raise AgentConfigurationError(
            f"Agent provider '{self._settings.agent_provider}' is not available."
        )

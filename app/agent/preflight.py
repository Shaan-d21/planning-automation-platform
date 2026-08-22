"""Deterministic, non-executing validation for agent action drafts."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import quote

from app.agent.models import AgentActionDraft, AgentDraftCheck
from app.application.action_inputs import (
    action_input_schema,
    missing_required_inputs,
)
from app.application.control_center import ControlCenterService
from app.application.operations import OPERATION_DEFINITIONS, OperationCatalogService
from app.application.reports import ReportWorkspaceService
from app.models.access_control import Permission, UserAccount
from app.utils.exceptions import EPMError


class AgentActionPreflightService:
    """Validate a prepared handoff without approving or executing it."""

    _LIVE_JOB_TYPES = {
        "business-rules": "RULES",
        "data-maps": "PLAN_TYPE_MAP",
        "metadata-import": "IMPORT_METADATA",
        "data-import": "IMPORT_DATA",
        "cube-refresh": "CUBE_REFRESH",
    }

    def __init__(
        self,
        *,
        control_center: ControlCenterService,
        operation_catalog: OperationCatalogService,
        report_workspace: ReportWorkspaceService,
    ) -> None:
        self._control_center = control_center
        self._operations = operation_catalog
        self._reports = report_workspace

    def preflight(
        self,
        draft: AgentActionDraft,
        user: UserAccount,
    ) -> tuple[str, tuple[AgentDraftCheck, ...]]:
        """Return a persisted-ready status and evidence checks."""
        checks: list[AgentDraftCheck] = []
        permission = self._required_permission(draft)
        if user.has_permission(permission):
            checks.append(
                self._check(
                    "permission",
                    "Platform access",
                    "PASS",
                    f"Your role permits {self._permission_label(permission)}.",
                )
            )
        else:
            checks.append(
                self._check(
                    "permission",
                    "Platform access",
                    "BLOCKED",
                    "Your platform role does not permit this action.",
                )
            )
            return "BLOCKED", tuple(checks)

        checks.extend(self._input_checks(draft))

        if draft.action_type == "process":
            checks.extend(self._process_checks(draft))
        elif draft.action_type == "operation":
            checks.extend(self._operation_checks(draft))
        else:
            checks.append(
                self._check(
                    "action_type",
                    "Action type",
                    "BLOCKED",
                    f"Unsupported action type '{draft.action_type}'.",
                )
            )
        return self._overall_status(checks), tuple(checks)

    def _input_checks(
        self, draft: AgentActionDraft
    ) -> tuple[AgentDraftCheck, ...]:
        """Validate required values using the shared action contract."""
        try:
            schema = action_input_schema(draft.action_type, draft.target_code)
        except EPMError as exc:
            return (
                self._check(
                    "inputs",
                    "Action inputs",
                    "BLOCKED",
                    str(exc),
                ),
            )
        if not any(field.required for field in schema.fields):
            return ()
        missing = missing_required_inputs(schema, draft.input_values)
        if missing:
            return (
                self._check(
                    "inputs",
                    "Required inputs",
                    "ACTION_REQUIRED",
                    "Complete: " + ", ".join(item.label for item in missing) + ".",
                ),
            )
        if not schema.fields:
            return ()
        return (
            self._check(
                "inputs",
                "Business context",
                "PASS",
                (
                    "Required preparation is complete. Exact Planning members, "
                    "Pipeline prompts, and files are verified during the next "
                    "live Oracle preflight."
                ),
            ),
        )

    def _process_checks(
        self, draft: AgentActionDraft
    ) -> tuple[AgentDraftCheck, ...]:
        expected_route = (
            "/app/control-panel?process_code="
            + quote(draft.target_code, safe="")
        )
        checks = [
            self._route_check(draft, exact=expected_route)
        ]
        try:
            processes = self._control_center.snapshot(
                history_limit=1
            ).processes
        except EPMError as exc:
            checks.append(
                self._unavailable(
                    "process",
                    "Configured process",
                    str(exc),
                )
            )
            return tuple(checks)
        process = next(
            (
                item
                for item in processes
                if item.code.casefold() == draft.target_code.casefold()
            ),
            None,
        )
        if process is None:
            checks.append(
                self._check(
                    "process",
                    "Configured process",
                    "ACTION_REQUIRED",
                    (
                        f"Process '{draft.target_code}' is not currently "
                        "active in the Planning workspace."
                    ),
                )
            )
        else:
            checks.append(
                self._check(
                    "process",
                    "Configured process",
                    "PASS",
                    (
                        f"'{process.name}' is active with "
                        f"{len(process.steps)} visible stage"
                        f"{'s' if len(process.steps) != 1 else ''}."
                    ),
                )
            )
        return tuple(checks)

    def _operation_checks(
        self, draft: AgentActionDraft
    ) -> tuple[AgentDraftCheck, ...]:
        definition = next(
            (
                item
                for item in OPERATION_DEFINITIONS
                if item.code.casefold() == draft.target_code.casefold()
            ),
            None,
        )
        if definition is None:
            return (
                self._check(
                    "operation",
                    "Operation registration",
                    "BLOCKED",
                    f"Operation '{draft.target_code}' is no longer registered.",
                ),
            )
        checks = [self._route_check(draft, exact=definition.route)]
        artifact = str(draft.artifact_name or "").strip()
        if not artifact:
            checks.append(
                self._check(
                    "artifact",
                    "Oracle artifact",
                    "ACTION_REQUIRED",
                    "Select the exact artifact on the governed operation screen.",
                )
            )
            return tuple(checks)

        if draft.target_code in self._LIVE_JOB_TYPES:
            checks.append(self._live_job_check(draft.target_code, artifact))
        elif draft.target_code == "pipelines":
            try:
                registered = self._operations.discover_registered().pipelines
            except EPMError as exc:
                checks.append(
                    self._unavailable(
                        "artifact", "Registered Pipeline", str(exc)
                    )
                )
            else:
                checks.append(
                    self._artifact_check(
                        artifact,
                        (
                            value
                            for item in registered
                            for value in (item.code, item.name)
                        ),
                        label="Registered Pipeline",
                    )
                )
        elif draft.target_code == "data-integrations":
            try:
                registered = (
                    self._operations.discover_registered().data_integrations
                )
            except EPMError as exc:
                checks.append(
                    self._unavailable(
                        "artifact", "Registered Data Integration", str(exc)
                    )
                )
            else:
                checks.append(
                    self._artifact_check(
                        artifact,
                        (item.name for item in registered),
                        label="Registered Data Integration",
                    )
                )
        elif draft.target_code == "report-generation":
            try:
                report = self._reports.preflight(artifact)
            except EPMError as exc:
                checks.append(
                    self._unavailable(
                        "artifact",
                        "Registered report",
                        str(exc),
                    )
                )
            else:
                checks.append(
                    self._check(
                        "artifact",
                        "Planning report or form",
                        "PASS",
                        (
                            f"'{report.form_name}' is available for cube "
                            f"'{report.cube}'."
                        ),
                    )
                )
        elif draft.target_code == "substitution-variables":
            checks.append(
                self._check(
                    "artifact",
                    "Substitution variable",
                    "PASS",
                    (
                        f"Variable '{artifact}' is ready for scope and value "
                        "review on the governed screen."
                    ),
                )
            )
        return tuple(checks)

    def _live_job_check(
        self, operation_code: str, artifact: str
    ) -> AgentDraftCheck:
        try:
            names = self._operations.discover_job_names(
                job_type=self._LIVE_JOB_TYPES[operation_code]
            )
        except EPMError as exc:
            return self._unavailable(
                "artifact",
                "Live Oracle artifact",
                str(exc),
            )
        return self._artifact_check(
            artifact,
            names,
            label="Live Oracle artifact",
        )

    @staticmethod
    def _artifact_check(
        artifact: str,
        available: Iterable[str],
        *,
        label: str,
    ) -> AgentDraftCheck:
        names = tuple(str(item).strip() for item in available if str(item).strip())
        match = next(
            (item for item in names if item.casefold() == artifact.casefold()),
            None,
        )
        if match is not None:
            return AgentActionPreflightService._check(
                "artifact",
                label,
                "PASS",
                f"'{match}' is currently available.",
            )
        return AgentActionPreflightService._check(
            "artifact",
            label,
            "ACTION_REQUIRED",
            (
                f"'{artifact}' was not found. Choose a currently available "
                "artifact on the governed screen."
            ),
        )

    @staticmethod
    def _route_check(
        draft: AgentActionDraft,
        *,
        exact: str | None = None,
        expected_prefix: str | None = None,
    ) -> AgentDraftCheck:
        valid = bool(
            (exact is not None and draft.route == exact)
            or (
                expected_prefix is not None
                and draft.route.startswith(expected_prefix)
            )
        )
        return AgentActionPreflightService._check(
            "route",
            "Governed destination",
            "PASS" if valid else "BLOCKED",
            (
                "The handoff uses a registered internal platform screen."
                if valid
                else "The draft destination is no longer valid. Prepare a new draft."
            ),
        )

    @staticmethod
    def _required_permission(draft: AgentActionDraft) -> Permission:
        if draft.action_type == "process":
            return Permission.PROCESS_RUN
        if draft.target_code == "report-generation":
            return Permission.REPORT_GENERATE
        if draft.target_code == "substitution-variables":
            return Permission.VARIABLE_UPDATE
        if draft.target_code == "user-variables":
            return Permission.USER_VARIABLE_UPDATE
        return Permission.OPERATION_EXECUTE

    @staticmethod
    def _permission_label(permission: Permission) -> str:
        return permission.value.replace(".", " ").replace("_", " ")

    @staticmethod
    def _overall_status(checks: Iterable[AgentDraftCheck]) -> str:
        statuses = {item.status for item in checks}
        if "BLOCKED" in statuses:
            return "BLOCKED"
        if "UNAVAILABLE" in statuses:
            return "VALIDATION_UNAVAILABLE"
        if "ACTION_REQUIRED" in statuses:
            return "NEEDS_INPUT"
        return "READY_FOR_GOVERNED_REVIEW"

    @staticmethod
    def _unavailable(code: str, label: str, message: str) -> AgentDraftCheck:
        return AgentActionPreflightService._check(
            code,
            label,
            "UNAVAILABLE",
            f"Validation could not be completed: {message}",
        )

    @staticmethod
    def _check(
        code: str,
        label: str,
        status: str,
        message: str,
    ) -> AgentDraftCheck:
        return AgentDraftCheck(
            code=code,
            label=label,
            status=status,
            message=message,
        )

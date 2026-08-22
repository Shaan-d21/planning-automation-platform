"""Custom exception hierarchy for Oracle EPM automation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.job import JobDiagnostics, JobResult


class EPMError(Exception):
    """Base exception for all framework-specific failures."""


class ConfigurationError(EPMError):
    """Raised when required configuration is missing or invalid."""


class DatabaseConnectionError(EPMError):
    """Raised when the platform database cannot be reached or authenticated."""


class APIRequestError(EPMError):
    """Raised when an Oracle EPM API request or response is invalid."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthenticationError(APIRequestError):
    """Raised when Oracle EPM rejects the configured credentials."""


class AccessControlError(EPMError):
    """Raised when platform identity or authorization data is invalid."""


class IdentitySynchronizationError(AccessControlError):
    """Raised when an external identity snapshot cannot be persisted safely."""


class IdentitySnapshotChangedError(IdentitySynchronizationError):
    """Raised when Oracle access changed after an administrator's preview."""


class FederatedAuthenticationError(AccessControlError):
    """Raised when a validated external identity cannot enter the platform."""


class ApiTokenError(AccessControlError):
    """Raised when a scoped external-client token is invalid."""


class AgentError(EPMError):
    """Base exception for the platform AI agent subsystem."""


class AgentConfigurationError(AgentError):
    """Raised when the selected agent provider is not configured."""


class AgentProviderError(AgentError):
    """Raised when a model provider cannot complete an agent turn."""


class AgentConversationError(AgentError):
    """Raised when a user-owned agent conversation is invalid."""


class AgentCapabilityError(AgentError):
    """Raised when an agent requests a disallowed or invalid capability."""


class EPMConnectionError(EPMError):
    """Raised when the Oracle EPM environment cannot be reached."""


class FileUploadError(EPMError):
    """Raised when an Inbox upload cannot be completed."""


class FileCatalogError(EPMError):
    """Raised when Oracle repository files cannot be discovered safely."""


class MetadataImportError(EPMError):
    """Raised when a metadata import job cannot be submitted."""


class DataImportError(EPMError):
    """Raised when a native Planning data import cannot be completed."""


class DataIntegrationError(EPMError):
    """Raised when a Data Integration job cannot be validated or executed."""


class BusinessRuleError(EPMError):
    """Raised when a Business Rule cannot be validated or executed."""


class PipelineError(EPMError):
    """Raised when a Data Integration Pipeline cannot be executed."""


class DataMapError(EPMError):
    """Raised when a Planning Data Map cannot be executed."""


class DataValidationError(EPMError):
    """Raised when source-to-target validation cannot be completed."""


class WorkflowError(EPMError):
    """Raised when a configured automation workflow cannot be completed."""


class ExecutionQueueError(EPMError):
    """Raised when durable execution work cannot be queued or claimed."""


class ExecutionQueueConflictError(ExecutionQueueError):
    """Raised when the same target already has active durable work."""


class SubstitutionVariableError(EPMError):
    """Raised when substitution variables cannot be discovered or updated."""


class UserVariableError(EPMError):
    """Raised when Planning user-variable values cannot be managed safely."""


class PlanningCycleError(EPMError):
    """Raised when Planning cycle pre-flight or execution is invalid."""


class PlanningWorkflowError(EPMError):
    """Raised when an operational Planning cycle or task is invalid."""


class PlanningProcessError(EPMError):
    """Raised when an end-to-end Planning process is invalid or fails."""


class ScheduleError(EPMError):
    """Raised when a Planning Process schedule is invalid or cannot run."""


class OperationError(EPMError):
    """Raised when a standalone automation operation cannot be completed."""


class CubeRefreshError(EPMError):
    """Raised when a saved Planning Cube Refresh job cannot be executed."""


class ReportGenerationError(EPMError):
    """Raised when a Planning report cannot be exported or rendered."""


class NotificationError(EPMError):
    """Raised when a configured notification provider cannot send a message."""


class EPMAutomateError(EPMError):
    """Base exception for EPM Automate failures."""


class EPMAutomateNotInstalledError(EPMAutomateError):
    """Raised when the EPM Automate executable cannot be found."""


class EPMAutomateAuthenticationError(EPMAutomateError):
    """Raised when EPM Automate rejects the configured credentials."""


class EPMAutomateCommandError(EPMAutomateError):
    """Raised when an EPM Automate command returns a failure."""

    def __init__(
        self,
        command: str,
        return_code: int,
        details: str,
    ) -> None:
        self.command = command
        self.return_code = return_code
        self.details = details
        super().__init__(
            f"EPM Automate command '{command}' failed with exit code "
            f"{return_code}: {details}"
        )


class EPMAutomateTimeoutError(EPMAutomateError):
    """Raised when an EPM Automate command exceeds its timeout."""

    def __init__(self, command: str, timeout_seconds: float) -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"EPM Automate command '{command}' did not complete within "
            f"{timeout_seconds:g} seconds."
        )


class JobTimeoutError(EPMError):
    """Raised when an Oracle EPM job exceeds its monitoring timeout."""

    def __init__(self, job_id: int, timeout_seconds: float) -> None:
        self.job_id = job_id
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Oracle EPM job {job_id} did not complete within "
            f"{timeout_seconds:g} seconds."
        )


class JobFailedError(EPMError):
    """Raised when an Oracle EPM job reaches a failed terminal state."""

    def __init__(
        self,
        job: JobResult,
        *,
        diagnostics: JobDiagnostics | None = None,
    ) -> None:
        self.job = job
        self.diagnostics = diagnostics
        detail = job.details or job.descriptive_status
        super().__init__(
            f"Oracle EPM job {job.job_id} failed with status "
            f"{job.status} ({detail})."
        )

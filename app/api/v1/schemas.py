"""Stable response contracts used to bootstrap the product frontend."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.models.access_control import RoleCode


class SessionLoginRequest(BaseModel):
    """Credentials used for an existing local platform account."""

    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=512)


class OracleSessionLoginRequest(BaseModel):
    """Transient credentials validated directly by Oracle Cloud EPM."""

    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=512)


class InitialAdministratorRequest(BaseModel):
    """One-time administrator setup used by the React application."""

    username: str = Field(min_length=3, max_length=80)
    display_name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=254)
    password: str = Field(min_length=12, max_length=256)
    password_confirmation: str = Field(min_length=12, max_length=256)

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password != self.password_confirmation:
            raise ValueError("Password confirmation does not match.")
        return self


class ProductSummary(BaseModel):
    """Non-secret product identity rendered by every client shell."""

    name: str
    company: str
    api_version: str


class EnvironmentSummary(BaseModel):
    """Safe Oracle environment context for an authenticated user."""

    application_name: str
    deployment_mode: str
    base_url: str
    configured: bool
    execution_account: str


class EnvironmentApplicationSummary(BaseModel):
    """One application returned by supported Oracle discovery."""

    name: str
    product_type: str | None = None
    application_type: str | None = None
    admin_mode: bool | None = None


class EnvironmentConfigurationResponse(BaseModel):
    """Non-secret persisted connection configuration for administrators."""

    status: str = "success"
    base_url: str
    deployment_mode: str
    active_application: str | None
    selected_application: str | None
    selection_source: str | None
    configured: bool
    restart_required: bool
    applications: list[EnvironmentApplicationSummary] = Field(
        default_factory=list
    )
    last_discovered_at: datetime | None = None
    last_discovery_error: str | None = None
    message: str | None = None


class EnvironmentApplicationSelectionRequest(BaseModel):
    """Administrator selection from the live Oracle application list."""

    application_name: str = Field(min_length=1, max_length=128)


class IdentityAuthenticationSummary(BaseModel):
    """Safe unauthenticated sign-in options exposed to the browser."""

    federated_enabled: bool
    oracle_credentials_enabled: bool = False
    provider_name: str
    login_url: str | None = None
    local_recovery_enabled: bool = True


class CurrentUserSummary(BaseModel):
    """Password-free platform identity and effective authorization."""

    user_id: int
    username: str
    display_name: str
    email: str | None
    platform_roles: list[str]
    permissions: list[str]
    persona: str
    persona_label: str


class NavigationItem(BaseModel):
    """One server-authorized navigation destination."""

    code: str
    label: str
    path: str
    group: str


class FeatureAvailability(BaseModel):
    """Explicit rollout status for the incremental frontend migration."""

    legacy_ui: bool = False
    task_engine: bool = True
    planning_cycles: bool = True
    approvals: bool = True
    notifications: bool = True
    access_control: bool = True
    jobs_activity: bool = True


class OperationSummary(BaseModel):
    """One standalone Oracle EPM service available to the current user."""

    code: str
    display_name: str
    description: str
    category: str
    risk_level: str
    route: str


class OperationsResponse(BaseModel):
    """Permission-filtered catalog for the enterprise Operations workspace."""

    status: str
    operations: list[OperationSummary] = Field(default_factory=list)


class FrontendBootstrapResponse(BaseModel):
    """Initial state required before a browser renders authenticated UI."""

    product: ProductSummary
    authenticated: bool
    requires_bootstrap: bool
    csrf_token: str
    identity_authentication: IdentityAuthenticationSummary
    environment: EnvironmentSummary | None = None
    user: CurrentUserSummary | None = None
    navigation: list[NavigationItem] = Field(default_factory=list)
    features: FeatureAvailability = Field(default_factory=FeatureAvailability)


class SessionResponse(BaseModel):
    """Result returned after starting or ending a platform session."""

    status: str
    message: str
    csrf_token: str | None = None
    user: CurrentUserSummary | None = None


class CycleStageCreateRequest(BaseModel):
    """One business stage supplied with a new operational cycle."""

    code: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    sequence: int = Field(gt=0)
    start_date: date | None = None
    due_date: date | None = None


class PlanningTaskCreateRequest(BaseModel):
    """One actionable task and its client-key dependencies."""

    key: str = Field(min_length=1, max_length=80)
    stage_code: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    task_type: str = Field(min_length=1, max_length=60)
    priority: str = "NORMAL"
    assigned_username: str | None = Field(default=None, max_length=80)
    assigned_role_code: str | None = Field(default=None, max_length=64)
    entity: str | None = Field(default=None, max_length=160)
    scenario: str | None = Field(default=None, max_length=120)
    period: str | None = Field(default=None, max_length=80)
    due_at: datetime | None = None
    action_type: str = Field(min_length=1, max_length=60)
    action_config: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)


class PlanningCycleCreateRequest(BaseModel):
    """Atomic creation request for a cycle, its stages, and tasks."""

    code: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    cycle_type: str = Field(min_length=1, max_length=40)
    process_code: str | None = Field(default=None, max_length=128)
    scenario: str | None = Field(default=None, max_length=120)
    year: str = Field(min_length=1, max_length=40)
    actual_through_period: str | None = Field(default=None, max_length=80)
    forecast_start_period: str | None = Field(default=None, max_length=80)
    start_date: date
    due_date: date
    stages: list[CycleStageCreateRequest] = Field(min_length=1)
    tasks: list[PlanningTaskCreateRequest] = Field(min_length=1)


class PlanningTaskStatusRequest(BaseModel):
    """One governed task-status transition."""

    status: str = Field(min_length=1, max_length=30)


class PlanningApprovalDecisionRequest(BaseModel):
    """One governed manager decision and optional business explanation."""

    decision: str = Field(min_length=1, max_length=30)
    comment: str | None = Field(default=None, max_length=4000)


class PlatformUserCreateRequest(BaseModel):
    """Create one platform identity with one primary business role."""

    username: str = Field(min_length=3, max_length=80)
    display_name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=254)
    password: str = Field(min_length=12, max_length=256)
    role_code: RoleCode


class PlatformUserEditRequest(BaseModel):
    """Update a platform identity without exposing credential state."""

    display_name: str = Field(min_length=1, max_length=120)
    email: str | None = Field(default=None, max_length=254)
    active: bool
    role_code: RoleCode


class PlatformPasswordChangeRequest(BaseModel):
    """Administrator-issued password replacement."""

    password: str = Field(min_length=12, max_length=256)
    password_confirmation: str = Field(min_length=12, max_length=256)


class IdentitySynchronizationRequest(BaseModel):
    """Apply the exact Oracle directory state previously reviewed."""

    snapshot_checksum: str = Field(min_length=64, max_length=64)


class IdentityRoleMappingRequest(BaseModel):
    """Map one synchronized Oracle entitlement to a platform role."""

    role_code: RoleCode


class IdentityProvisioningRequest(BaseModel):
    """Apply the exact linked-profile plan previously reviewed."""

    provisioning_checksum: str = Field(min_length=64, max_length=64)

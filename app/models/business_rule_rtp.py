"""Typed Business Rule runtime-prompt registry models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class RuntimePromptDefinition:
    """One Calculation Manager runtime prompt known to the platform."""

    name: str
    label: str
    prompt_order: int
    value_type: str
    dimension: str | None = None
    default_value: str | None = None
    has_default: bool = False
    required_at_launch: bool = True
    hidden: bool = False
    allow_multiple: bool = False
    security_mode: str | None = None
    scope_type: str = "UNKNOWN"
    scope_name: str | None = None
    source_variable_id: str | None = None
    limit_type: str | None = None
    limit_value: str | None = None
    source_metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BusinessRuleRTPDefinition:
    """Current RTP contract for one rule in one Oracle environment."""

    definition_id: int
    environment_key: str
    application_name: str
    rule_name: str
    cube_name: str | None
    source_name: str
    source_checksum: str
    parser_version: str
    definition_checksum: str
    synchronized_at: datetime
    prompts: tuple[RuntimePromptDefinition, ...]


@dataclass(frozen=True, slots=True)
class RTPRegistryImportResult:
    """Auditable result of importing a Calc Manager XML/ZIP package."""

    sync_run_id: int
    source_name: str
    source_checksum: str
    parser_version: str
    rules_imported: int
    prompts_imported: int
    warnings: tuple[str, ...]
    completed_at: datetime
    rules_added: int | None = None
    rules_changed: int | None = None
    rules_unchanged: int | None = None


@dataclass(frozen=True, slots=True)
class RTPRegistryDefinitionSummary:
    """Concise health record for one active rule definition."""

    rule_name: str
    cube_name: str | None
    source_name: str
    synchronized_at: datetime
    prompt_count: int
    required_prompt_count: int
    live_status: str


@dataclass(frozen=True, slots=True)
class RTPRegistrySyncRun:
    """One successful or failed synchronization attempt."""

    sync_run_id: int
    source_name: str
    parser_version: str
    status: str
    rules_imported: int
    prompts_imported: int
    warnings: tuple[str, ...]
    error_summary: str | None
    started_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class RTPRegistryStatus:
    """Environment-scoped registry coverage and synchronization health."""

    status: str
    application_name: str
    live_catalog_available: bool
    live_rule_count: int | None
    synchronized_rule_count: int
    synchronized_prompt_count: int
    unsynchronized_live_rules: tuple[str, ...]
    definitions_not_in_live_catalog: tuple[str, ...]
    definitions: tuple[RTPRegistryDefinitionSummary, ...]
    recent_syncs: tuple[RTPRegistrySyncRun, ...]

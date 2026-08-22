"""Typed models for Oracle Planning Business Rule execution."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BusinessRuleSubmission:
    """Result returned after submitting a Business Rule through REST."""

    job_id: int
    rule_name: str
    runtime_prompts: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class BusinessRuleCommandResult:
    """Successful Business Rule result returned by EPM Automate."""

    rule_name: str
    runtime_prompts: tuple[tuple[str, str], ...]
    command_output: str

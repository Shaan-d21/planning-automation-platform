"""Structured parameter corrections and contradiction detection.

This module handles a compact vocabulary of Planning slot types. It does not
validate Oracle members or construct execution payloads; the existing
operation normalizers and live catalogs remain authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable

from app.agent.task_state import (
    AgentTaskConfidence,
    AgentTaskIntent,
    AgentTaskInterpreter,
    AgentTaskPhase,
    AgentTaskUnderstanding,
)


_ACTIVE_PHASES = frozenset({"COLLECTING_INFORMATION", "READY_FOR_PLAN"})
_CORRECTION_SIGNAL = re.compile(
    r"\b(?:actually|instead|rather|change|changed|switch|set|update|"
    r"replace|make\s+it|use)\b",
    re.IGNORECASE,
)
_MONTHS = AgentTaskInterpreter._MONTHS
_MONTH_PATTERN = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\b",
    re.IGNORECASE,
)
_YEAR_PATTERN = re.compile(r"\bfy\s*([0-9]{2,4})\b", re.IGNORECASE)
_SCENARIO_PATTERN = re.compile(
    r"\b(actuals?|forecast|budget|plan)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ParameterConflict:
    name: str
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParameterDeltaResolution:
    task: AgentTaskUnderstanding
    updates: dict[str, Any]
    conflicts: tuple[ParameterConflict, ...] = ()

    def apply(self, context: dict[str, Any]) -> None:
        context["dialogue_act"] = (
            "parameter_conflict" if self.conflicts else "parameter_correction"
        )
        context["parameter_conflicts"] = [
            {"name": item.name, "values": list(item.values)}
            for item in self.conflicts
        ]


class AgentParameterDeltaResolver:
    """Apply explicit slot changes without restarting the active task."""

    @classmethod
    def resolve(
        cls,
        prompt: str,
        prior_context: dict[str, Any] | None,
        interpreted: AgentTaskUnderstanding,
    ) -> ParameterDeltaResolution | None:
        text = " ".join(str(prompt or "").split())
        if not text:
            return None
        prior = prior_context if isinstance(prior_context, dict) else {}
        prior_phase = str(prior.get("phase") or "").upper()
        active = prior_phase in _ACTIVE_PHASES
        try:
            prior_intent = AgentTaskIntent(
                str(prior.get("intent") or "UNKNOWN").upper()
            )
        except ValueError:
            prior_intent = AgentTaskIntent.UNKNOWN
        intent = (
            prior_intent
            if active and prior_intent is not AgentTaskIntent.UNKNOWN
            else interpreted.intent
        )
        if intent in {
            AgentTaskIntent.UNKNOWN,
            AgentTaskIntent.HELP_EXPLAIN,
            AgentTaskIntent.CANCEL_OPERATION,
            AgentTaskIntent.JOB_STATUS,
        }:
            return None

        correction = active and bool(_CORRECTION_SIGNAL.search(text))
        conflicts, directed = cls._slot_analysis(text, intent, correction)
        if conflicts:
            updates = AgentTaskInterpreter.extract_parameters_for_intent(
                text,
                intent,
            )
            updates.update(directed)
            if "period" in directed:
                updates.pop("start_period", None)
                updates.pop("end_period", None)
            for item in conflicts:
                # Explicitly clear the contradictory slot so an older value
                # cannot survive the context merge and reach planning.
                updates[item.name] = None
                if item.name == "period":
                    updates.pop("period_reference", None)
                    updates.pop("start_period", None)
                    updates.pop("end_period", None)
            task = AgentTaskUnderstanding(
                intent=intent,
                phase=AgentTaskPhase.COLLECTING_INFORMATION,
                confidence=AgentTaskConfidence.NEEDS_CLARIFICATION,
                parameters=updates,
                missing_parameters=(conflicts[0].name,),
                clarification_prompt=cls._conflict_prompt(conflicts[0]),
                objective=str(prior.get("objective") or interpreted.objective),
            )
            return ParameterDeltaResolution(
                task=task,
                updates=updates,
                conflicts=conflicts,
            )
        if not correction:
            return None

        updates = AgentTaskInterpreter.extract_parameters_for_intent(text, intent)
        updates.update(directed)
        if "period" in directed:
            updates.pop("start_period", None)
            updates.pop("end_period", None)
        if intent in {
            AgentTaskIntent.FORECAST_SEEDING,
            AgentTaskIntent.VARIANCE_REPORTING,
        } and (
            "comparison" in updates
            or len(cls._values_for("scenario", text)) > 1
        ):
            updates.pop("scenario", None)
        allowed_update_names = {
            "period",
            "period_reference",
            "start_period",
            "end_period",
            "year",
            "scenario",
            "comparison",
            "threshold",
            "file",
            "file_preference",
            "dimension",
            "parent_member",
            "load_method",
            "cutoff_period",
            "saved_view",
            "pov_overrides",
        }
        updates = {
            name: value
            for name, value in updates.items()
            if name in allowed_update_names
        }
        if not updates:
            return None
        prior_parameters = prior.get("parameters")
        combined = (
            dict(prior_parameters)
            if isinstance(prior_parameters, dict)
            else dict(interpreted.parameters)
        )
        combined.update(updates)
        missing, clarification = AgentTaskInterpreter.requirements_for(
            intent,
            combined,
        )
        task = AgentTaskUnderstanding(
            intent=intent,
            phase=(
                AgentTaskPhase.COLLECTING_INFORMATION
                if missing
                else AgentTaskPhase.READY_FOR_PLAN
            ),
            confidence=(
                AgentTaskConfidence.NEEDS_CLARIFICATION
                if missing
                else AgentTaskConfidence.HIGH_CONFIDENCE
            ),
            # Emit only changed values. AgentContextResolver owns the merge
            # and records them as user corrections against active task state.
            parameters=updates,
            missing_parameters=missing,
            clarification_prompt=clarification,
            objective=str(prior.get("objective") or interpreted.objective),
        )
        return ParameterDeltaResolution(task=task, updates=updates)

    @classmethod
    def _slot_analysis(
        cls,
        text: str,
        intent: AgentTaskIntent,
        correction: bool,
    ) -> tuple[tuple[ParameterConflict, ...], dict[str, str]]:
        candidates = {
            "year": cls._ordered_unique(
                f"FY{match.group(1)}" for match in _YEAR_PATTERN.finditer(text)
            ),
            "scenario": cls._ordered_unique(
                cls._scenario(match.group(1))
                for match in _SCENARIO_PATTERN.finditer(text)
            ),
            "period": cls._ordered_unique(
                _MONTHS[match.group(1).casefold()]
                for match in _MONTH_PATTERN.finditer(text)
            ),
        }
        extracted = AgentTaskInterpreter.extract_parameters_for_intent(text, intent)
        directed: dict[str, str] = {}
        conflicts: list[ParameterConflict] = []
        for name, values in candidates.items():
            if len(values) < 2:
                continue
            if name == "scenario" and intent in {
                AgentTaskIntent.FORECAST_SEEDING,
                AgentTaskIntent.VARIANCE_REPORTING,
            }:
                continue
            target = cls._directed_target(text, name, values) if correction else None
            if target is not None:
                directed[name] = target
                continue
            if (
                name == "period"
                and intent is AgentTaskIntent.DATA_LOAD
                and "start_period" in extracted
            ):
                continue
            conflicts.append(ParameterConflict(name=name, values=values))
        return tuple(conflicts), directed

    @classmethod
    def _directed_target(
        cls,
        text: str,
        name: str,
        values: tuple[str, ...],
    ) -> str | None:
        normalized = text.casefold()
        if " instead of " in normalized:
            before = text[: normalized.index(" instead of ")]
            matches = cls._values_for(name, before)
            return matches[-1] if matches else None
        if re.search(
            r"\b(?:from\b.+\bto|change\b.+\bto|switch\b.+\bto)\b",
            normalized,
        ):
            after = re.split(r"\bto\b", text, flags=re.IGNORECASE)[-1]
            matches = cls._values_for(name, after)
            return matches[-1] if matches else None
        if re.search(r"\bnot\b", normalized):
            return values[-1]
        return None

    @classmethod
    def _values_for(cls, name: str, text: str) -> tuple[str, ...]:
        if name == "year":
            return cls._ordered_unique(
                f"FY{item.group(1)}" for item in _YEAR_PATTERN.finditer(text)
            )
        if name == "scenario":
            return cls._ordered_unique(
                cls._scenario(item.group(1))
                for item in _SCENARIO_PATTERN.finditer(text)
            )
        return cls._ordered_unique(
            _MONTHS[item.group(1).casefold()]
            for item in _MONTH_PATTERN.finditer(text)
        )

    @staticmethod
    def _scenario(value: str) -> str:
        normalized = value.casefold()
        return "Actual" if normalized == "actuals" else normalized.title()

    @staticmethod
    def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(values))

    @staticmethod
    def _conflict_prompt(conflict: ParameterConflict) -> str:
        label = {
            "year": "planning year",
            "scenario": "scenario",
            "period": "period",
        }.get(conflict.name, conflict.name.replace("_", " "))
        choices = " or ".join(f"**{item}**" for item in conflict.values)
        return f"You provided more than one {label}: {choices}. Which should I use?"

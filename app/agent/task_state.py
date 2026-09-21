"""Structured business-task understanding for the conversational agent.

This module deliberately does not call Oracle or execute tools.  It turns the
conversation into checkpoint-safe business context that LangGraph can use to
ask one focused question at a time before the existing governed preparation
and approval workflows take over.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any, Sequence

from app.agent.models import AgentMessage, AgentMessageRole


class AgentTaskIntent(StrEnum):
    """Business intents understood independently of provider wording."""

    MONTH_CLOSE = "MONTH_CLOSE"
    METADATA_LOAD = "METADATA_LOAD"
    DATA_LOAD = "DATA_LOAD"
    FORECAST_SEEDING = "FORECAST_SEEDING"
    VARIANCE_REPORTING = "VARIANCE_REPORTING"
    RUN_BUSINESS_RULE = "RUN_BUSINESS_RULE"
    RUN_DATA_INTEGRATION = "RUN_DATA_INTEGRATION"
    RUN_PIPELINE = "RUN_PIPELINE"
    JOB_STATUS = "JOB_STATUS"
    CANCEL_OPERATION = "CANCEL_OPERATION"
    HELP_EXPLAIN = "HELP_EXPLAIN"
    UNKNOWN = "UNKNOWN"


class AgentTaskPhase(StrEnum):
    """High-level task lifecycle persisted in LangGraph state."""

    UNDERSTANDING_REQUEST = "UNDERSTANDING_REQUEST"
    COLLECTING_INFORMATION = "COLLECTING_INFORMATION"
    READY_FOR_PLAN = "READY_FOR_PLAN"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    WAITING_FOR_ORACLE = "WAITING_FOR_ORACLE"
    PROCESSING_RESULT = "PROCESSING_RESULT"
    AWAITING_USER_INPUT = "AWAITING_USER_INPUT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AgentTaskConfidence(StrEnum):
    """Execution-safety signal; it is intentionally not shown as a score."""

    HIGH_CONFIDENCE = "HIGH_CONFIDENCE"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    UNSAFE_TO_EXECUTE = "UNSAFE_TO_EXECUTE"


@dataclass(frozen=True, slots=True)
class AgentTaskUnderstanding:
    """Provider-independent interpretation of the current conversation."""

    intent: AgentTaskIntent
    phase: AgentTaskPhase
    confidence: AgentTaskConfidence
    parameters: dict[str, Any] = field(default_factory=dict)
    missing_parameters: tuple[str, ...] = ()
    clarification_prompt: str | None = None
    objective: str = ""

    def to_payload(self) -> dict[str, Any]:
        """Return a JSON-like payload safe for a LangGraph checkpoint."""
        return {
            "intent": self.intent.value,
            "phase": self.phase.value,
            "confidence": self.confidence.value,
            "parameters": dict(self.parameters),
            "missing_parameters": list(self.missing_parameters),
            "clarification_prompt": self.clarification_prompt,
            "objective": self.objective,
        }


class AgentTaskInterpreter:
    """Extract common EPM business intent and slots from conversation history.

    The LLM remains responsible for flexible language understanding.  This
    deterministic layer supplies safety-critical continuity, common synonyms,
    relative-period handling, and missing-input checks so provider variance
    cannot silently bypass clarification.
    """

    _MONTHS = {
        "jan": "Jan",
        "january": "Jan",
        "feb": "Feb",
        "february": "Feb",
        "mar": "Mar",
        "march": "Mar",
        "apr": "Apr",
        "april": "Apr",
        "may": "May",
        "jun": "Jun",
        "june": "Jun",
        "jul": "Jul",
        "july": "Jul",
        "aug": "Aug",
        "august": "Aug",
        "sep": "Sep",
        "sept": "Sep",
        "september": "Sep",
        "oct": "Oct",
        "october": "Oct",
        "nov": "Nov",
        "november": "Nov",
        "dec": "Dec",
        "december": "Dec",
    }
    _MONTH_SEQUENCE = (
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    )
    _DIMENSIONS = (
        "Account",
        "Entity",
        "Product",
        "Employee",
        "Job",
        "Project",
        "Scenario",
        "Version",
        "Period",
        "Year",
        "Currency",
    )
    _CANCEL_PATTERN = re.compile(
        r"^(?:please\s+)?(?:stop|cancel|abort|leave\s+it|don't\s+continue|"
        r"do\s+not\s+continue|skip\s+this|cancel\s+the\s+current\s+task)\b",
        re.IGNORECASE,
    )
    _HELP_PATTERN = re.compile(
        r"^(?:help|explain|what\s+(?:can|does|is|are)|how\s+(?:can|does|do))\b",
        re.IGNORECASE,
    )
    _NEW_TASK_START = re.compile(
        r"^(?:(?:i\s+want\s+to|please|can\s+you)\s+)?"
        r"(?:run|execute|start|load|import|update|add|create|prepare|"
        r"show|generate|refresh|calculate|recalculate|compute|recompute|"
        r"seed|complete|check|review|compare)\b",
        re.IGNORECASE,
    )
    _CALCULATION_ACTION = re.compile(
        r"^\s*(?:(?:please|kindly)\s+|(?:can|could|would)\s+you\s+|"
        r"i\s+(?:want|need)\s+to\s+)*"
        r"(?:(?:calculate|recalculate|compute|recompute)\b|"
        r"(?:run|execute|start|perform|do)\b.{1,100}\b"
        r"(?:calculations?|calc)\b)",
        re.IGNORECASE,
    )
    _INTENT_PATTERNS: tuple[tuple[AgentTaskIntent, re.Pattern[str]], ...] = (
        (
            AgentTaskIntent.MONTH_CLOSE,
            re.compile(
                r"\b(?:month(?:ly)?[- ]?end|month(?:ly)?)\s+clos(?:e|ing)|"
                r"\bclos(?:e|ing)\s+(?:activities|the\s+month|this\s+month)|"
                r"\bclosing\s+activities\b|"
                r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
                r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
                r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(?:month\s+)?close\b",
                re.IGNORECASE,
            ),
        ),
        (
            AgentTaskIntent.FORECAST_SEEDING,
            re.compile(
                r"\b(?:seed(?:ing)?|prepare|create)\s+"
                r"(?:(?:the|a|new)\s+){0,2}forecast\b|"
                r"\bforecast[- ]?seed(?:ing)?\b|"
                r"\bcopy\s+actuals?\s+(?:in)?to\s+forecast\b|"
                r"\b(?:actuals?|plan(?:ning)?|budget)\s+"
                r"(?:to|into)\s+forecast\b|"
                r"\bactuals?\s+through\s+.+\bforecast\b",
                re.IGNORECASE,
            ),
        ),
        (
            AgentTaskIntent.VARIANCE_REPORTING,
            re.compile(
                r"\bvariance(?:s|\s+report|\s+reporting)?\b|"
                r"\b(?:actual|forecast|budget)\s+(?:vs\.?|versus)\s+"
                r"(?:actual|forecast|budget)\b|\bunusual\s+differences?\b",
                re.IGNORECASE,
            ),
        ),
        (
            AgentTaskIntent.METADATA_LOAD,
            re.compile(
                r"\b(?:load|import|update)\b.{0,60}\bmetadata\b|"
                r"\bmetadata\s+(?:load|import|update)\b|"
                r"\b(?:load|import|update)\s+(?:the\s+)?"
                r"(?:account|entity|product)\s+metadata\b|"
                r"\b(?:load|import|update|add)\s+(?:(?:the|new)\s+){0,2}"
                r"(?:account|entity|product|employee|job|project|scenario|"
                r"version|period|year|currency)\s+"
                r"(?:dimensions?|hierarch(?:y|ies)|members?)\b|"
                r"\b(?:account|entity|product|employee|job|project|scenario|"
                r"version|period|year|currency)\s+"
                r"(?:dimensions?|hierarch(?:y|ies)|members?)\s+"
                r"(?:load|import|update)\b|"
                r"\b(?:add|create)\b.{1,100}\bas\s+(?:a\s+|an\s+)?"
                r"new\s+(?:product|account|entity|member)\b|"
                r"\b(?:add|create)\s+(?:a\s+)?new\s+"
                r"(?:product|account|entity|member)\b",
                re.IGNORECASE,
            ),
        ),
        (
            AgentTaskIntent.RUN_DATA_INTEGRATION,
            re.compile(
                r"\b(?:run|execute|start)\b.{0,100}\bdata\s+integration\b",
                re.IGNORECASE,
            ),
        ),
        (
            AgentTaskIntent.RUN_PIPELINE,
            re.compile(
                r"\b(?:run|execute|start)\b.{0,100}\bpipeline\b",
                re.IGNORECASE,
            ),
        ),
        (
            AgentTaskIntent.RUN_BUSINESS_RULE,
            re.compile(
                r"\b(?:run|execute|calculate|start)\b.{0,100}\b"
                r"(?:business\s+rule|calculation\s+rule|calc\s+rule|"
                r"plan\s+rule|rule)\b",
                re.IGNORECASE,
            ),
        ),
        (
            AgentTaskIntent.JOB_STATUS,
            re.compile(
                r"\b(?:job|execution|load|run)\s+(?:status|finished|complete)|"
                r"\b(?:check|show|tell).+\b(?:job|execution|load|run)\b|"
                r"\b(?:latest|last|failed)\s+(?:job|execution|run)\b",
                re.IGNORECASE,
            ),
        ),
        (
            AgentTaskIntent.DATA_LOAD,
            re.compile(
                r"\b(?:load|import)\b.{0,60}\b"
                r"(?:actuals?|planning\s+data|data(?:\s+file)?)\b|"
                r"\bpush\b.{0,60}\b(?:actuals?|data\s+file)\b|"
                r"\bdata\s+(?:load|import)\b",
                re.IGNORECASE,
            ),
        ),
    )

    @classmethod
    def interpret(
        cls,
        messages: Sequence[AgentMessage],
        *,
        today: date | None = None,
    ) -> AgentTaskUnderstanding:
        """Interpret all user turns so short answers retain task context."""
        user_turns = [
            message.content.strip()
            for message in messages
            if message.role is AgentMessageRole.USER and message.content.strip()
        ]
        if not user_turns:
            return cls._understanding(AgentTaskIntent.UNKNOWN, {}, "")

        latest = user_turns[-1]
        if cls._CANCEL_PATTERN.search(latest):
            return AgentTaskUnderstanding(
                intent=AgentTaskIntent.CANCEL_OPERATION,
                phase=AgentTaskPhase.CANCELLED,
                confidence=AgentTaskConfidence.HIGH_CONFIDENCE,
                objective=latest,
            )

        intent = cls._latest_intent(user_turns)
        if intent is AgentTaskIntent.UNKNOWN and cls._HELP_PATTERN.search(latest):
            intent = AgentTaskIntent.HELP_EXPLAIN
        task_turns = cls._current_task_turns(user_turns, intent)
        parameters = (
            {}
            if intent in {AgentTaskIntent.UNKNOWN, AgentTaskIntent.HELP_EXPLAIN}
            else cls._extract_parameters(task_turns, intent, today=today)
        )
        objective = cls._objective(task_turns, intent)
        return cls._understanding(intent, parameters, objective)

    @classmethod
    def _current_task_turns(
        cls,
        turns: Sequence[str],
        intent: AgentTaskIntent,
    ) -> Sequence[str]:
        """Exclude inputs from older tasks without discarding same-task replies."""
        if intent in {AgentTaskIntent.UNKNOWN, AgentTaskIntent.HELP_EXPLAIN}:
            return turns[-1:]
        anchor = 0
        previous_start = ""
        for index, text in enumerate(turns):
            if not cls._NEW_TASK_START.search(text):
                continue
            candidate = cls._direct_intent(text)
            if candidate not in {AgentTaskIntent.UNKNOWN, intent}:
                previous_start = ""
            if candidate is intent:
                if (
                    index > 0
                    and intent is AgentTaskIntent.DATA_LOAD
                    and cls._is_data_load_method_reply(text.casefold())
                    and any(
                        cls._matches_intent(prior, AgentTaskIntent.DATA_LOAD)
                        for prior in turns[:index]
                    )
                ):
                    continue
                normalized = re.sub(r"\W+", " ", text.casefold()).strip()
                if normalized != previous_start:
                    anchor = index
                    previous_start = normalized
        return turns[anchor:]

    @classmethod
    def _latest_intent(cls, turns: Sequence[str]) -> AgentTaskIntent:
        latest = " ".join(turns[-1].casefold().split())
        if re.search(r"\bmetadata\s+(?:import\s+)?job\b", latest):
            # An exact saved Planning job is already handled by the existing
            # governed metadata operation and must not be converted into a
            # business-level dimension/file interview.
            return AgentTaskIntent.UNKNOWN
        if len(turns) > 1 and any(
            cls._matches_intent(text, AgentTaskIntent.MONTH_CLOSE)
            for text in turns[:-1]
        ):
            activity_terms = re.findall(
                r"\b(?:load|import|run|execute|calculate|allocation|refresh|"
                r"variance|report|pipeline|integration|rule|push)\w*\b",
                latest,
            )
            if len(activity_terms) >= 2 or (
                len(activity_terms) >= 1
                and bool(re.search(r",|;|\bthen\b|\bfollowed\s+by\b", latest))
            ):
                return AgentTaskIntent.MONTH_CLOSE
        if len(turns) > 1 and any(
            cls._matches_intent(text, AgentTaskIntent.DATA_LOAD)
            for text in turns[:-1]
        ) and cls._is_data_load_method_reply(latest):
            # This short answer completes the existing business task; it must
            # not discard the scenario, period, and file already collected.
            return AgentTaskIntent.DATA_LOAD
        if len(turns) > 1 and any(
            cls._matches_intent(text, AgentTaskIntent.FORECAST_SEEDING)
            for text in turns[:-1]
        ) and cls._is_forecast_execution_method_reply(latest):
            return AgentTaskIntent.FORECAST_SEEDING
        if len(turns) > 1 and re.search(
            r"^(?:(?:show|list)\s+(?:me\s+)?(?:the\s+)?"
            r"(?:other\s+|available\s+)?rules?\b|"
            r"(?:another|other|different)\s+(?:rule|one|option)\b)",
            latest,
        ) and any(
            cls._matches_intent(text, AgentTaskIntent.FORECAST_SEEDING)
            for text in turns[:-1]
        ):
            return AgentTaskIntent.FORECAST_SEEDING
        if len(turns) > 1 and any(
            cls._matches_intent(text, AgentTaskIntent.VARIANCE_REPORTING)
            for text in turns[:-1]
        ) and cls._is_variance_view_reply(latest):
            return AgentTaskIntent.VARIANCE_REPORTING
        direct = cls._direct_intent(turns[-1])
        if direct is not AgentTaskIntent.UNKNOWN:
            return direct
        if len(turns) > 1 and cls._is_confirmation_reply(latest) and any(
            cls._matches_intent(text, AgentTaskIntent.RUN_BUSINESS_RULE)
            for text in turns[:-1]
        ):
            return AgentTaskIntent.RUN_BUSINESS_RULE
        if len(turns) < 2:
            return AgentTaskIntent.UNKNOWN
        for index in range(len(turns) - 2, -1, -1):
            text = turns[index]
            prior = cls._direct_intent(text)
            if prior is AgentTaskIntent.UNKNOWN:
                continue
            if cls._is_context_reply(latest):
                return prior
            if (
                cls._is_open_slot_reply(latest)
                and cls._missing_parameters(
                    prior,
                    cls._extract_parameters(turns[index:-1], prior, today=None),
                )
            ):
                return prior
            break
        return AgentTaskIntent.UNKNOWN

    @classmethod
    def _direct_intent(cls, text: str) -> AgentTaskIntent:
        if (
            cls._CALCULATION_ACTION.search(text)
            and not re.search(r"\b(?:then|followed\s+by)\b", text, re.IGNORECASE)
        ):
            return AgentTaskIntent.RUN_BUSINESS_RULE
        for intent, pattern in cls._INTENT_PATTERNS:
            if pattern.search(text):
                return intent
        return AgentTaskIntent.UNKNOWN

    @classmethod
    def _is_context_reply(cls, normalized: str) -> bool:
        """Accept a short slot answer/correction, not an unrelated new topic."""
        if len(normalized.split()) > 16:
            return False
        month_pattern = r"\b(?:" + "|".join(cls._MONTHS) + r")\b"
        dimension_pattern = (
            r"\b(?:"
            + "|".join(item.casefold() for item in cls._DIMENSIONS)
            + r")\b"
        )
        return bool(
            re.search(month_pattern, normalized)
            or re.search(dimension_pattern, normalized)
            or re.search(r"\bfy\s*[0-9]{2,4}\b", normalized)
            or re.search(r"\b(?:actual|forecast|budget)\b", normalized)
            or re.search(r"\b(?:latest|newest|most\s+recent)\b", normalized)
            or re.search(r"\.(?:csv|txt|zip|dat)\b", normalized)
            or re.search(r"\bupload\b", normalized)
            or re.search(r"\b(?:instead|correction|change\s+it\s+to)\b", normalized)
            or cls._is_data_load_method_reply(normalized)
            or cls._is_confirmation_reply(normalized)
        )

    @staticmethod
    def _is_confirmation_reply(normalized: str) -> bool:
        """Recognize an affirmative continuation, never a new task."""
        words = re.findall(r"[a-z]+", normalized.casefold())
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
            "now",
        }
        affirmative = {
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
            word in affirmative for word in words
        )

    @staticmethod
    def _is_open_slot_reply(normalized: str) -> bool:
        """Keep a pending task for codes or an explicit correction, not new topics."""
        return bool(
            re.fullmatch(
                r"(?:i\s+)?already\s+(?:told|said)\s+(?:you|that)[.!]?",
                normalized,
            )
            or re.fullmatch(r"[a-z][a-z0-9-]*_[a-z0-9_-]{1,79}", normalized)
        )

    @staticmethod
    def _is_data_load_method_reply(normalized: str) -> bool:
        return bool(
            re.search(r"\bdata\s+integration\b", normalized)
            or re.search(
                r"\b(?:saved\s+)?(?:planning\s+|native\s+)?"
                r"import\s+data(?:\s+job)?\b",
                normalized,
            )
            or re.search(r"\bplanning\s+import\b", normalized)
        )

    @staticmethod
    def _is_forecast_execution_method_reply(normalized: str) -> bool:
        return bool(
            re.search(r"\b(?:oracle\s+)?pipeline\b", normalized)
            or re.search(r"\b(?:business|calculation|calc)\s+rule\b", normalized)
            or re.search(r"\bdata\s+integration\b", normalized)
        )

    @staticmethod
    def _is_variance_view_reply(normalized: str) -> bool:
        return bool(
            re.search(
                r"^use saved data explorer view `[^`]+` for (?:the )?"
                r"variance review\.?$",
                normalized,
            )
        )

    @classmethod
    def _extract_parameters(
        cls,
        turns: Sequence[str],
        intent: AgentTaskIntent,
        *,
        today: date | None,
    ) -> dict[str, Any]:
        parameters: dict[str, Any] = {}
        current = today or date.today()
        month_close_turn = next(
            (
                index
                for index, text in enumerate(turns)
                if cls._matches_intent(text, AgentTaskIntent.MONTH_CLOSE)
            ),
            None,
        )

        for index, text in enumerate(turns):
            normalized = " ".join(text.casefold().split())
            period = cls._period_from_text(normalized, current)
            if period is not None:
                parameters["period"] = period[0]
                parameters["period_reference"] = period[1]
                if intent is AgentTaskIntent.DATA_LOAD:
                    month_range = cls._month_range_from_text(normalized)
                    if month_range is not None:
                        parameters["start_period"], parameters["end_period"] = (
                            month_range
                        )
                        parameters["period"] = month_range[1]
                    elif parameters.get("start_period") and parameters.get(
                        "end_period"
                    ):
                        if re.search(r"\bstart\s+(?:month|period)\b", normalized):
                            parameters["start_period"] = period[0]
                            parameters["period"] = parameters["end_period"]
                        elif re.search(r"\bend\s+(?:month|period)\b", normalized):
                            parameters["end_period"] = period[0]
                        else:
                            # A plain month correction replaces the previous
                            # range rather than preserving stale endpoints.
                            parameters.pop("start_period", None)
                            parameters.pop("end_period", None)

            year_match = re.search(r"\bfy\s*([0-9]{2,4})\b", normalized)
            if year_match:
                raw_year = year_match.group(1)
                parameters["year"] = f"FY{raw_year}"

            comparison = re.search(
                r"\b(actual|forecast|budget)\s+(?:vs\.?|versus)\s+"
                r"(actual|forecast|budget)\b",
                normalized,
            )
            if comparison:
                parameters["comparison"] = (
                    f"{comparison.group(1).title()} vs "
                    f"{comparison.group(2).title()}"
                )

            threshold = re.search(
                r"\b(?:above|over|greater\s+than|threshold(?:\s+of)?|"
                r"tolerance(?:\s+of)?)\s*[$]?([0-9][0-9,]*(?:\.[0-9]+)?)",
                normalized,
            )
            if threshold:
                parameters["threshold"] = float(
                    threshold.group(1).replace(",", "")
                )

            variance_view = re.search(
                r"\bsaved data explorer view `([^`]+)`",
                text.strip(),
                re.IGNORECASE,
            )
            if intent is AgentTaskIntent.VARIANCE_REPORTING and variance_view:
                parameters["saved_view"] = variance_view.group(1).strip()

            if (
                intent is AgentTaskIntent.VARIANCE_REPORTING
                and re.search(r"\bpov\s+overrides?\s*:", text, re.IGNORECASE)
            ):
                overrides = {
                    dimension.strip(): member.strip()
                    for dimension, member in re.findall(
                        r"([A-Za-z][A-Za-z0-9 _-]{0,79})\s*=\s*`([^`]{1,200})`",
                        text,
                    )
                }
                if overrides:
                    parameters["pov_overrides"] = overrides

            for scenario in ("Actual", "Forecast", "Budget"):
                if re.search(rf"\b{scenario.casefold()}s?\b", normalized):
                    parameters["scenario"] = scenario

            file_match = re.search(
                r"(?:^|[\s`'\"])([A-Za-z0-9][A-Za-z0-9_.-]{0,180}"
                r"\.(?:csv|txt|zip|dat))\b",
                text,
                re.IGNORECASE,
            )
            if file_match:
                parameters["file"] = file_match.group(1).strip()
                parameters.pop("file_preference", None)
            elif (
                intent
                in {AgentTaskIntent.METADATA_LOAD, AgentTaskIntent.DATA_LOAD}
                and re.search(r"\b(?:latest|most\s+recent|newest)\b", normalized)
            ):
                parameters["file_preference"] = "latest"

            if (
                intent is AgentTaskIntent.METADATA_LOAD
                and re.search(
                    r"\b(?:i(?:'ll| will| can)\s+upload|upload\s+it|"
                    r"upload\s+on\s+the\s+(?:review|governed)\s+screen)\b",
                    normalized,
                )
            ):
                parameters["file_preference"] = "upload"

            if intent is AgentTaskIntent.METADATA_LOAD:
                member_match = re.search(
                    r"\b(?:add|create)\s+(.{1,80}?)\s+as\s+"
                    r"(?:a\s+|an\s+)?(?:new\s+)?"
                    r"(?:product|account|entity|member)\b",
                    text,
                    re.IGNORECASE,
                ) or re.search(
                    r"\b(?:add|create)\s+(?:a\s+)?new\s+"
                    r"(?:product|account|entity|member)\s+(?:(?:called|named)\s+)?"
                    r"(.{1,80}?)(?:[.!?]|$)",
                    text,
                    re.IGNORECASE,
                )
                if member_match:
                    parameters["requested_member"] = member_match.group(1).strip()
                parent_match = re.search(
                    r"\b([A-Za-z][A-Za-z0-9_-]{0,79})\s+is\s+"
                    r"(?:the\s+)?(?:name\s+of\s+)?(?:a\s+)?"
                    r"(?:dimension\s+)?member\s+under\b|"
                    r"\bparent(?:\s+member)?\s+(?:is|=)\s+"
                    r"[`'\"]?([A-Za-z][A-Za-z0-9_-]{0,79})\b|"
                    r"\b(?:add|create)\b.{0,100}?\b(?:under|beneath)\s+"
                    r"[`'\"]?([A-Za-z][A-Za-z0-9_-]{0,79})\b",
                    text,
                    re.IGNORECASE,
                )
                if parent_match:
                    parameters["parent_member"] = next(
                        value for value in parent_match.groups() if value
                    )
                    parameters.pop("possible_parent_member", None)
                elif (
                    index > 0
                    and parameters.get("requested_member")
                    and not parameters.get("parent_member")
                    and re.fullmatch(
                        r"[A-Za-z][A-Za-z0-9-]*_[A-Za-z0-9_-]{1,79}",
                        text.strip(),
                    )
                ):
                    parameters["possible_parent_member"] = text.strip()
                elif (
                    cls._is_confirmation_reply(normalized)
                    and parameters.get("possible_parent_member")
                    and not parameters.get("parent_member")
                ):
                    parameters["parent_member"] = parameters.pop(
                        "possible_parent_member"
                    )
                for dimension in cls._DIMENSIONS:
                    if re.search(
                        rf"\b{re.escape(dimension.casefold())}\b",
                        normalized,
                    ):
                        parameters["dimension"] = dimension
                        break

            if intent is AgentTaskIntent.DATA_LOAD:
                if re.search(r"\bdata\s+integration\b", normalized):
                    parameters["load_method"] = "DATA_INTEGRATION"
                elif re.search(
                    r"\b(?:saved\s+)?(?:planning\s+|native\s+)?"
                    r"import\s+data(?:\s+job)?\b|"
                    r"\bplanning\s+import\b",
                    normalized,
                ):
                    parameters["load_method"] = "PLANNING_IMPORT"

            if intent is AgentTaskIntent.FORECAST_SEEDING:
                if re.search(
                    r"\bforecast[- ]?seed(?:ing)?\b|"
                    r"\bseed(?:ing)?\b.{0,50}\bforecast\b|"
                    r"\b(?:actuals?|plan(?:ning)?|budget)\s+"
                    r"(?:to|into)\s+forecast\b",
                    normalized,
                ):
                    parameters["seed_requested"] = True
                if re.search(r"\b(?:oracle\s+)?pipeline\b", normalized):
                    parameters["execution_method"] = "PIPELINE"
                elif re.search(
                    r"\b(?:business|calculation|calc)\s+rule\b",
                    normalized,
                ):
                    parameters["execution_method"] = "BUSINESS_RULE"
                elif re.search(r"\bdata\s+integration\b", normalized):
                    parameters["execution_method"] = "DATA_INTEGRATION"

            if intent is AgentTaskIntent.FORECAST_SEEDING and period is not None:
                if re.search(
                    r"\b(?:through|until|retain(?:ed)?\s+"
                    r"(?:actuals?\s+)?through|cutoff)\b",
                    normalized,
                ) or not cls._matches_intent(text, AgentTaskIntent.FORECAST_SEEDING):
                    parameters["cutoff_period"] = period[0]

            if (
                intent is AgentTaskIntent.MONTH_CLOSE
                and month_close_turn is not None
                and index > month_close_turn
                and cls._contains_activity_description(normalized)
            ):
                parameters["activities"] = cls._activity_list(text)

        return parameters

    @classmethod
    def _period_from_text(
        cls,
        normalized: str,
        current: date,
    ) -> tuple[str, str] | None:
        if re.search(r"\b(?:last|previous)\s+month(?:'s)?\b", normalized):
            month_index = current.month - 2
            return cls._MONTH_SEQUENCE[month_index], "previous_month"
        if re.search(r"\bthis\s+month(?:'s)?\b", normalized):
            return cls._MONTH_SEQUENCE[current.month - 1], "current_month"
        matches = list(
            re.finditer(
                r"\b(" + "|".join(cls._MONTHS) + r")\b",
                normalized,
                re.IGNORECASE,
            )
        )
        if not matches:
            return None
        raw = matches[-1].group(1).casefold()
        return cls._MONTHS[raw], "explicit"

    @classmethod
    def _month_range_from_text(cls, normalized: str) -> tuple[str, str] | None:
        month = r"(?:" + "|".join(cls._MONTHS) + r")"
        period = rf"\b({month})\b(?:[-/#]\s*(?:fy)?\d{{2,4}})?"
        connector = r"\s*(?:to|through|thru|until|[-–—])\s*"
        match = re.search(period + connector + period, normalized)
        if match is None:
            match = re.search(
                r"\bbetween\s+" + period + r"\s+and\s+" + period,
                normalized,
            )
        if match is None:
            return None
        return (
            cls._MONTHS[match.group(1).casefold()],
            cls._MONTHS[match.group(2).casefold()],
        )

    @classmethod
    def _understanding(
        cls,
        intent: AgentTaskIntent,
        parameters: dict[str, Any],
        objective: str,
    ) -> AgentTaskUnderstanding:
        missing = cls._missing_parameters(intent, parameters)
        if intent is AgentTaskIntent.CANCEL_OPERATION:
            phase = AgentTaskPhase.CANCELLED
            confidence = AgentTaskConfidence.HIGH_CONFIDENCE
        elif intent is AgentTaskIntent.UNKNOWN:
            phase = AgentTaskPhase.UNDERSTANDING_REQUEST
            confidence = AgentTaskConfidence.UNSAFE_TO_EXECUTE
        elif missing:
            phase = AgentTaskPhase.COLLECTING_INFORMATION
            confidence = AgentTaskConfidence.NEEDS_CLARIFICATION
        else:
            phase = AgentTaskPhase.READY_FOR_PLAN
            confidence = AgentTaskConfidence.HIGH_CONFIDENCE
        return AgentTaskUnderstanding(
            intent=intent,
            phase=phase,
            confidence=confidence,
            parameters=parameters,
            missing_parameters=missing,
            clarification_prompt=cls._clarification_prompt(
                intent, missing, parameters
            ),
            objective=objective,
        )

    @staticmethod
    def _missing_parameters(
        intent: AgentTaskIntent,
        parameters: dict[str, Any],
    ) -> tuple[str, ...]:
        requirements: dict[AgentTaskIntent, tuple[str, ...]] = {
            AgentTaskIntent.MONTH_CLOSE: ("period", "activities"),
            AgentTaskIntent.METADATA_LOAD: ("dimension", "file_reference"),
            AgentTaskIntent.DATA_LOAD: (
                "scenario",
                "period",
                "file_reference",
            ),
            AgentTaskIntent.FORECAST_SEEDING: ("cutoff_period",),
            AgentTaskIntent.VARIANCE_REPORTING: ("comparison", "period"),
        }
        missing: list[str] = []
        for name in requirements.get(intent, ()):
            if (
                intent is AgentTaskIntent.FORECAST_SEEDING
                and name == "cutoff_period"
                and parameters.get("seed_requested")
            ):
                continue
            if name == "file_reference":
                if not (parameters.get("file") or parameters.get("file_preference")):
                    missing.append(name)
            elif not parameters.get(name):
                missing.append(name)
        return tuple(missing)

    @staticmethod
    def _clarification_prompt(
        intent: AgentTaskIntent,
        missing: tuple[str, ...],
        parameters: dict[str, Any],
    ) -> str | None:
        if not missing:
            return None
        if (
            intent is AgentTaskIntent.METADATA_LOAD
            and "file_reference" in missing
            and parameters.get("requested_member")
        ):
            possible_parent = str(
                parameters.get("possible_parent_member") or ""
            ).strip()
            if possible_parent and not parameters.get("parent_member"):
                return (
                    f"I still have your request to add a new member. Is "
                    f"{possible_parent} its parent member? I can prepare a "
                    "Metadata Import once a file containing the member and "
                    "parent is available; chat cannot create it directly."
                )
            parent = str(parameters.get("parent_member") or "").strip()
            parent_context = (
                f" You identified {parent} as its parent; I have not "
                "verified that in Oracle."
                if parent else ""
            )
            return (
                "I can prepare a governed Metadata Import, but I cannot "
                "create a single member directly from chat. The metadata "
                "file must define the new member and its parent."
                + parent_context
                + " Which metadata file contains this change? Give its "
                "filename, or say 'I'll upload it' to choose a file on the "
                "review screen."
            )
        prompts: dict[tuple[AgentTaskIntent, str], str] = {
            (
                AgentTaskIntent.MONTH_CLOSE,
                "period",
            ): "Which period are we closing?",
            (
                AgentTaskIntent.MONTH_CLOSE,
                "activities",
            ): (
                "I don't have a Month Close workflow selected for this task yet. "
                "Which activities should it include?"
            ),
            (
                AgentTaskIntent.METADATA_LOAD,
                "dimension",
            ): "Sure. Which dimension do you want to update?",
            (
                AgentTaskIntent.METADATA_LOAD,
                "file_reference",
            ): (
                "Which metadata file should I use? You can also say to use "
                "the latest one."
            ),
            (
                AgentTaskIntent.DATA_LOAD,
                "scenario",
            ): (
                "Which data are you loading—for example, Actual, Budget, or "
                "Forecast?"
            ),
            (
                AgentTaskIntent.DATA_LOAD,
                "period",
            ): "Which period should I load?",
            (
                AgentTaskIntent.DATA_LOAD,
                "file_reference",
            ): (
                "Which data file should I use? You can also ask me to find "
                "the latest one."
            ),
            (
                AgentTaskIntent.DATA_LOAD,
                "load_method",
            ): (
                "How should Oracle load this file: use a configured Data "
                "Integration, or run a saved Planning Import Data job?"
            ),
            (
                AgentTaskIntent.FORECAST_SEEDING,
                "cutoff_period",
            ): (
                "Through which month should Actual data be retained before "
                "Forecast begins?"
            ),
            (
                AgentTaskIntent.VARIANCE_REPORTING,
                "comparison",
            ): (
                "Which comparison would you like: Actual vs Budget or Actual "
                "vs Forecast?"
            ),
            (
                AgentTaskIntent.VARIANCE_REPORTING,
                "period",
            ): "Which period should I use for the variance review?",
        }
        return prompts.get((intent, missing[0]))

    @classmethod
    def _matches_intent(cls, text: str, intent: AgentTaskIntent) -> bool:
        return any(
            candidate is intent and pattern.search(text)
            for candidate, pattern in cls._INTENT_PATTERNS
        )

    @staticmethod
    def _contains_activity_description(normalized: str) -> bool:
        return bool(
            re.search(
                r"\b(?:load|import|run|execute|calculate|allocation|refresh|"
                r"variance|report|pipeline|integration|rule|push)\b",
                normalized,
            )
        )

    @staticmethod
    def _activity_list(text: str) -> list[str]:
        parts = re.split(
            r"\s*(?:,|;|\bthen\b|\bfollowed\s+by\b|\band\s+then\b)\s*",
            text,
            flags=re.IGNORECASE,
        )
        return [" ".join(part.split()) for part in parts if part.strip()]

    @classmethod
    def _objective(
        cls,
        turns: Sequence[str],
        intent: AgentTaskIntent,
    ) -> str:
        for text in turns:
            if cls._matches_intent(text, intent):
                return " ".join(text.split())[:1_000]
        return " ".join(turns[-1].split())[:1_000]

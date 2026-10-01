"""Deterministic resolution of answers to structured artifact choices."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from app.agent.models import AgentClarificationRequest


class ClarificationChoiceStatus(StrEnum):
    SELECTED = "selected"
    CANCELLED = "cancelled"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class ClarificationChoiceResolution:
    status: ClarificationChoiceStatus
    value: str | None = None
    reason: str = ""


class ClarificationChoiceResolver:
    """Resolve natural short replies only when one choice is provable."""

    _CANCEL = re.compile(
        r"^\s*(?:cancel|cancel\s+this(?:\s+task)?|stop|stop\s+this|"
        r"never\s*mind|skip\s+this|go\s+back|none)\s*[.!]?\s*$",
        re.IGNORECASE,
    )
    _SELECTION_PREFIX = re.compile(
        r"^\s*(?:(?:please\s+)?(?:use|select|choose|pick|run|execute|"
        r"start|prepare)(?:\s+option)?\s+)",
        re.IGNORECASE,
    )
    _ORDINALS = {
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

    @classmethod
    def resolve(
        cls,
        prompt: str,
        request: AgentClarificationRequest,
    ) -> ClarificationChoiceResolution:
        text = str(prompt or "").strip()
        if cls._CANCEL.fullmatch(text):
            return ClarificationChoiceResolution(
                ClarificationChoiceStatus.CANCELLED,
                reason="explicit_cancel",
            )
        options = tuple(request.options)
        if not text or not options:
            return ClarificationChoiceResolution(
                ClarificationChoiceStatus.UNRESOLVED,
                reason="no_selectable_options",
            )

        direct = cls._match_alias(text, request)
        if direct is not None:
            return ClarificationChoiceResolution(
                ClarificationChoiceStatus.SELECTED,
                value=direct,
                reason="exact_name",
            )
        without_prefix = cls._SELECTION_PREFIX.sub("", text, count=1)
        if without_prefix != text:
            prefixed = cls._match_alias(without_prefix, request)
            if prefixed is not None:
                return ClarificationChoiceResolution(
                    ClarificationChoiceStatus.SELECTED,
                    value=prefixed,
                    reason="explicit_name",
                )

        ordinal = cls.ordinal_selection(text)
        if ordinal is not None:
            index = len(options) - 1 if ordinal == -1 else ordinal
            if 0 <= index < len(options):
                return ClarificationChoiceResolution(
                    ClarificationChoiceStatus.SELECTED,
                    value=options[index],
                    reason="ordinal",
                )
            return ClarificationChoiceResolution(
                ClarificationChoiceStatus.UNRESOLVED,
                reason="ordinal_out_of_range",
            )

        dominant = cls._dominant_recommendation(request)
        normalized = cls._normalize(text)
        if normalized in {
            "recommended",
            "recommended one",
            "suggested",
            "suggested one",
            "best match",
            "top match",
            "use recommended",
            "use recommended one",
            "use the recommended",
            "use the recommended one",
        } and dominant is not None:
            return ClarificationChoiceResolution(
                ClarificationChoiceStatus.SELECTED,
                value=dominant,
                reason="dominant_recommendation",
            )
        if normalized in {
            "other",
            "other one",
            "the other",
            "the other one",
            "use other",
            "use the other one",
        } and len(options) == 2 and dominant is not None:
            return ClarificationChoiceResolution(
                ClarificationChoiceStatus.SELECTED,
                value=next(item for item in options if item != dominant),
                reason="other_than_recommendation",
            )
        return ClarificationChoiceResolution(
            ClarificationChoiceStatus.UNRESOLVED,
            reason="no_unique_match",
        )

    @classmethod
    def _match_alias(
        cls,
        text: str,
        request: AgentClarificationRequest,
    ) -> str | None:
        candidate = cls._normalize(text)
        matches: set[str] = set()
        for option in request.options:
            label = request.option_labels.get(option, option)
            if candidate in {cls._normalize(option), cls._normalize(label)}:
                matches.add(option)
        return next(iter(matches)) if len(matches) == 1 else None

    @classmethod
    def ordinal_selection(cls, text: str) -> int | None:
        """Return a zero-based ordinal only for an unambiguous short reply."""
        normalized = cls._normalize(text)
        words = normalized.split()
        if not words or len(words) > 8:
            return None
        filler = {
            "the", "one", "option", "number", "please", "use", "select",
            "choose", "pick", "run", "execute", "start", "prepare",
        }
        meaningful = [word for word in words if word not in filler]
        return cls._ORDINALS.get(meaningful[0]) if len(meaningful) == 1 else None

    @classmethod
    def _dominant_recommendation(
        cls,
        request: AgentClarificationRequest,
    ) -> str | None:
        available = set(request.options)
        ranked = [
            item
            for item in request.recommendations
            if str(item.get("name") or "") in available
        ]
        ranked.sort(key=lambda item: -int(item.get("score") or 0))
        if not ranked:
            return None
        top = ranked[0]
        if str(top.get("confidence") or "") != "Strong match":
            return None
        top_score = int(top.get("score") or 0)
        if top_score < 65:
            return None
        if len(ranked) > 1 and top_score - int(ranked[1].get("score") or 0) < 15:
            return None
        return str(top.get("name") or "")

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", str(value).casefold()))

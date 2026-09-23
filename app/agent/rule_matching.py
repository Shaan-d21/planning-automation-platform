"""Deterministic matching between a Planning task description and live rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from math import ceil


_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_WORD = re.compile(r"[^a-z0-9]+")
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "application",
        "business",
        "calculate",
        "calculation",
        "complete",
        "execute",
        "for",
        "in",
        "of",
        "oracle",
        "planning",
        "pipeline",
        "prepare",
        "process",
        "rule",
        "run",
        "task",
        "the",
        "this",
        "to",
        "using",
        "want",
        "with",
    }
)

_LOAD_GENERIC_TERMS = frozenset(
    {
        "data",
        "dimension",
        "dimensions",
        "file",
        "files",
        "hierarchy",
        "hierarchies",
        "import",
        "integration",
        "job",
        "load",
        "loading",
        "member",
        "members",
        "metadata",
        "new",
        "planning",
        "some",
        "from",
        "through",
        "until",
    }
)
_LOAD_PERIOD_TERMS = frozenset(
    {
        "jan", "january", "feb", "february", "mar", "march",
        "apr", "april", "may", "jun", "june", "jul", "july",
        "aug", "august", "sep", "sept", "september", "oct",
        "october", "nov", "november", "dec", "december",
    }
)
_LOAD_TOKEN_ALIASES = {
    "items": "product",
    "item": "product",
    "products": "product",
    "skus": "product",
    "sku": "product",
    "unit": "volume",
    "units": "volume",
    "quantity": "volume",
    "quantities": "volume",
    "qty": "volume",
    "volumes": "volume",
    "actuals": "actual",
    "forecasts": "forecast",
}
_METADATA_PURPOSE_TERMS = frozenset(
    {"metadata", "dimension", "hierarchy", "member", "masterdata"}
)
_DATA_PURPOSE_TERMS = frozenset(
    {
        "actual",
        "budget",
        "forecast",
        "amount",
        "balance",
        "fact",
        "revenue",
        "sales",
        "transaction",
        "volume",
    }
)


@dataclass(frozen=True, slots=True)
class BusinessRuleMatch:
    """One explainable recommendation from the current Oracle rule catalog."""

    name: str
    display_name: str
    confidence: str
    score: int
    reason: str

    def as_payload(self) -> dict[str, str | int]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "confidence": self.confidence,
            "score": self.score,
            "reason": self.reason,
        }


def recommend_business_rules(
    context: str,
    rule_names: tuple[str, ...],
    *,
    limit: int = 5,
) -> tuple[BusinessRuleMatch, ...]:
    """Rank live rule names without inventing Oracle metadata or using an LLM."""
    return recommend_artifacts(
        context,
        tuple((name, name) for name in rule_names),
        limit=limit,
    )


def recommend_forecast_seeding_rules(
    artifacts: tuple[tuple[str, str], ...],
    *,
    limit: int = 5,
) -> tuple[BusinessRuleMatch, ...]:
    """Suggest live rule names; never claim their calculation logic is proven."""
    target_terms = {"forecast", "forecasts", "projection", "projections"}
    source_terms = {
        "actual", "actuals", "plan", "budget", "prediction", "predictions"
    }
    action_terms = {
        "seed", "seeding", "copy", "create", "initialize", "initialise",
        "populate", "transfer", "move", "load", "rollforward",
    }
    matches: list[BusinessRuleMatch] = []
    for identifier, display_name in artifacts:
        tokens = set(_meaningful_tokens(display_name))
        if not tokens & target_terms:
            continue
        source = bool(tokens & source_terms)
        action = bool(tokens & action_terms)
        expanded = _CAMEL_BOUNDARY.sub(" ", display_name).casefold()
        source_to_forecast = bool(
            re.search(
                r"\b(?:actuals?|plan|budget|predictions?)\b"
                r".{0,35}\b(?:to|into)\s+(?:the\s+)?forecast\b",
                expanded,
            )
        )
        score = min(100, 35 + 20 * source + 25 * action + 20 * source_to_forecast)
        reason = (
            "Name suggests source-to-Forecast movement; verify the rule logic."
            if source_to_forecast
            else "Name suggests Forecast setup; verify the rule logic."
            if action
            else "Name mentions Forecast; seeding purpose is not verified."
        )
        matches.append(
            BusinessRuleMatch(
                name=identifier,
                display_name=display_name,
                confidence="Possible match",
                score=score,
                reason=reason,
            )
        )
    return tuple(
        sorted(matches, key=lambda item: (-item.score, item.display_name.casefold()))
        [: max(1, limit)]
    )


def recommend_artifacts(
    context: str,
    artifacts: tuple[tuple[str, str], ...],
    *,
    limit: int = 5,
) -> tuple[BusinessRuleMatch, ...]:
    """Rank canonical Oracle identifiers using their user-facing names."""
    query_tokens = _meaningful_tokens(context)
    if not query_tokens:
        return ()
    query_set = set(query_tokens)
    normalized_context = " ".join(query_tokens)
    matches: list[BusinessRuleMatch] = []
    for identifier, display_name in artifacts:
        rule_tokens = _meaningful_tokens(display_name)
        if not rule_tokens:
            continue
        rule_set = set(rule_tokens)
        exact = sorted(query_set & rule_set)
        partial = sorted(
            query_token
            for query_token in query_set - set(exact)
            if len(query_token) >= 4
            and any(
                query_token in rule_token or rule_token in query_token
                for rule_token in rule_set
                if len(rule_token) >= 4
            )
        )
        matched_rule_tokens = {
            rule_token
            for rule_token in rule_set
            if rule_token in exact
            or any(
                query_token in rule_token or rule_token in query_token
                for query_token in partial
            )
        }
        matched_query_tokens = set(exact) | set(partial)
        coverage = len(matched_rule_tokens) / len(rule_set)
        query_coverage = len(matched_query_tokens) / len(query_set)
        similarity = SequenceMatcher(
            None,
            " ".join(rule_tokens),
            normalized_context,
        ).ratio()
        score = round(
            min(
                100,
                len(exact) * 22
                + len(partial) * 10
                + coverage * 24
                + query_coverage * 18
                + similarity * 10,
            )
        )
        if (
            len(matched_query_tokens) >= 2 and score >= 50
        ) or score >= 58:
            confidence = "Strong match"
        elif exact or partial or score >= 32:
            confidence = "Possible match"
        else:
            continue
        terms = exact + [item for item in partial if item not in exact]
        reason = (
            "Matches task terms: " + ", ".join(terms[:4]) + "."
            if terms
            else "The artifact name is similar to the task description."
        )
        matches.append(
            BusinessRuleMatch(
                name=identifier,
                display_name=display_name,
                confidence=confidence,
                score=score,
                reason=reason,
            )
        )
    return tuple(
        sorted(
            matches,
            key=lambda item: (-item.score, item.display_name.casefold()),
        )[
            : max(1, limit)
        ]
    )


def filter_relevant_load_artifacts(
    context: str,
    task_intent: str,
    artifacts: tuple[tuple[str, str, str], ...],
    *,
    allow_unknown_integrations: bool = False,
) -> tuple[tuple[str, str, str], ...]:
    """Keep only load artifacts relevant to the requested business subject.

    Native Planning jobs are already separated by Oracle job type. Data
    Integrations do not expose a reliable data-versus-metadata purpose in the
    current catalog, so obvious purpose words are used only to exclude a
    contradictory route. An integration with an unknown purpose is retained
    only when its name matches a specific subject supplied by the user.

    This filters a selection list only. The selected identifier is still
    validated against the live catalog before a governed action is prepared.
    """
    normalized_intent = str(task_intent or "").strip().upper()
    if normalized_intent not in {"DATA_LOAD", "METADATA_LOAD"}:
        return ()
    subject_tokens = set(_load_subject_tokens(context))
    relevant: list[tuple[str, str, str]] = []
    for identifier, display_name, operation_code in artifacts:
        artifact_tokens = set(_canonical_load_tokens(display_name))
        if operation_code == "data-integrations":
            metadata_purpose = bool(artifact_tokens & _METADATA_PURPOSE_TERMS)
            data_purpose = bool(artifact_tokens & _DATA_PURPOSE_TERMS)
            if (
                normalized_intent == "METADATA_LOAD"
                and data_purpose
                and not metadata_purpose
            ):
                continue
            if normalized_intent == "DATA_LOAD" and metadata_purpose:
                continue
            if (
                normalized_intent == "METADATA_LOAD"
                and not subject_tokens
                and not metadata_purpose
                and not allow_unknown_integrations
            ):
                # A generic metadata request must not display every Data
                # Integration when its purpose cannot be established.
                continue
        if subject_tokens:
            matched = subject_tokens & artifact_tokens
            required = (
                1
                if len(subject_tokens) == 1
                else max(2, ceil(len(subject_tokens) / 2))
            )
            if len(matched) < required:
                continue
        relevant.append((identifier, display_name, operation_code))
    return tuple(relevant)


def _load_subject_tokens(value: str) -> tuple[str, ...]:
    without_files = re.sub(
        r"\b[A-Za-z0-9][A-Za-z0-9_.-]*\.(?:csv|txt|zip|dat)\b",
        " ",
        str(value or ""),
        flags=re.IGNORECASE,
    )
    return tuple(
        token
        for token in _canonical_load_tokens(without_files)
        if token not in _LOAD_GENERIC_TERMS
        and token not in _STOP_WORDS
        and token not in _LOAD_PERIOD_TERMS
        and not re.fullmatch(r"fy\d{2,4}|\d{2,4}", token)
    )


def _canonical_load_tokens(value: str) -> tuple[str, ...]:
    expanded = _CAMEL_BOUNDARY.sub(" ", str(value or ""))
    raw = _NON_WORD.sub(" ", expanded.casefold()).split()
    return tuple(
        _LOAD_TOKEN_ALIASES.get(token, token)
        for token in raw
        if len(token) > 1
    )


def _meaningful_tokens(value: str) -> tuple[str, ...]:
    expanded = _CAMEL_BOUNDARY.sub(" ", str(value or ""))
    tokens = tuple(
        token
        for token in _NON_WORD.sub(" ", expanded.casefold()).split()
        if len(token) > 1 and token not in _STOP_WORDS
    )
    return tokens

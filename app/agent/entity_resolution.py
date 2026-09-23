"""Reusable, observable resolution of user entity candidates."""

from __future__ import annotations

from dataclasses import dataclass

from app.agent.context import EntityResolutionStatus
from app.agent.rule_matching import recommend_artifacts


@dataclass(frozen=True, slots=True)
class EntityResolution:
    status: EntityResolutionStatus
    candidate_name: str
    canonical_name: str | None = None
    candidates: tuple[str, ...] = ()
    reason: str = ""

    def as_payload(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "candidate_name": self.candidate_name,
            "canonical_name": self.canonical_name,
            "candidates": list(self.candidates),
            "reason": self.reason,
        }


class CatalogEntityResolver:
    """Resolve only against a catalog already obtained from the platform."""

    @staticmethod
    def resolve(
        candidate: str,
        catalog: tuple[tuple[str, str], ...],
        *,
        catalog_available: bool = True,
    ) -> EntityResolution:
        requested = str(candidate or "").strip()
        if not catalog_available:
            return EntityResolution(
                status=EntityResolutionStatus.CATALOG_UNAVAILABLE,
                candidate_name=requested,
                reason="The current environment catalog could not be read.",
            )
        if not requested:
            return EntityResolution(
                status=EntityResolutionStatus.UNRESOLVED,
                candidate_name="",
                reason="No entity name was provided.",
            )
        normalized = requested.casefold()
        exact = [
            identifier
            for identifier, display_name in catalog
            if identifier.casefold() == normalized
            or display_name.casefold() == normalized
        ]
        exact = list(dict.fromkeys(exact))
        if len(exact) == 1:
            return EntityResolution(
                status=EntityResolutionStatus.EXACT,
                candidate_name=requested,
                canonical_name=exact[0],
                candidates=(exact[0],),
                reason="Matched a current catalog identifier exactly.",
            )
        if len(exact) > 1:
            return EntityResolution(
                status=EntityResolutionStatus.AMBIGUOUS,
                candidate_name=requested,
                candidates=tuple(exact),
                reason="The display name maps to more than one catalog identifier.",
            )
        ranked = recommend_artifacts(requested, catalog)
        if not ranked:
            return EntityResolution(
                status=EntityResolutionStatus.NOT_FOUND,
                candidate_name=requested,
                reason="No current catalog candidate matched the supplied name.",
            )
        strongest = tuple(item for item in ranked if item.confidence == "Strong match")
        if len(strongest) == 1 and (
            len(ranked) == 1 or strongest[0].score >= ranked[1].score + 15
        ):
            return EntityResolution(
                status=EntityResolutionStatus.ONE_CANDIDATE,
                candidate_name=requested,
                candidates=(strongest[0].name,),
                reason=(
                    "One likely current catalog candidate was found; user "
                    "confirmation is still required."
                ),
            )
        return EntityResolution(
            status=EntityResolutionStatus.AMBIGUOUS,
            candidate_name=requested,
            candidates=tuple(item.name for item in ranked),
            reason="Several current catalog candidates require clarification.",
        )


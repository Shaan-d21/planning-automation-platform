"""Shared Planning metadata snapshot contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.models.environment import MemberInfo, PlanTypeInfo


@dataclass(frozen=True, slots=True)
class VerifiedPlanningMetadataSnapshot:
    """Environment-scoped metadata accepted only after an admin/live sync.

    The snapshot is a compatibility source, not a permissive cache. Callers
    must reject stale snapshots and must never use it to hide authentication or
    authorization failures from a live Oracle request.
    """

    environment_key: str
    verified_at: datetime
    plan_types: tuple[PlanTypeInfo, ...]
    members: dict[tuple[str, str], tuple[MemberInfo, ...]] = field(
        default_factory=dict
    )
    source: str = "verified_admin_snapshot"

    def is_fresh(
        self,
        *,
        now: datetime | None = None,
        max_age: timedelta = timedelta(hours=24),
    ) -> bool:
        observed = now or datetime.now(UTC)
        verified = self.verified_at
        if verified.tzinfo is None:
            verified = verified.replace(tzinfo=UTC)
        return timedelta(0) <= observed - verified <= max_age

    def members_for(
        self,
        cube: str,
        dimension: str,
    ) -> tuple[MemberInfo, ...] | None:
        requested = (cube.casefold(), dimension.casefold())
        for key, values in self.members.items():
            normalized = (str(key[0]).casefold(), str(key[1]).casefold())
            if normalized == requested:
                return tuple(values)
        return None

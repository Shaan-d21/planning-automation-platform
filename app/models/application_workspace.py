"""Non-secret registered Oracle application workspace models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.products.contracts import BusinessProcessType


@dataclass(frozen=True, slots=True)
class ApplicationWorkspace:
    """One Oracle application a platform user is allowed to enter."""

    application_id: int
    application_name: str
    business_process: BusinessProcessType
    product_type: str | None
    application_type: str | None
    active: bool
    last_verified_at: datetime | None
    current: bool = False


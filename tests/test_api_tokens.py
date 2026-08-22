"""Security tests for scoped external-client tokens."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.models.api_token import ApiTokenScope
from app.services.access_control_service import AccessControlService
from app.services.api_token_service import ApiTokenService
from app.utils.exceptions import ApiTokenError


def _services(tmp_path: Path):
    database = tmp_path / "tokens.sqlite3"
    access = AccessControlService(database)
    user = access.bootstrap_administrator(
        username="admin",
        display_name="Platform Administrator",
        email="admin@example.com",
        password="Test password 123!",
    )
    return user, ApiTokenService(database, access_control=access)


def test_token_secret_is_returned_once_and_authenticates_scope(
    tmp_path: Path,
) -> None:
    user, service = _services(tmp_path)
    issued = service.create(
        user_id=user.user_id,
        name="Excel Pipeline Runner",
        scopes=frozenset(
            {
                ApiTokenScope.PIPELINE_READ,
                ApiTokenScope.PIPELINE_RUN,
            }
        ),
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )

    assert issued.token.startswith(f"bepm_{issued.record.token_prefix}_")
    assert not hasattr(issued.record, "token_hash")
    authenticated = service.authenticate(
        issued.token,
        required_scope=ApiTokenScope.PIPELINE_RUN,
        ip_address="127.0.0.1",
    )
    assert authenticated.user.username == "admin"
    assert authenticated.record.token_id == issued.record.token_id
    assert service.list_for_user(user.user_id) == (service.get(issued.record.token_id),)


def test_token_scope_and_revocation_are_enforced(tmp_path: Path) -> None:
    user, service = _services(tmp_path)
    issued = service.create(
        user_id=user.user_id,
        name="Read only inspection",
        scopes=frozenset({ApiTokenScope.PIPELINE_READ}),
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )

    with pytest.raises(ApiTokenError, match="does not permit"):
        service.authenticate(
            issued.token,
            required_scope=ApiTokenScope.PIPELINE_RUN,
        )

    service.revoke(issued.record.token_id)
    with pytest.raises(ApiTokenError, match="invalid or unavailable"):
        service.authenticate(
            issued.token,
            required_scope=ApiTokenScope.PIPELINE_READ,
        )

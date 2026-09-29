"""Persistent browser-session security tests."""

from pathlib import Path

import pytest

from app.services.access_control_service import AccessControlService
from app.services.session_security_service import ClientContext, SessionSecurityService
from app.utils.exceptions import AccessControlError


def _client(ip: str) -> ClientContext:
    return ClientContext(
        ip_address=ip,
        country_code="IN",
        user_agent="pytest browser",
        cloudflare_ray="ray-test",
    )


def test_sessions_are_persistent_detect_concurrency_and_can_be_revoked(
    tmp_path: Path,
) -> None:
    database = tmp_path / "security.sqlite3"
    access = AccessControlService(database)
    user = access.bootstrap_administrator(
        username="admin",
        display_name="System Owner",
        email=None,
        password="Secure passphrase 123!",
    )
    sessions = SessionSecurityService(database)

    sessions.start(
        "session-one",
        user_id=user.user_id,
        username=user.username,
        authentication_method="local",
        client=_client("192.0.2.1"),
    )
    sessions.start(
        "session-two",
        user_id=user.user_id,
        username=user.username,
        authentication_method="oracle_basic",
        client=_client("198.51.100.2"),
    )

    assert sessions.validate_and_touch(
        "session-one", user_id=user.user_id, client=_client("192.0.2.1")
    )
    dashboard = sessions.dashboard(current_session_id="session-one")
    assert dashboard["summary"]["active_sessions"] == 2
    assert dashboard["summary"]["concurrent_accounts"] == 1
    assert dashboard["concurrent_accounts"][0]["ip_addresses"] == [
        "192.0.2.1",
        "198.51.100.2",
    ]

    second_key = sessions.session_key("session-two")
    sessions.revoke(second_key, actor_user_id=user.user_id)
    assert not sessions.validate_and_touch(
        "session-two", user_id=user.user_id, client=_client("198.51.100.2")
    )


def test_system_administrator_can_be_delegated_without_changing_business_role(
    tmp_path: Path,
) -> None:
    database = tmp_path / "security.sqlite3"
    access = AccessControlService(database)
    owner = access.bootstrap_administrator(
        username="admin",
        display_name="System Owner",
        email=None,
        password="Secure passphrase 123!",
    )
    user = access.create_user(
        username="security",
        display_name="Security Reviewer",
        email=None,
        password="Security passphrase 123!",
        roles=("VIEWER",),
        actor_user_id=owner.user_id,
    )

    updated = access.set_system_administrator(
        user.user_id,
        enabled=True,
        actor_user_id=owner.user_id,
    )
    assert {role.value for role in updated.roles} == {"VIEWER", "SYSTEM_ADMINISTRATOR"}
    assert access.roles()[-1].code.value == "VIEWER"
    access.set_system_administrator(
        owner.user_id,
        enabled=False,
        actor_user_id=owner.user_id,
    )
    with pytest.raises(AccessControlError, match="last active System Administrator"):
        access.set_system_administrator(
            user.user_id,
            enabled=False,
            actor_user_id=user.user_id,
        )

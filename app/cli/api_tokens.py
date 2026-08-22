"""Create and revoke scoped external-client API tokens."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from app.config.settings import PROJECT_ROOT, Settings
from app.infrastructure.database.migration import assert_schema_current
from app.models.access_control import Permission
from app.models.api_token import ApiTokenScope
from app.services.access_control_service import AccessControlService
from app.services.api_token_service import ApiTokenService
from app.utils.exceptions import ApiTokenError, EPMError


_DEFAULT_SCOPES = frozenset(
    {
        ApiTokenScope.PIPELINE_READ,
        ApiTokenScope.PIPELINE_RUN,
        ApiTokenScope.EXECUTION_READ,
    }
)

_SCOPE_PERMISSIONS = {
    ApiTokenScope.PIPELINE_READ: Permission.OPERATION_EXECUTE,
    ApiTokenScope.PIPELINE_RUN: Permission.OPERATION_EXECUTE,
    ApiTokenScope.EXECUTION_READ: Permission.HISTORY_VIEW,
}


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    try:
        settings = Settings.from_env()
        assert_schema_current(
            settings.database_target,
            project_root=PROJECT_ROOT,
        )
        access = AccessControlService(settings.database_target)
        tokens = ApiTokenService(
            settings.database_target,
            access_control=access,
        )
        if args.command == "create":
            return _create(args, access, tokens)
        if args.command == "list":
            return _list(args, access, tokens)
        if args.command == "revoke":
            record = tokens.revoke(args.token_id)
            print(
                f"Revoked token {record.token_id} "
                f"({record.name}, prefix {record.token_prefix})."
            )
            return 0
        parser.print_help()
        return 2
    except EPMError as exc:
        print(f"API token command failed: {exc}")
        return 1


def _create(args, access: AccessControlService, tokens: ApiTokenService) -> int:
    user = _user(access, args.username)
    scopes = _parse_scopes(args.scopes)
    missing_permissions = sorted(
        {
            _SCOPE_PERMISSIONS[scope].value
            for scope in scopes
            if not user.has_permission(_SCOPE_PERMISSIONS[scope])
        }
    )
    if missing_permissions:
        raise ApiTokenError(
            f"User '{user.username}' lacks permission(s): "
            + ", ".join(missing_permissions)
        )
    expires_at = datetime.now(UTC) + timedelta(days=args.expires_days)
    issued = tokens.create(
        user_id=user.user_id,
        name=args.name,
        scopes=scopes,
        expires_at=expires_at,
    )
    print("\nAPI token created successfully.")
    print(f"Token ID: {issued.record.token_id}")
    print(f"User: {user.username}")
    print(f"Expires: {expires_at.isoformat()}")
    print("Scopes: " + ", ".join(sorted(scope.value for scope in scopes)))
    print("\nCopy this token now. It will not be shown again:\n")
    print(issued.token)
    print(
        "\nKeep it secret. Use the revoke command immediately if it is "
        "shared or exposed."
    )
    return 0


def _list(args, access: AccessControlService, tokens: ApiTokenService) -> int:
    user = _user(access, args.username)
    records = tokens.list_for_user(user.user_id)
    if not records:
        print(f"No API tokens exist for '{user.username}'.")
        return 0
    print("ID  Prefix        State     Expires                    Name")
    for item in records:
        state = "REVOKED" if item.revoked_at else "ACTIVE"
        expiry = item.expires_at.isoformat() if item.expires_at else "Never"
        print(
            f"{item.token_id:<3} {item.token_prefix:<13} "
            f"{state:<9} {expiry:<26} {item.name}"
        )
    return 0


def _user(access: AccessControlService, username: str):
    requested = username.strip().casefold()
    user = next(
        (
            item
            for item in access.list_users()
            if item.username.casefold() == requested
        ),
        None,
    )
    if user is None:
        available = ", ".join(item.username for item in access.list_users())
        raise ApiTokenError(
            f"Platform user '{username}' was not found. Available: "
            f"{available or 'none'}"
        )
    return user


def _parse_scopes(value: str) -> frozenset[ApiTokenScope]:
    if not value.strip():
        return _DEFAULT_SCOPES
    return frozenset(
        ApiTokenScope(item.strip())
        for item in value.split(",")
        if item.strip()
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage revocable BISP EPM API tokens.",
    )
    subparsers = parser.add_subparsers(dest="command")
    create = subparsers.add_parser("create", help="Create a token.")
    create.add_argument("--username", required=True)
    create.add_argument("--name", default="Excel Pipeline Runner")
    create.add_argument("--expires-days", type=int, default=30, choices=range(1, 366))
    create.add_argument(
        "--scopes",
        default=",".join(sorted(scope.value for scope in _DEFAULT_SCOPES)),
    )
    list_command = subparsers.add_parser("list", help="List safe metadata.")
    list_command.add_argument("--username", required=True)
    revoke = subparsers.add_parser("revoke", help="Revoke a token permanently.")
    revoke.add_argument("--token-id", type=int, required=True)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())

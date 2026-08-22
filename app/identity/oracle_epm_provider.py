"""Read-only Oracle Cloud EPM Access Control identity adapter."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from app.config.settings import Settings
from app.models.identity import (
    ExternalAssignmentType,
    ExternalEntitlementSnapshot,
    ExternalEntitlementType,
    ExternalIdentitySnapshot,
    IdentityDirectorySnapshot,
    IdentityProviderDefinition,
    IdentityProviderType,
)
from app.models.oracle_artifact import OracleEnvironment
from app.utils.exceptions import APIRequestError, IdentitySynchronizationError


class OracleIdentityReportClient(Protocol):
    def get(self, endpoint: str, *, params=None) -> Any: ...


class OracleEPMIdentityProvider:
    """Translate Cloud EPM v2 access reports into the common identity model."""

    _USER_ROLE_ENDPOINT = (
        "interop/rest/security/v2/report/roleassignmentreport/user"
    )
    _USER_GROUP_ENDPOINT = "interop/rest/security/v2/report/usergroupreport"

    def __init__(
        self,
        settings: Settings,
        client: OracleIdentityReportClient,
    ) -> None:
        self._settings = settings
        self._client = client
        self._definition = self.definition_for(settings)

    @staticmethod
    def definition_for(settings: Settings) -> IdentityProviderDefinition:
        """Build deterministic, non-secret provider metadata."""
        environment = OracleEnvironment.from_settings(
            settings.epm_base_url,
            settings.application_name,
        )
        return IdentityProviderDefinition(
            code=f"oracle-epm-{environment.key[:20]}",
            provider_type=IdentityProviderType.ORACLE_CLOUD,
            display_name=f"Oracle EPM — {settings.application_name}",
            issuer_url=settings.oracle_identity_issuer_url,
            environment_key=environment.key,
            safe_configuration={
                "source": "EPM_ACCESS_CONTROL_V2",
                "application_name": settings.application_name,
                "base_url": settings.epm_base_url,
                "subject_strategy": "NORMALIZED_USER_LOGIN",
            },
        )

    @property
    def definition(self) -> IdentityProviderDefinition:
        return self._definition

    def fetch_snapshot(self) -> IdentityDirectorySnapshot:
        """Read user roles and groups without changing Oracle or platform data."""
        if self._settings.resolved_deployment_mode != "cloud":
            raise IdentitySynchronizationError(
                "Oracle identity synchronization requires an Oracle Cloud EPM "
                "environment. The current connection is on-premises."
            )
        role_payload = self._require_report(
            self._client.get(self._USER_ROLE_ENDPOINT),
            "user role assignment",
        )
        warnings: list[str] = []
        complete = True
        try:
            group_payload = self._require_report(
                self._client.get(self._USER_GROUP_ENDPOINT),
                "user group membership",
            )
        except APIRequestError as exc:
            if exc.status_code != 404:
                raise
            group_payload = {"details": []}
            complete = False
            warnings.append(
                "The connected Cloud EPM version did not expose the v2 user "
                "group report. Roles were retrieved, but group membership was not."
            )

        users: dict[str, dict[str, Any]] = {}
        self._merge_role_report(users, role_payload)
        self._merge_group_report(users, group_payload)
        identities = tuple(
            self._identity_snapshot(users[key]) for key in sorted(users)
        )
        return IdentityDirectorySnapshot(
            identities=identities,
            retrieved_at=datetime.now(UTC),
            complete=complete,
            details={
                "source": "Oracle Cloud EPM Access Control v2",
                "warnings": warnings,
                "role_report_records": len(role_payload.get("details") or []),
                "group_report_records": len(group_payload.get("details") or []),
            },
        )

    @staticmethod
    def _require_report(payload: Any, label: str) -> Mapping[str, Any]:
        if not isinstance(payload, Mapping):
            raise IdentitySynchronizationError(
                f"Oracle returned an invalid {label} report response."
            )
        try:
            status = int(payload.get("status", -1))
        except (TypeError, ValueError) as exc:
            raise IdentitySynchronizationError(
                f"Oracle returned an invalid {label} report status."
            ) from exc
        if status != 0:
            error = payload.get("error")
            message = ""
            if isinstance(error, Mapping):
                message = str(error.get("errormessage") or "").strip()
            raise IdentitySynchronizationError(
                message or f"Oracle could not generate the {label} report."
            )
        details = payload.get("details")
        if details is not None and not isinstance(details, list):
            raise IdentitySynchronizationError(
                f"Oracle returned invalid records in the {label} report."
            )
        return payload

    @classmethod
    def _merge_role_report(
        cls,
        users: dict[str, dict[str, Any]],
        payload: Mapping[str, Any],
    ) -> None:
        for raw_user in payload.get("details") or []:
            if not isinstance(raw_user, Mapping):
                continue
            user = cls._user_record(users, raw_user)
            for raw_role in raw_user.get("roles") or []:
                if not isinstance(raw_role, Mapping):
                    continue
                name = str(raw_role.get("rolename") or "").strip()
                if not name:
                    continue
                role_type = str(raw_role.get("roletype") or "").casefold()
                entitlement_type = (
                    ExternalEntitlementType.APPLICATION_ROLE
                    if role_type == "application"
                    else ExternalEntitlementType.GRANULAR_ROLE
                )
                granted_through = str(
                    raw_role.get("grantedthroughgroup") or ""
                ).strip()
                user["entitlements"].append(
                    ExternalEntitlementSnapshot(
                        external_key=name,
                        display_name=name,
                        entitlement_type=entitlement_type,
                        assignment_type=(
                            ExternalAssignmentType.INHERITED
                            if granted_through
                            else ExternalAssignmentType.DIRECT
                        ),
                        granted_through_group=granted_through or None,
                    )
                )

    @classmethod
    def _merge_group_report(
        cls,
        users: dict[str, dict[str, Any]],
        payload: Mapping[str, Any],
    ) -> None:
        for raw_user in payload.get("details") or []:
            if not isinstance(raw_user, Mapping):
                continue
            user = cls._user_record(users, raw_user)
            for raw_group in raw_user.get("groups") or []:
                if not isinstance(raw_group, Mapping):
                    continue
                name = str(raw_group.get("groupname") or "").strip()
                if not name:
                    continue
                direct = str(raw_group.get("direct") or "").casefold() == "yes"
                user["entitlements"].append(
                    ExternalEntitlementSnapshot(
                        external_key=name,
                        display_name=name,
                        entitlement_type=ExternalEntitlementType.GROUP,
                        assignment_type=(
                            ExternalAssignmentType.DIRECT
                            if direct
                            else ExternalAssignmentType.INHERITED
                        ),
                    )
                )

    @staticmethod
    def _user_record(
        users: dict[str, dict[str, Any]],
        raw_user: Mapping[str, Any],
    ) -> dict[str, Any]:
        username = str(raw_user.get("userlogin") or "").strip()
        if not username:
            raise IdentitySynchronizationError(
                "Oracle returned an identity without a user login."
            )
        key = username.casefold()
        first_name = str(raw_user.get("firstname") or "").strip()
        last_name = str(raw_user.get("lastname") or "").strip()
        display_name = " ".join(
            value for value in (first_name, last_name) if value
        ) or username
        record = users.setdefault(
            key,
            {
                "username": username,
                "display_name": display_name,
                "email": None,
                "entitlements": [],
            },
        )
        email = str(raw_user.get("email") or "").strip()
        if email:
            record["email"] = email
        if display_name != username:
            record["display_name"] = display_name
        return record

    @staticmethod
    def _identity_snapshot(record: Mapping[str, Any]) -> ExternalIdentitySnapshot:
        unique: dict[
            tuple[ExternalEntitlementType, str],
            ExternalEntitlementSnapshot,
        ] = {}
        for entitlement in record["entitlements"]:
            key = (
                entitlement.entitlement_type,
                entitlement.external_key.casefold(),
            )
            current = unique.get(key)
            if (
                current is None
                or current.assignment_type == ExternalAssignmentType.INHERITED
                and entitlement.assignment_type == ExternalAssignmentType.DIRECT
            ):
                unique[key] = entitlement
        username = str(record["username"])
        return ExternalIdentitySnapshot(
            subject=f"epm-login:{username.casefold()}",
            username=username,
            display_name=str(record["display_name"]),
            email=(str(record["email"]) if record.get("email") else None),
            entitlements=tuple(
                unique[key]
                for key in sorted(unique, key=lambda item: (item[0].value, item[1]))
            ),
        )

"""Read-only Oracle Cloud EPM Access Control identity adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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

    def post(self, endpoint: str, *, payload=None, params=None) -> Any: ...


class OracleEPMIdentityProvider:
    """Translate Cloud EPM Access Control data into the common identity model."""

    _USER_LIST_ENDPOINT = "interop/rest/security/v1/users/list"
    _AVAILABLE_ROLES_ENDPOINT = (
        "interop/rest/security/v2/role/getavailableroles"
    )
    _LEGACY_USER_ROLE_ENDPOINT = (
        "interop/rest/security/v2/report/roleassignmentreport/user"
    )
    _LEGACY_USER_GROUP_ENDPOINT = (
        "interop/rest/security/v2/report/usergroupreport"
    )
    _USER_LIST_OPTIONS = {
        "epmgroups": True,
        "idcsgroups": True,
        "applicationroles": True,
        "granularroles": True,
        "indirect": True,
    }

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
            display_name=f"Oracle EPM - {settings.application_name}",
            issuer_url=settings.oracle_identity_issuer_url,
            environment_key=environment.key,
            safe_configuration={
                "source": "EPM_ACCESS_CONTROL",
                "application_name": settings.application_name,
                "base_url": settings.epm_base_url,
                "subject_strategy": "NORMALIZED_USER_LOGIN",
            },
        )

    @property
    def definition(self) -> IdentityProviderDefinition:
        return self._definition

    def fetch_snapshot(self) -> IdentityDirectorySnapshot:
        """Read all visible users, groups, roles, and the available role catalog."""
        self._ensure_cloud()
        payload, complete, warnings, source = self._user_directory()
        identities = self._identities(payload)
        role_warning_count = len(warnings)
        available = self._available_roles(warnings)
        role_catalog_complete = len(warnings) == role_warning_count
        return IdentityDirectorySnapshot(
            identities=identities,
            retrieved_at=datetime.now(UTC),
            complete=complete and role_catalog_complete,
            available_entitlements=available,
            details={
                "source": source,
                "warnings": warnings,
                "user_records": len(payload.get("details") or []),
                "available_roles": len(available),
                "available_role_catalog_complete": role_catalog_complete,
            },
        )

    def fetch_identity_snapshot(self, username: str) -> IdentityDirectorySnapshot:
        """Read one authenticated user without treating other users as removed."""
        self._ensure_cloud()
        requested = str(username).strip()
        if not requested or len(requested) > 254:
            raise IdentitySynchronizationError(
                "A valid Oracle EPM user login is required."
            )
        payload, _, warnings, source = self._user_directory(requested)
        matches = tuple(
            identity
            for identity in self._identities(payload)
            if self._same_login(requested, identity.username)
        )
        if len(matches) != 1:
            raise IdentitySynchronizationError(
                "Oracle authenticated the user, but Access Control did not "
                "return one matching user profile. Ask an administrator to "
                "verify the integration account and user login format."
            )
        return IdentityDirectorySnapshot(
            identities=matches,
            retrieved_at=datetime.now(UTC),
            complete=False,
            details={
                "source": source,
                "lookup": "single_user",
                "warnings": warnings,
            },
        )

    def _user_directory(
        self,
        username: str | None = None,
    ) -> tuple[Mapping[str, Any], bool, list[str], str]:
        """Prefer enriched List Users and safely support earlier Cloud releases."""
        request_payload = dict(self._USER_LIST_OPTIONS)
        if username:
            request_payload["userlogin"] = username
        primary_failure: Exception | None = None
        try:
            payload = self._require_report(
                self._client.post(
                    self._USER_LIST_ENDPOINT,
                    payload=request_payload,
                ),
                "user directory",
            )
            details = payload.get("details") or []
            enriched = not details or any(
                isinstance(item, Mapping)
                and any(
                    key in item
                    for key in (
                        "epmgroups",
                        "idcsgroups",
                        "applicationroles",
                        "granularroles",
                    )
                )
                for item in details
            )
            if enriched:
                return (
                    payload,
                    True,
                    [],
                    "Oracle Cloud EPM Access Control - List Users",
                )
        except (APIRequestError, IdentitySynchronizationError) as exc:
            primary_failure = exc

        try:
            payload, complete, warnings = self._legacy_user_directory(username)
        except (APIRequestError, IdentitySynchronizationError):
            if primary_failure is not None:
                raise primary_failure
            raise
        warnings.insert(
            0,
            "The enriched List Users response was unavailable; compatible "
            "Access Control role and group reports were used.",
        )
        return (
            payload,
            complete,
            warnings,
            "Oracle Cloud EPM Access Control - compatibility reports",
        )

    def _legacy_user_directory(
        self,
        username: str | None,
    ) -> tuple[Mapping[str, Any], bool, list[str]]:
        """Merge role and group reports used before enriched List Users."""
        params = {"userlogin": username} if username else None
        roles = self._require_report(
            self._client.get(self._LEGACY_USER_ROLE_ENDPOINT, params=params),
            "user role assignment",
        )
        warnings: list[str] = []
        complete = True
        try:
            groups = self._require_report(
                self._client.get(self._LEGACY_USER_GROUP_ENDPOINT, params=params),
                "user group membership",
            )
        except (APIRequestError, IdentitySynchronizationError) as exc:
            groups = {"status": 0, "details": []}
            complete = False
            warnings.append(
                f"Group membership could not be retrieved from the "
                f"compatibility report: {exc}"
            )
        users: dict[str, dict[str, Any]] = {}
        for source in roles.get("details") or []:
            self._merge_legacy_user(users, source, include_roles=True)
        for source in groups.get("details") or []:
            self._merge_legacy_user(users, source, include_roles=False)
        return {"status": 0, "details": list(users.values())}, complete, warnings

    @staticmethod
    def _merge_legacy_user(
        users: dict[str, dict[str, Any]],
        source: object,
        *,
        include_roles: bool,
    ) -> None:
        if not isinstance(source, Mapping):
            return
        username = str(source.get("userlogin") or "").strip()
        if not username:
            return
        key = username.casefold()
        target = users.setdefault(
            key,
            {
                "userlogin": username,
                "firstname": source.get("firstname"),
                "lastname": source.get("lastname"),
                "email": source.get("email"),
                "roles": [],
                "groups": [],
            },
        )
        for field in ("firstname", "lastname", "email"):
            if not target.get(field) and source.get(field):
                target[field] = source.get(field)
        collection = "roles" if include_roles else "groups"
        values = source.get(collection)
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            target[collection].extend(values)

    def _available_roles(
        self,
        warnings: list[str],
    ) -> tuple[ExternalEntitlementSnapshot, ...]:
        discovered: dict[
            tuple[ExternalEntitlementType, str],
            ExternalEntitlementSnapshot,
        ] = {}
        for role_type, entitlement_type in (
            ("application", ExternalEntitlementType.APPLICATION_ROLE),
            ("granular", ExternalEntitlementType.GRANULAR_ROLE),
        ):
            try:
                payload = self._require_report(
                    self._client.get(
                        self._AVAILABLE_ROLES_ENDPOINT,
                        params={"type": role_type},
                    ),
                    f"available {role_type} roles",
                )
            except (APIRequestError, IdentitySynchronizationError) as exc:
                warnings.append(
                    f"Available {role_type} roles could not be retrieved: {exc}"
                )
                continue
            for raw in payload.get("details") or []:
                if not isinstance(raw, Mapping):
                    continue
                name = str(raw.get("name") or raw.get("rolename") or "").strip()
                if not name:
                    continue
                item = ExternalEntitlementSnapshot(
                    external_key=name,
                    display_name=name,
                    entitlement_type=entitlement_type,
                )
                discovered[(entitlement_type, name.casefold())] = item
        return tuple(
            discovered[key]
            for key in sorted(discovered, key=lambda item: (item[0].value, item[1]))
        )

    @classmethod
    def _identities(
        cls,
        payload: Mapping[str, Any],
    ) -> tuple[ExternalIdentitySnapshot, ...]:
        identities: list[ExternalIdentitySnapshot] = []
        for raw_user in payload.get("details") or []:
            if not isinstance(raw_user, Mapping):
                continue
            username = str(raw_user.get("userlogin") or "").strip()
            if not username:
                raise IdentitySynchronizationError(
                    "Oracle returned an identity without a user login."
                )
            first_name = str(raw_user.get("firstname") or "").strip()
            last_name = str(raw_user.get("lastname") or "").strip()
            display_name = " ".join(
                item for item in (first_name, last_name) if item
            ) or username
            entitlements: list[ExternalEntitlementSnapshot] = []
            cls._append_roles(
                entitlements,
                raw_user.get("applicationroles"),
                ExternalEntitlementType.APPLICATION_ROLE,
            )
            cls._append_roles(
                entitlements,
                raw_user.get("granularroles"),
                ExternalEntitlementType.GRANULAR_ROLE,
            )
            cls._append_groups(entitlements, raw_user.get("epmgroups"))
            cls._append_groups(entitlements, raw_user.get("idcsgroups"))
            cls._append_compatibility_assignments(entitlements, raw_user)
            unique = cls._unique_entitlements(entitlements)
            identities.append(
                ExternalIdentitySnapshot(
                    subject=f"epm-login:{username.casefold()}",
                    username=username,
                    display_name=display_name,
                    email=(
                        str(raw_user.get("email")).strip()
                        if raw_user.get("email")
                        else None
                    ),
                    entitlements=unique,
                )
            )
        return tuple(
            sorted(identities, key=lambda item: item.username.casefold())
        )

    @staticmethod
    def _append_roles(
        target: list[ExternalEntitlementSnapshot],
        raw_roles: object,
        entitlement_type: ExternalEntitlementType,
    ) -> None:
        if not isinstance(raw_roles, Sequence) or isinstance(raw_roles, (str, bytes)):
            return
        for raw_role in raw_roles:
            if not isinstance(raw_role, Mapping):
                continue
            name = str(raw_role.get("rolename") or raw_role.get("name") or "").strip()
            if not name:
                continue
            granted_through = str(
                raw_role.get("grantedthroughgroup") or ""
            ).strip()
            raw_direct = raw_role.get("direct")
            direct = (
                not granted_through
                if raw_direct is None
                else str(raw_direct).strip().casefold() == "yes"
            )
            target.append(
                ExternalEntitlementSnapshot(
                    external_key=name,
                    display_name=name,
                    entitlement_type=entitlement_type,
                    assignment_type=(
                        ExternalAssignmentType.DIRECT
                        if direct
                        else ExternalAssignmentType.INHERITED
                    ),
                    granted_through_group=granted_through or None,
                )
            )

    @staticmethod
    def _append_groups(
        target: list[ExternalEntitlementSnapshot],
        raw_groups: object,
    ) -> None:
        if not isinstance(raw_groups, Sequence) or isinstance(raw_groups, (str, bytes)):
            return
        for raw_group in raw_groups:
            if not isinstance(raw_group, Mapping):
                continue
            name = str(raw_group.get("groupname") or raw_group.get("name") or "").strip()
            if not name:
                continue
            direct = str(raw_group.get("direct") or "yes").strip().casefold() == "yes"
            target.append(
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

    @classmethod
    def _append_compatibility_assignments(
        cls,
        target: list[ExternalEntitlementSnapshot],
        raw_user: Mapping[str, Any],
    ) -> None:
        """Accept older response names used by earlier EPM monthly updates."""
        raw_roles = raw_user.get("roles")
        if isinstance(raw_roles, Sequence) and not isinstance(raw_roles, (str, bytes)):
            for raw_role in raw_roles:
                if not isinstance(raw_role, Mapping):
                    continue
                role_type = str(raw_role.get("roletype") or "").casefold()
                cls._append_roles(
                    target,
                    (raw_role,),
                    (
                        ExternalEntitlementType.GRANULAR_ROLE
                        if role_type == "granular"
                        else ExternalEntitlementType.APPLICATION_ROLE
                    ),
                )
        member_of = raw_user.get("memberof")
        if isinstance(member_of, Mapping):
            cls._append_groups(target, member_of.get("groups"))
        cls._append_groups(target, raw_user.get("groups"))

    @staticmethod
    def _unique_entitlements(
        entitlements: list[ExternalEntitlementSnapshot],
    ) -> tuple[ExternalEntitlementSnapshot, ...]:
        unique: dict[
            tuple[ExternalEntitlementType, str],
            ExternalEntitlementSnapshot,
        ] = {}
        for item in entitlements:
            key = (item.entitlement_type, item.external_key.casefold())
            current = unique.get(key)
            if (
                current is None
                or current.assignment_type == ExternalAssignmentType.INHERITED
                and item.assignment_type == ExternalAssignmentType.DIRECT
            ):
                unique[key] = item
        return tuple(
            unique[key]
            for key in sorted(unique, key=lambda item: (item[0].value, item[1]))
        )

    @staticmethod
    def _same_login(requested: str, returned: str) -> bool:
        left = requested.strip().casefold()
        right = returned.strip().casefold()
        if left == right:
            return True
        return "." in left and left.split(".", 1)[1] == right

    def _ensure_cloud(self) -> None:
        if self._settings.resolved_deployment_mode != "cloud":
            raise IdentitySynchronizationError(
                "Oracle identity synchronization requires an Oracle Cloud EPM "
                "environment. The current connection is on-premises."
            )

    @staticmethod
    def _require_report(payload: Any, label: str) -> Mapping[str, Any]:
        if not isinstance(payload, Mapping):
            raise IdentitySynchronizationError(
                f"Oracle returned an invalid {label} response."
            )
        try:
            status = int(payload.get("status", -1))
        except (TypeError, ValueError) as exc:
            raise IdentitySynchronizationError(
                f"Oracle returned an invalid {label} status."
            ) from exc
        if status != 0:
            error = payload.get("error")
            message = ""
            if isinstance(error, Mapping):
                message = str(error.get("errormessage") or "").strip()
            raise IdentitySynchronizationError(
                message or f"Oracle could not retrieve {label}."
            )
        details = payload.get("details")
        if details is not None and (
            not isinstance(details, Sequence)
            or isinstance(details, (str, bytes))
        ):
            raise IdentitySynchronizationError(
                f"Oracle returned invalid records in the {label} response."
            )
        return payload

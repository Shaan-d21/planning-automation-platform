"""Oracle EPM credential login with just-in-time linked platform profiles."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from app.clients.epm_client import EPMClient
from app.config.settings import Settings
from app.identity.oracle_epm_provider import OracleEPMIdentityProvider
from app.services.access_control_service import AccessControlService
from app.services.federated_provisioning_service import (
    FederatedProvisioningService,
)
from app.services.identity_directory_service import IdentityDirectoryService
from app.utils.exceptions import (
    AuthenticationError,
    IdentitySynchronizationError,
    OracleCredentialAuthenticationError,
)


class OraclePasswordAuthenticationService:
    """Validate Oracle credentials and resolve approved platform access."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[Settings], EPMClient] | None = None,
    ) -> None:
        self._settings = settings
        self._access = AccessControlService(settings.database_target)
        self._directory = IdentityDirectoryService(settings.database_target)
        self._provisioning = FederatedProvisioningService(
            settings.database_target
        )
        self._client_factory = client_factory or EPMClient

    def authenticate(
        self,
        username: str,
        password: str,
        *,
        ip_address: str | None = None,
    ):
        """Authenticate once with Oracle, then refresh and map that identity."""
        normalized_username = str(username).strip()
        if not normalized_username or len(normalized_username) > 254:
            raise OracleCredentialAuthenticationError(
                "Enter a valid Oracle EPM username.",
                credentials_valid=False,
            )
        if not password or len(password) > 512:
            raise OracleCredentialAuthenticationError(
                "Enter a valid Oracle EPM password.",
                credentials_valid=False,
            )
        if not self._settings.oracle_password_login_ready:
            raise OracleCredentialAuthenticationError(
                "Oracle credential sign-in is not enabled for this environment.",
                credentials_valid=False,
            )
        if self._access.external_login_rate_limited(
            normalized_username,
            ip_address=ip_address,
        ):
            self._access.record_external_login_throttled(
                normalized_username,
                ip_address=ip_address,
            )
            raise OracleCredentialAuthenticationError(
                "Too many unsuccessful Oracle sign-in attempts. Wait 15 "
                "minutes before trying again.",
                credentials_valid=False,
            )

        login_settings = replace(
            self._settings,
            epm_username=normalized_username,
            epm_password=password,
        )
        try:
            with self._client_factory(login_settings) as user_client:
                user_client.authenticate()
        except AuthenticationError as exc:
            self._access.record_external_login_failure(
                normalized_username,
                reason="INVALID_ORACLE_CREDENTIALS",
                credential_failure=True,
                ip_address=ip_address,
            )
            raise OracleCredentialAuthenticationError(
                "The Oracle EPM username or password is incorrect.",
                credentials_valid=False,
            ) from exc

        provider_definition = OracleEPMIdentityProvider.definition_for(
            self._settings
        )
        try:
            with self._client_factory(self._settings) as service_client:
                provider = OracleEPMIdentityProvider(
                    self._settings,
                    service_client,
                )
                snapshot = provider.fetch_identity_snapshot(
                    normalized_username
                )
            self._directory.register_provider(provider_definition)
            self._directory.synchronize(
                provider_definition.code,
                snapshot,
                initiated_by_user_id=None,
            )
            identity = snapshot.identities[0]
            user_id = self._provisioning.provision_identity(
                provider_definition.code,
                identity.subject,
                actor_user_id=None,
            )
        except IdentitySynchronizationError as exc:
            self._access.record_external_login_failure(
                normalized_username,
                reason="VALID_ORACLE_USER_WITHOUT_PLATFORM_ACCESS",
                ip_address=ip_address,
            )
            raise OracleCredentialAuthenticationError(
                str(exc),
                credentials_valid=True,
            ) from exc

        return self._access.record_external_login(
            user_id,
            provider_code=provider_definition.code,
            authentication_method="oracle_basic",
            ip_address=ip_address,
        )

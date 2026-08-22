"""Oracle OCI IAM/IDCS OpenID Connect authorization-code client."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from authlib.integrations.starlette_client import OAuth
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.config.settings import Settings
from app.utils.exceptions import ConfigurationError, FederatedAuthenticationError


@dataclass(frozen=True, slots=True)
class OracleOIDCClaims:
    """Minimal verified claims allowed across the authentication boundary."""

    subject: str
    username: str
    display_name: str | None = None
    email: str | None = None


class OracleOIDCClient:
    """Perform OIDC discovery, PKCE, token validation, and UserInfo checks."""

    _CLIENT_NAME = "oracle_identity"

    def __init__(self, settings: Settings) -> None:
        if not settings.federated_identity_ready:
            raise ConfigurationError(
                "Oracle federated sign-in requires the identity issuer URL "
                "and client ID."
            )
        discovery_url = settings.oracle_identity_discovery_url
        assert discovery_url is not None
        self._oauth = OAuth()
        self._oauth.register(
            name=self._CLIENT_NAME,
            client_id=settings.oracle_identity_client_id,
            client_secret=settings.oracle_identity_client_secret,
            server_metadata_url=discovery_url,
            client_kwargs={
                "scope": "openid profile email",
                "code_challenge_method": "S256",
                "token_endpoint_auth_method": (
                    "client_secret_basic"
                    if settings.oracle_identity_client_secret
                    else "none"
                ),
            },
        )

    async def begin(
        self,
        request: Request,
        redirect_uri: str,
    ) -> RedirectResponse:
        """Start Authorization Code flow with nonce, state, and PKCE."""
        return await self._client().authorize_redirect(request, redirect_uri)

    async def complete(self, request: Request) -> OracleOIDCClaims:
        """Exchange the code and return only fully validated identity claims."""
        client = self._client()
        token = await client.authorize_access_token(request)
        id_claims = token.get("userinfo")
        if not isinstance(id_claims, Mapping):
            raise FederatedAuthenticationError(
                "Oracle did not return a validated identity token."
            )
        userinfo = await client.userinfo(token=token)
        if not isinstance(userinfo, Mapping):
            raise FederatedAuthenticationError(
                "Oracle did not return a valid user profile."
            )
        id_subject = self._required_claim(id_claims, "sub", 255)
        userinfo_subject = self._required_claim(userinfo, "sub", 255)
        if id_subject != userinfo_subject:
            raise FederatedAuthenticationError(
                "Oracle identity-token and UserInfo subjects did not match."
            )
        return OracleOIDCClaims(
            subject=id_subject,
            username=self._required_claim(
                userinfo,
                "preferred_username",
                254,
            ),
            display_name=self._optional_claim(userinfo, "name", 200),
            email=self._optional_claim(userinfo, "email", 254),
        )

    def _client(self):
        client = self._oauth.create_client(self._CLIENT_NAME)
        if client is None:
            raise ConfigurationError("Oracle OIDC client is not registered.")
        return client

    @staticmethod
    def _required_claim(
        claims: Mapping[str, Any],
        name: str,
        maximum: int,
    ) -> str:
        value = str(claims.get(name) or "").strip()
        if not value or len(value) > maximum:
            raise FederatedAuthenticationError(
                f"Oracle did not return a valid '{name}' identity claim."
            )
        return value

    @staticmethod
    def _optional_claim(
        claims: Mapping[str, Any],
        name: str,
        maximum: int,
    ) -> str | None:
        value = str(claims.get(name) or "").strip()
        return value[:maximum] if value else None

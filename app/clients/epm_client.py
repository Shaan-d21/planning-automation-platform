"""Reusable HTTP client for Oracle EPM Planning REST APIs."""

from __future__ import annotations

import logging
import socket
from collections.abc import Mapping
from typing import Any, BinaryIO
from urllib.parse import urlparse

import requests
from requests import Response, Session
from requests.auth import HTTPBasicAuth

from app.config.settings import Settings
from app.utils.exceptions import (
    APIRequestError,
    AuthenticationError,
    EPMConnectionError,
)


class EPMClient:
    """Manage authenticated HTTP communication with Oracle EPM Planning.

    Oracle Planning REST APIs use HTTP Basic Authentication on each request.
    The client retains a single ``requests.Session`` so authentication,
    connection pooling, headers, and future shared behavior are centralized.
    """

    _PLANNING_API_VERSION = "v3"

    def __init__(
        self,
        settings: Settings,
        *,
        session: Session | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the EPM client from validated application settings."""
        self._settings = settings
        self._session = session or requests.Session()
        self._logger = logger or logging.getLogger(__name__)
        self._authenticated = False

        self._session.auth = HTTPBasicAuth(
            settings.epm_username,
            settings.require_rest_password(),
        )
        self._session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "oracle-planning-automation/0.1.0",
            }
        )

    @property
    def base_url(self) -> str:
        """Return the normalized Oracle EPM environment URL."""
        return self._settings.epm_base_url

    @property
    def application_name(self) -> str:
        """Return the configured Planning application name."""
        return self._settings.application_name

    @property
    def is_authenticated(self) -> bool:
        """Indicate whether authentication was verified successfully."""
        return self._authenticated

    @property
    def deployment_mode(self) -> str:
        """Return the configured or inferred Oracle deployment mode."""
        return self._settings.resolved_deployment_mode

    @property
    def is_cloud_environment(self) -> bool:
        """Return whether Cloud-first REST behavior should be used."""
        return self.deployment_mode == "cloud"

    def authenticate(self) -> Mapping[str, Any]:
        """Verify credentials by retrieving Planning API version information.

        Returns:
            The API-version response returned by Oracle Planning.

        Raises:
            AuthenticationError: If Oracle rejects the supplied credentials.
            APIRequestError: If verification fails for another API reason.
            EPMConnectionError: If the Oracle EPM environment is unreachable.
        """
        self._logger.info(
            "Authentication started for Planning application '%s'.",
            self.application_name,
        )

        try:
            version_info = self.get_api_version_information()
        except (
            AuthenticationError,
            APIRequestError,
            EPMConnectionError,
        ) as exc:
            self._authenticated = False
            self._logger.error(
                "Authentication failed for Planning application '%s': %s",
                self.application_name,
                exc,
            )
            self._logger.debug(
                "Authentication failure details.",
                exc_info=True,
            )
            raise

        self._authenticated = True
        self._logger.info(
            "Authentication successful for Planning application '%s'.",
            self.application_name,
        )
        return version_info

    def get_api_version_information(self) -> Mapping[str, Any]:
        """Retrieve Planning API information to verify authenticated access."""
        result = self.get(self.planning_api_root)

        if not isinstance(result, Mapping):
            raise APIRequestError(
                "Oracle Planning returned an unexpected API version response."
            )
        return result

    def get(
        self,
        endpoint: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        """Send a GET request to a relative Oracle EPM endpoint."""
        return self._request("GET", endpoint, params=params)

    def post(
        self,
        endpoint: str,
        *,
        payload: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        """Send a POST request to a relative Oracle EPM endpoint."""
        return self._request(
            "POST",
            endpoint,
            params=params,
            json=payload,
        )

    def post_binary(
        self,
        endpoint: str,
        content: BinaryIO,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        """Stream binary content in a POST request to an EPM endpoint."""
        return self._request(
            "POST",
            endpoint,
            params=params,
            data=content,
            headers={"Content-Type": "application/octet-stream"},
        )

    def delete(
        self,
        endpoint: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        """Send a DELETE request to a relative Oracle EPM endpoint."""
        return self._request("DELETE", endpoint, params=params)

    def close(self) -> None:
        """Release network resources held by the underlying session."""
        self._session.close()

    def __enter__(self) -> EPMClient:
        """Return this client for use as a context manager."""
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        """Close the session when leaving a context manager."""
        self.close()

    @property
    def planning_api_root(self) -> str:
        """Return the Planning REST root relative to the configured base URL."""
        if self.base_url.lower().endswith("/hyperionplanning"):
            return f"rest/{self._PLANNING_API_VERSION}"
        return f"HyperionPlanning/rest/{self._PLANNING_API_VERSION}"

    def _request(self, method: str, endpoint: str, **kwargs: Any) -> Any:
        """Send an HTTP request and apply common transport/response handling."""
        url = self._build_url(endpoint)
        self._logger.info("HTTP request: %s %s", method, url)

        try:
            response = self._session.request(
                method=method,
                url=url,
                timeout=self._settings.request_timeout,
                verify=self._settings.verify_ssl,
                **kwargs,
            )
        except requests.exceptions.Timeout as exc:
            message = (
                f"Request to Oracle EPM timed out after "
                f"{self._settings.request_timeout:g} seconds."
            )
            self._logger.error(message)
            raise EPMConnectionError(message) from exc
        except requests.exceptions.ConnectionError as exc:
            if self._is_name_resolution_error(exc):
                hostname = urlparse(self.base_url).hostname or self.base_url
                message = (
                    f"DNS resolution failed for Oracle EPM host "
                    f"'{hostname}'. Verify EPM_BASE_URL and check the active "
                    f"VPN, corporate DNS, or network DNS configuration."
                )
            else:
                message = "Unable to connect to the Oracle EPM environment."
            self._logger.error(message)
            raise EPMConnectionError(message) from exc
        except requests.exceptions.RequestException as exc:
            message = f"Oracle EPM request failed: {exc}"
            self._logger.error(message)
            raise APIRequestError(message) from exc

        self._logger.info(
            "HTTP response status: %s for %s %s",
            response.status_code,
            method,
            url,
        )
        self._raise_for_error(response)
        return self._deserialize_response(response)

    def _build_url(self, endpoint: str) -> str:
        """Build a URL while preventing credentials from reaching another host."""
        if not endpoint or endpoint.startswith(("http://", "https://", "//")):
            raise ValueError("Endpoint must be a non-empty relative path.")
        base_url = self.base_url
        endpoint_root = endpoint.lstrip("/").lower()
        if (
            endpoint_root.startswith(("interop/", "aif/"))
            and base_url.lower().endswith("/hyperionplanning")
        ):
            base_url = base_url[: -len("/HyperionPlanning")]
        return f"{base_url}/{endpoint.lstrip('/')}"

    @staticmethod
    def _is_name_resolution_error(error: BaseException) -> bool:
        """Return whether an exception chain represents a DNS lookup failure."""
        pending: list[BaseException] = [error]
        visited: set[int] = set()

        while pending:
            current = pending.pop()
            if id(current) in visited:
                continue
            visited.add(id(current))

            if (
                isinstance(current, socket.gaierror)
                or type(current).__name__ == "NameResolutionError"
                or "getaddrinfo failed" in str(current).lower()
            ):
                return True

            if current.__cause__ is not None:
                pending.append(current.__cause__)
            if current.__context__ is not None:
                pending.append(current.__context__)
            pending.extend(
                argument
                for argument in current.args
                if isinstance(argument, BaseException)
            )

        return False

    @staticmethod
    def _deserialize_response(response: Response) -> Any:
        """Deserialize JSON responses and preserve text responses."""
        if response.status_code == requests.codes.no_content or not response.content:
            return None

        content_type = response.headers.get("Content-Type", "").lower()
        if "json" in content_type:
            try:
                return response.json()
            except requests.exceptions.JSONDecodeError as exc:
                raise APIRequestError(
                    "Oracle EPM returned malformed JSON."
                ) from exc
        return response.text

    @staticmethod
    def _raise_for_error(response: Response) -> None:
        """Translate HTTP error responses into framework exceptions."""
        if response.status_code in (
            requests.codes.unauthorized,
            requests.codes.forbidden,
        ):
            raise AuthenticationError(
                "Oracle Planning authentication failed. Verify the username, "
                "password, identity domain requirements, and user access.",
                status_code=response.status_code,
            )

        if response.ok:
            return

        detail = EPMClient._response_error_detail(response)
        raise APIRequestError(
            f"Oracle EPM API returned HTTP {response.status_code}: {detail}",
            status_code=response.status_code,
        )

    @staticmethod
    def _response_error_detail(response: Response) -> str:
        """Extract a bounded, useful error detail without exposing credentials."""
        try:
            body = response.json()
        except requests.exceptions.JSONDecodeError:
            body = response.text

        if isinstance(body, Mapping):
            detail = (
                body.get("details")
                or body.get("detail")
                or body.get("message")
                or body.get("error")
                or str(body)
            )
        else:
            detail = str(body)

        normalized_detail = " ".join(detail.split())
        return normalized_detail[:500] or "No error details were returned."

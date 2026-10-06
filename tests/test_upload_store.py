"""Tests for session- and application-scoped temporary uploads."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.utils.exceptions import ConfigurationError
from app.web.uploads import ProcessUploadStore


class _UploadRequest:
    def __init__(self, content: bytes) -> None:
        self._content = content

    async def stream(self):
        yield self._content


def _save(
    store: ProcessUploadStore,
    *,
    owner: str,
    application_id: int,
    filename: str = "forecast.csv",
):
    return asyncio.run(
        store.save(
            _UploadRequest(b"Account,Jan\nRevenue,100\n"),
            owner=owner,
            application_id=application_id,
            filename=filename,
        )
    )


def test_upload_token_requires_its_session_and_application(
    tmp_path: Path,
) -> None:
    store = ProcessUploadStore(tmp_path / "uploads")
    receipt = _save(store, owner="session-a", application_id=11)

    resolved = store.resolve(
        receipt.token,
        owner="session-a",
        application_id=11,
    )

    assert resolved.name == "forecast.csv"
    with pytest.raises(ConfigurationError, match="unavailable or expired"):
        store.resolve(
            receipt.token,
            owner="session-a",
            application_id=12,
        )
    with pytest.raises(ConfigurationError, match="unavailable or expired"):
        store.resolve(
            receipt.token,
            owner="session-b",
            application_id=11,
        )


def test_upload_cleanup_cannot_delete_another_application_file(
    tmp_path: Path,
) -> None:
    store = ProcessUploadStore(tmp_path / "uploads")
    receipt = _save(store, owner="session-a", application_id=11)

    store.delete_many(
        (receipt.token,),
        owner="session-a",
        application_id=12,
    )
    assert store.resolve(
        receipt.token,
        owner="session-a",
        application_id=11,
    ).is_file()

    store.delete_many(
        (receipt.token,),
        owner="session-a",
        application_id=11,
    )
    with pytest.raises(ConfigurationError, match="unavailable or expired"):
        store.resolve(
            receipt.token,
            owner="session-a",
            application_id=11,
        )


def test_logout_cleanup_removes_session_uploads_across_applications(
    tmp_path: Path,
) -> None:
    store = ProcessUploadStore(tmp_path / "uploads")
    first = _save(store, owner="session-a", application_id=11)
    second = _save(
        store,
        owner="session-a",
        application_id=12,
        filename="metadata.csv",
    )

    store.delete_owner("session-a")

    for receipt, application_id in ((first, 11), (second, 12)):
        with pytest.raises(ConfigurationError, match="unavailable or expired"):
            store.resolve(
                receipt.token,
                owner="session-a",
                application_id=application_id,
            )

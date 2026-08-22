"""Session-scoped temporary upload storage for Planning process inputs."""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from uuid import uuid4

from fastapi import Request

from app.utils.exceptions import ConfigurationError


@dataclass(frozen=True, slots=True)
class UploadReceipt:
    token: str
    filename: str
    size: int


@dataclass(frozen=True, slots=True)
class _StoredUpload:
    token: str
    owner: str
    path: Path
    size: int
    created_at: float


class ProcessUploadStore:
    """Store bounded uploads outside the Oracle Inbox until approval."""

    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int = 100 * 1024 * 1024,
        max_age_seconds: float = 8 * 60 * 60,
    ) -> None:
        self._root = Path(root)
        self._max_bytes = max_bytes
        self._max_age_seconds = max_age_seconds
        self._lock = Lock()
        self._uploads: dict[str, _StoredUpload] = {}

    async def save(
        self,
        request: Request,
        *,
        owner: str,
        filename: str,
    ) -> UploadReceipt:
        """Stream one browser upload to a session-owned temporary file."""
        self._purge_expired()
        safe_name = self._safe_name(filename)
        token = uuid4().hex
        directory = self._root / token
        directory.mkdir(parents=True, exist_ok=False)
        path = directory / safe_name
        size = 0
        try:
            with path.open("wb") as stream:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > self._max_bytes:
                        raise ConfigurationError(
                            "Uploaded file exceeds the 100 MB limit."
                        )
                    stream.write(chunk)
            if size == 0:
                raise ConfigurationError("Uploaded file is empty.")
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        stored = _StoredUpload(
            token=token,
            owner=owner,
            path=path,
            size=size,
            created_at=time.monotonic(),
        )
        with self._lock:
            self._uploads[token] = stored
        return UploadReceipt(
            token=token,
            filename=safe_name,
            size=size,
        )

    def resolve(self, token: str, *, owner: str) -> Path:
        """Resolve an opaque token only for its creating session."""
        self._purge_expired()
        with self._lock:
            upload = self._uploads.get(str(token).strip())
        if (
            upload is None
            or upload.owner != owner
            or not upload.path.is_file()
        ):
            raise ConfigurationError(
                "The selected temporary upload is unavailable or expired."
            )
        return upload.path

    def delete_many(self, tokens: tuple[str, ...]) -> None:
        """Remove temporary files after their process reaches a terminal state."""
        for token in tokens:
            with self._lock:
                upload = self._uploads.pop(token, None)
            if upload is not None:
                shutil.rmtree(upload.path.parent, ignore_errors=True)

    def delete_owner(self, owner: str) -> None:
        """Remove all pending uploads owned by one browser session."""
        with self._lock:
            uploads = [
                upload
                for upload in self._uploads.values()
                if upload.owner == owner
            ]
            for upload in uploads:
                self._uploads.pop(upload.token, None)
        for upload in uploads:
            shutil.rmtree(upload.path.parent, ignore_errors=True)

    def _purge_expired(self) -> None:
        cutoff = time.monotonic() - self._max_age_seconds
        with self._lock:
            expired = [
                upload
                for upload in self._uploads.values()
                if upload.created_at < cutoff
            ]
            for upload in expired:
                self._uploads.pop(upload.token, None)
        for upload in expired:
            shutil.rmtree(upload.path.parent, ignore_errors=True)

    @staticmethod
    def _safe_name(filename: str) -> str:
        normalized = str(filename).strip().replace("\\", "/")
        safe_name = normalized.rsplit("/", 1)[-1]
        if not safe_name or safe_name in {".", ".."}:
            raise ConfigurationError("A valid upload filename is required.")
        return safe_name

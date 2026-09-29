"""Persistent browser sessions and authentication activity reporting."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, desc, func, insert, select, update

from app.infrastructure.database.engine import DatabaseTarget, database_for, utc_datetime
from app.infrastructure.database.schema import (
    authentication_events,
    platform_sessions,
    platform_users,
)
from app.utils.exceptions import AccessControlError


SESSION_TTL = timedelta(hours=8)
SESSION_TOUCH_INTERVAL = timedelta(minutes=2)


@dataclass(frozen=True, slots=True)
class ClientContext:
    """Trusted, sanitized request metadata retained for security review."""

    ip_address: str | None
    country_code: str | None
    user_agent: str | None
    cloudflare_ray: str | None


class SessionSecurityService:
    """Create, validate, revoke, and report durable browser sessions."""

    def __init__(self, database_target: DatabaseTarget) -> None:
        self._database = database_for(database_target)

    @staticmethod
    def session_key(raw_session_id: str) -> str:
        return hashlib.sha256(raw_session_id.encode("utf-8")).hexdigest()

    def start(
        self,
        raw_session_id: str,
        *,
        user_id: int,
        username: str,
        authentication_method: str,
        client: ClientContext,
    ) -> None:
        now = datetime.now(UTC)
        key = self.session_key(raw_session_id)
        with self._database.begin() as connection:
            connection.execute(
                insert(platform_sessions).values(
                    session_id_hash=key,
                    user_id=user_id,
                    authentication_method=str(authentication_method)[:32],
                    login_ip=client.ip_address,
                    current_ip=client.ip_address,
                    country_code=client.country_code,
                    user_agent=client.user_agent,
                    cloudflare_ray=client.cloudflare_ray,
                    started_at=now,
                    last_seen_at=now,
                    expires_at=now + SESSION_TTL,
                )
            )
            self._event(
                connection,
                "SESSION_STARTED",
                username,
                user_id,
                True,
                client.ip_address,
                {
                    "session_key": key,
                    "authentication_method": authentication_method,
                    "country_code": client.country_code,
                    "user_agent": client.user_agent,
                    "cloudflare_ray": client.cloudflare_ray,
                },
            )

    def validate_and_touch(
        self,
        raw_session_id: str,
        *,
        user_id: int,
        client: ClientContext,
    ) -> bool:
        key = self.session_key(raw_session_id)
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            row = connection.execute(
                select(platform_sessions).where(
                    platform_sessions.c.session_id_hash == key,
                    platform_sessions.c.user_id == user_id,
                )
            ).mappings().one_or_none()
            if row is None:
                return False
            if (
                row["ended_at"] is not None
                or row["revoked_at"] is not None
                or utc_datetime(row["expires_at"]) <= now
            ):
                return False
            last_seen = utc_datetime(row["last_seen_at"])
            if (
                now - last_seen >= SESSION_TOUCH_INTERVAL
                or row["current_ip"] != client.ip_address
            ):
                connection.execute(
                    update(platform_sessions)
                    .where(platform_sessions.c.session_id_hash == key)
                    .values(
                        current_ip=client.ip_address,
                        country_code=client.country_code or row["country_code"],
                        cloudflare_ray=client.cloudflare_ray,
                        last_seen_at=now,
                    )
                )
            return True

    def end(self, raw_session_id: str, *, username: str, user_id: int) -> None:
        key = self.session_key(raw_session_id)
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            result = connection.execute(
                update(platform_sessions)
                .where(
                    platform_sessions.c.session_id_hash == key,
                    platform_sessions.c.ended_at.is_(None),
                )
                .values(ended_at=now, last_seen_at=now)
            )
            if result.rowcount:
                self._event(
                    connection,
                    "SESSION_ENDED",
                    username,
                    user_id,
                    True,
                    None,
                    {"session_key": key},
                )

    def revoke(
        self,
        session_key: str,
        *,
        actor_user_id: int,
        reason: str = "Revoked by System Administrator",
    ) -> None:
        normalized = str(session_key).strip().casefold()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise AccessControlError("The selected session is invalid.")
        now = datetime.now(UTC)
        with self._database.begin() as connection:
            row = connection.execute(
                select(
                    platform_sessions.c.user_id,
                    platform_sessions.c.current_ip,
                    platform_users.c.username,
                )
                .join(platform_users, platform_users.c.user_id == platform_sessions.c.user_id)
                .where(platform_sessions.c.session_id_hash == normalized)
            ).one_or_none()
            if row is None:
                raise AccessControlError("The selected session was not found.")
            connection.execute(
                update(platform_sessions)
                .where(platform_sessions.c.session_id_hash == normalized)
                .values(
                    revoked_at=now,
                    revoked_by_user_id=actor_user_id,
                    revoke_reason=str(reason).strip()[:255],
                )
            )
            self._event(
                connection,
                "SESSION_REVOKED",
                str(row.username),
                actor_user_id,
                True,
                row.current_ip,
                {"session_key": normalized, "target_user_id": int(row.user_id)},
            )

    def dashboard(self, *, current_session_id: str | None = None, limit: int = 100) -> dict[str, object]:
        now = datetime.now(UTC)
        recent_cutoff = now - timedelta(hours=24)
        current_key = self.session_key(current_session_id) if current_session_id else None
        active_clause = and_(
            platform_sessions.c.ended_at.is_(None),
            platform_sessions.c.revoked_at.is_(None),
            platform_sessions.c.expires_at > now,
        )
        with self._database.connect() as connection:
            session_rows = connection.execute(
                select(
                    platform_sessions,
                    platform_users.c.username,
                    platform_users.c.display_name,
                )
                .join(platform_users, platform_users.c.user_id == platform_sessions.c.user_id)
                .order_by(desc(platform_sessions.c.last_seen_at))
                .limit(max(1, min(int(limit), 250)))
            ).mappings().all()
            event_rows = connection.execute(
                select(authentication_events)
                .order_by(desc(authentication_events.c.occurred_at))
                .limit(max(1, min(int(limit), 250)))
            ).mappings().all()
            active_count = int(
                connection.execute(
                    select(func.count()).select_from(platform_sessions).where(active_clause)
                ).scalar_one()
            )
            unique_ips = int(
                connection.execute(
                    select(func.count(func.distinct(platform_sessions.c.current_ip)))
                    .where(active_clause, platform_sessions.c.current_ip.is_not(None))
                ).scalar_one()
            )
            failed_logins = int(
                connection.execute(
                    select(func.count()).select_from(authentication_events).where(
                        authentication_events.c.success.is_(False),
                        authentication_events.c.occurred_at >= recent_cutoff,
                    )
                ).scalar_one()
            )

        sessions: list[dict[str, object]] = []
        active_by_user: dict[int, dict[str, object]] = {}
        for row in session_rows:
            active = bool(
                row["ended_at"] is None
                and row["revoked_at"] is None
                and utc_datetime(row["expires_at"]) > now
            )
            sessions.append(
                {
                    "session_key": row["session_id_hash"],
                    "user_id": int(row["user_id"]),
                    "username": row["username"],
                    "display_name": row["display_name"],
                    "authentication_method": row["authentication_method"],
                    "login_ip": row["login_ip"],
                    "current_ip": row["current_ip"],
                    "country_code": row["country_code"],
                    "user_agent": row["user_agent"],
                    "cloudflare_ray": row["cloudflare_ray"],
                    "started_at": utc_datetime(row["started_at"]),
                    "last_seen_at": utc_datetime(row["last_seen_at"]),
                    "expires_at": utc_datetime(row["expires_at"]),
                    "ended_at": utc_datetime(row["ended_at"]),
                    "revoked_at": utc_datetime(row["revoked_at"]),
                    "revoke_reason": row["revoke_reason"],
                    "active": active,
                    "current": row["session_id_hash"] == current_key,
                }
            )
            if active:
                item = active_by_user.setdefault(
                    int(row["user_id"]),
                    {
                        "user_id": int(row["user_id"]),
                        "username": row["username"],
                        "display_name": row["display_name"],
                        "session_count": 0,
                        "ip_addresses": set(),
                    },
                )
                item["session_count"] = int(item["session_count"]) + 1
                if row["current_ip"]:
                    item["ip_addresses"].add(row["current_ip"])
        concurrent = []
        for item in active_by_user.values():
            ips = sorted(item.pop("ip_addresses"))
            if int(item["session_count"]) > 1 or len(ips) > 1:
                concurrent.append({**item, "ip_addresses": ips})
        events = [
            {
                "event_id": int(row["event_id"]),
                "event_type": row["event_type"],
                "username": row["username_snapshot"],
                "success": bool(row["success"]),
                "ip_address": row["ip_address"],
                "occurred_at": utc_datetime(row["occurred_at"]),
                "details": row["details"] or {},
            }
            for row in event_rows
        ]
        return {
            "status": "ready",
            "summary": {
                "active_sessions": active_count,
                "concurrent_accounts": len(concurrent),
                "unique_active_ips": unique_ips,
                "failed_logins_24h": failed_logins,
            },
            "concurrent_accounts": concurrent,
            "sessions": sessions,
            "events": events,
        }

    @staticmethod
    def _event(connection, event_type, username, actor_user_id, success, ip_address, details) -> None:
        connection.execute(
            insert(authentication_events).values(
                event_type=event_type,
                username_snapshot=str(username)[:80],
                actor_user_id=actor_user_id,
                success=success,
                ip_address=ip_address,
                occurred_at=datetime.now(UTC),
                details={key: value for key, value in details.items() if value is not None},
            )
        )

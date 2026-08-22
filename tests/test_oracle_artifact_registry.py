"""Tests for environment-scoped Oracle artifact lifecycle management."""

from pathlib import Path

from app.models.oracle_artifact import (
    OracleArtifactSource,
    OracleArtifactStatus,
    OracleArtifactType,
    OracleEnvironment,
)
from app.services.oracle_artifact_registry import OracleArtifactRegistry


def _environment(url: str, application: str) -> OracleEnvironment:
    return OracleEnvironment.from_settings(url, application)


def test_registrations_are_isolated_by_server_and_application(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite3"
    vision = OracleArtifactRegistry(
        database,
        _environment("https://pod-a.oraclecloud.com", "Vision"),
    )
    ebpcs = OracleArtifactRegistry(
        database,
        _environment("https://pod-a.oraclecloud.com", "EBPCS"),
    )
    other_pod = OracleArtifactRegistry(
        database,
        _environment("https://pod-b.oraclecloud.com", "Vision"),
    )

    vision.seed(
        OracleArtifactType.PIPELINE,
        (("PIPE01", "Forecast Pipeline", None),),
    )

    assert [item.oracle_identifier for item in vision.list(OracleArtifactType.PIPELINE)] == ["PIPE01"]
    assert ebpcs.list(OracleArtifactType.PIPELINE) == ()
    assert other_pod.list(OracleArtifactType.PIPELINE) == ()


def test_missing_registration_is_hidden_then_soft_deactivated(
    tmp_path: Path,
) -> None:
    registry = OracleArtifactRegistry(
        tmp_path / "catalog.sqlite3",
        _environment("https://pod.oraclecloud.com", "Vision"),
    )
    registry.seed(
        OracleArtifactType.PIPELINE,
        (("PIPE01", "Forecast Pipeline", None),),
    )

    first = registry.mark_missing(
        OracleArtifactType.PIPELINE,
        "PIPE01",
        "Oracle returned HTTP 404: Not Found",
    )
    second = registry.mark_missing(
        OracleArtifactType.PIPELINE,
        "PIPE01",
        "Oracle returned HTTP 404: Not Found",
    )

    assert first.status == OracleArtifactStatus.MISSING
    assert first.is_active is True
    assert second.status == OracleArtifactStatus.INACTIVE
    assert second.is_active is False
    assert registry.list(OracleArtifactType.PIPELINE) == ()
    assert registry.list(
        OracleArtifactType.PIPELINE,
        include_inactive=True,
    )[0].oracle_identifier == "PIPE01"


def test_successful_verification_reactivates_a_missing_registration(
    tmp_path: Path,
) -> None:
    registry = OracleArtifactRegistry(
        tmp_path / "catalog.sqlite3",
        _environment("https://pod.oraclecloud.com", "Vision"),
    )
    registry.seed(
        OracleArtifactType.DATA_INTEGRATION,
        (("Revenue_Load", "Revenue Load", None),),
    )
    registry.mark_missing(
        OracleArtifactType.DATA_INTEGRATION,
        "Revenue_Load",
        "not found",
    )
    registry.mark_missing(
        OracleArtifactType.DATA_INTEGRATION,
        "Revenue_Load",
        "not found",
    )

    restored = registry.mark_verified(
        OracleArtifactType.DATA_INTEGRATION,
        "Revenue_Load",
    )

    assert restored.status == OracleArtifactStatus.VERIFIED
    assert restored.is_active is True
    assert restored.consecutive_missing_count == 0


def test_authoritative_snapshot_adds_and_soft_deactivates_live_artifacts(
    tmp_path: Path,
) -> None:
    registry = OracleArtifactRegistry(
        tmp_path / "catalog.sqlite3",
        _environment("https://pod.oraclecloud.com", "Vision"),
    )

    first = registry.reconcile_snapshot(
        OracleArtifactType.BUSINESS_RULE,
        ("Calculate Revenue", "Aggregate Plan"),
    )
    registry.reconcile_snapshot(
        OracleArtifactType.BUSINESS_RULE,
        ("Calculate Revenue",),
    )
    second = registry.reconcile_snapshot(
        OracleArtifactType.BUSINESS_RULE,
        ("Calculate Revenue",),
    )

    assert {item.oracle_identifier for item in first} == {
        "Calculate Revenue",
        "Aggregate Plan",
    }
    assert all(item.source == OracleArtifactSource.LIVE_DISCOVERY for item in first)
    removed = next(
        item for item in second if item.oracle_identifier == "Aggregate Plan"
    )
    assert removed.status == OracleArtifactStatus.INACTIVE
    assert removed.is_active is False

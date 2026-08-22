"""Compatibility adapter for the unified Oracle artifact registry."""

from __future__ import annotations

from app.infrastructure.database.engine import DatabaseTarget
from app.models.oracle_artifact import (
    OracleArtifactSource,
    OracleArtifactStatus,
    OracleArtifactType,
    OracleEnvironment,
)
from app.models.pipeline_catalog import PipelineCatalogDefinition
from app.services.oracle_artifact_registry import OracleArtifactRegistry


class SQLPipelineRegistry:
    """Preserve the former repository API for tests and legacy callers."""

    def __init__(self, database_target: DatabaseTarget) -> None:
        self._registry = OracleArtifactRegistry(
            database_target,
            OracleEnvironment(
                key="__legacy_api__",
                base_url="legacy://pipeline-registry",
                application_name="Legacy",
            ),
        )

    def save(
        self,
        definition: PipelineCatalogDefinition,
    ) -> PipelineCatalogDefinition:
        self._registry.register(
            OracleArtifactType.PIPELINE,
            definition.code,
            display_name=definition.name,
            description=definition.description,
            source=OracleArtifactSource.LEGACY,
            status=OracleArtifactStatus.VERIFIED,
        )
        return definition

    def list_all(self) -> tuple[PipelineCatalogDefinition, ...]:
        return tuple(
            PipelineCatalogDefinition(
                code=item.oracle_identifier,
                name=item.display_name,
                description=item.description,
            )
            for item in self._registry.list(OracleArtifactType.PIPELINE)
        )


SQLitePipelineRegistry = SQLPipelineRegistry

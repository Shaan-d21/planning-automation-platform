"""Tests for durable Pipeline registrations."""

import json
from pathlib import Path

from app.models.pipeline_catalog import PipelineCatalogDefinition
from app.services.pipeline_catalog_service import PipelineCatalogService
from app.services.pipeline_registry import SQLitePipelineRegistry


def test_registered_pipeline_joins_json_seed_catalog(tmp_path: Path) -> None:
    catalog = tmp_path / "pipelines.json"
    catalog.write_text(
        json.dumps(
            {
                "pipelines": [
                    {"code": "PIPE01", "name": "Seed Pipeline"}
                ]
            }
        ),
        encoding="utf-8",
    )
    database = tmp_path / "history.sqlite3"
    SQLitePipelineRegistry(database).save(
        PipelineCatalogDefinition(
            code="PIPE02",
            name="Discovered Pipeline",
            description="Verified from Oracle EPM",
        )
    )

    definitions = PipelineCatalogService(database).load(catalog)

    assert [item.code for item in definitions] == ["PIPE01", "PIPE02"]


def test_json_seed_takes_precedence_over_duplicate_registration(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "pipelines.json"
    catalog.write_text(
        '{"pipelines": [{"code": "PIPE01", "name": "Approved Name"}]}',
        encoding="utf-8",
    )
    database = tmp_path / "history.sqlite3"
    SQLitePipelineRegistry(database).save(
        PipelineCatalogDefinition(code="pipe01", name="Oracle Name")
    )

    definitions = PipelineCatalogService(database).load(catalog)

    assert len(definitions) == 1
    assert definitions[0].name == "Approved Name"

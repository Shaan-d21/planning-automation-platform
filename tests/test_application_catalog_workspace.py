"""Application ownership tests for reusable local catalogs."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from app.application.operations import OperationCatalogService
from app.config.settings import Settings
from app.models.planning_cycle import PlanningCycleDefinition
from app.models.planning_process import (
    PlanningProcessDefinition,
    PlanningProcessStepDefinition,
    PlanningProcessStepType,
)
from app.services.application_catalog_workspace import (
    ApplicationCatalogWorkspace,
)
from app.services.pipeline_process_repository import (
    SQLPipelineProcessRepository,
)


def _settings(tmp_path: Path) -> Settings:
    pipeline = tmp_path / "pipelines.json"
    integration = tmp_path / "integrations.json"
    processes = tmp_path / "processes.json"
    cycles = tmp_path / "cycles.json"
    pipeline.write_text(
        json.dumps({"version": 1, "pipelines": [{"code": "P1", "name": "P1"}]}),
        encoding="utf-8",
    )
    integration.write_text(
        json.dumps({"version": 1, "integrations": [{"name": "Load 1"}]}),
        encoding="utf-8",
    )
    processes.write_text(
        json.dumps({"version": 1, "processes": []}),
        encoding="utf-8",
    )
    cycles.write_text(
        json.dumps({"version": 1, "cycles": []}),
        encoding="utf-8",
    )
    return Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="administrator",
        epm_password="secret",
        application_name="Vision",
        workflow_database_file=tmp_path / "history.sqlite3",
        runtime_data_dir=tmp_path / "runtime",
        pipeline_catalog_file=pipeline,
        data_integration_catalog_file=integration,
        planning_process_catalog_file=processes,
        planning_cycle_catalog_file=cycles,
    )


def _process(name: str) -> tuple[PlanningProcessDefinition, PlanningCycleDefinition]:
    return (
        PlanningProcessDefinition(
            code="MONTH_CLOSE",
            display_name=name,
            cycle_code="MONTH_CLOSE_CYCLE",
            steps=(
                PlanningProcessStepDefinition(
                    step_type=PlanningProcessStepType.RUN_PIPELINE,
                    name="Run Pipeline",
                ),
            ),
        ),
        PlanningCycleDefinition(
            code="MONTH_CLOSE_CYCLE",
            display_name=f"{name} Cycle",
            pipeline_code="P1",
            data_map_name=None,
        ),
    )


def test_catalog_seeds_only_the_deployment_application(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    deployment = ApplicationCatalogWorkspace(
        settings,
        application_id=11,
        seed_from_configuration=True,
    )
    other = ApplicationCatalogWorkspace(
        settings,
        application_id=22,
    )

    deployment_payload = json.loads(
        deployment.pipeline_catalog_file.read_text(encoding="utf-8")
    )
    other_payload = json.loads(
        other.pipeline_catalog_file.read_text(encoding="utf-8")
    )

    assert deployment_payload["pipelines"][0]["code"] == "P1"
    assert other_payload == {"version": 1, "pipelines": []}
    assert deployment.pipeline_catalog_file != other.pipeline_catalog_file


def test_operation_allow_lists_do_not_seed_a_second_application(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    deployment = OperationCatalogService(
        settings,
        application_id=11,
        seed_catalogs=True,
    ).discover_registered()
    other = OperationCatalogService(
        replace(settings, application_name="Consolidation"),
        application_id=22,
    ).discover_registered()

    assert [item.code for item in deployment.pipelines] == ["P1"]
    assert [item.name for item in deployment.data_integrations] == ["Load 1"]
    assert other.pipelines == ()
    assert other.data_integrations == ()


def test_designer_records_allow_same_code_in_two_applications(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = SQLPipelineProcessRepository(
        settings.database_target,
        application_id=11,
    )
    second = SQLPipelineProcessRepository(
        settings.database_target,
        application_id=22,
    )
    first_process, first_cycle = _process("First Application Close")
    second_process, second_cycle = _process("Second Application Close")

    first_draft = first.save_draft(first_process, first_cycle)
    second_draft = second.save_draft(second_process, second_cycle)
    first.activate(first_process.code, first_draft.version)
    second.activate(second_process.code, second_draft.version)

    assert first.get_active("MONTH_CLOSE").process.display_name == (
        "First Application Close"
    )
    assert second.get_active("MONTH_CLOSE").process.display_name == (
        "Second Application Close"
    )
    assert len(first.list_versions()) == 1
    assert len(second.list_versions()) == 1


def test_legacy_designer_records_are_adopted_only_by_deployment_app(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    legacy = SQLPipelineProcessRepository(settings.database_target)
    process, cycle = _process("Legacy Close")
    draft = legacy.save_draft(process, cycle)
    legacy.activate(process.code, draft.version)

    deployment = SQLPipelineProcessRepository(
        settings.database_target,
        application_id=11,
        adopt_legacy=True,
    )
    other = SQLPipelineProcessRepository(
        settings.database_target,
        application_id=22,
    )

    assert deployment.get_active("MONTH_CLOSE") is not None
    assert other.get_active("MONTH_CLOSE") is None

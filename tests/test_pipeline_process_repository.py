"""Tests for versioned Pipeline Process Designer persistence."""

from pathlib import Path

from app.models.pipeline_process_design import ProcessDesignStatus
from app.models.planning_cycle import PlanningCycleDefinition
from app.models.planning_process import (
    PlanningProcessDefinition,
    PlanningProcessStepDefinition,
    PlanningProcessStepType,
    ProcessContextMode,
)
from app.services.pipeline_process_repository import (
    SQLitePipelineProcessRepository,
)
from app.services.planning_process_catalog_service import (
    PlanningProcessCatalogService,
)
from app.services.planning_cycle_catalog_service import (
    PlanningCycleCatalogService,
)


def _definitions(name: str = "Forecast Process"):
    process = PlanningProcessDefinition(
        code="FORECAST_PROCESS",
        display_name=name,
        cycle_code="FORECAST_PROCESS_CYCLE",
        steps=(
            PlanningProcessStepDefinition(
                step_type=PlanningProcessStepType.PREFLIGHT,
                name="Validate Oracle Pipeline Inputs",
            ),
            PlanningProcessStepDefinition(
                step_type=PlanningProcessStepType.RUN_PIPELINE,
                name="Run Oracle Pipeline",
            ),
        ),
        context_mode=ProcessContextMode.PIPELINE_DEFAULTS,
    )
    cycle = PlanningCycleDefinition(
        code="FORECAST_PROCESS_CYCLE",
        display_name=f"{name} Cycle",
        pipeline_code="PIPE01",
        data_map_name=None,
    )
    return process, cycle


def test_repository_versions_and_atomically_activates_process(
    tmp_path: Path,
) -> None:
    repository = SQLitePipelineProcessRepository(tmp_path / "history.sqlite3")
    process, cycle = _definitions()

    first = repository.save_draft(process, cycle)
    second_process, second_cycle = _definitions("Updated Forecast")
    second = repository.save_draft(second_process, second_cycle)
    repository.activate(first.process.code, first.version)
    active = repository.activate(second.process.code, second.version)

    versions = repository.list_versions(first.process.code)

    assert first.version == 1
    assert second.version == 2
    assert active.status is ProcessDesignStatus.ACTIVE
    assert active.process.context_mode is ProcessContextMode.PIPELINE_DEFAULTS
    assert versions[0].status is ProcessDesignStatus.ACTIVE
    assert versions[1].status is ProcessDesignStatus.RETIRED


def test_active_designer_process_joins_static_process_catalog(
    tmp_path: Path,
) -> None:
    catalog_file = tmp_path / "processes.json"
    catalog_file.write_text(
        '{"version": 1, "processes": []}',
        encoding="utf-8",
    )
    database = tmp_path / "history.sqlite3"
    repository = SQLitePipelineProcessRepository(database)
    process, cycle = _definitions()
    draft = repository.save_draft(process, cycle)
    repository.activate(process.code, draft.version)

    definitions = PlanningProcessCatalogService(database).load(catalog_file)

    assert [item.code for item in definitions] == ["FORECAST_PROCESS"]


def test_active_designer_definition_overrides_legacy_catalog_entry(
    tmp_path: Path,
) -> None:
    process_catalog = tmp_path / "processes.json"
    process_catalog.write_text(
        """{
          "processes": [{
            "code": "FORECAST_PROCESS",
            "displayName": "Legacy Forecast",
            "cycleCode": "FORECAST_PROCESS_CYCLE",
            "steps": [
              {"type": "PREFLIGHT", "name": "Legacy validation"},
              {"type": "RUN_PIPELINE", "name": "Legacy Pipeline"}
            ]
          }]
        }""",
        encoding="utf-8",
    )
    cycle_catalog = tmp_path / "cycles.json"
    cycle_catalog.write_text(
        """{
          "cycles": [{
            "code": "FORECAST_PROCESS_CYCLE",
            "displayName": "Legacy Cycle",
            "pipelineCode": "OLD_PIPE"
          }]
        }""",
        encoding="utf-8",
    )
    database = tmp_path / "history.sqlite3"
    repository = SQLitePipelineProcessRepository(database)
    process, cycle = _definitions("Published Forecast")
    draft = repository.save_draft(process, cycle)
    repository.activate(process.code, draft.version)

    resolved_process = PlanningProcessCatalogService(database).get(
        process_catalog,
        process.code,
    )
    resolved_cycle = PlanningCycleCatalogService(database).get(
        cycle_catalog,
        cycle.code,
    )

    assert resolved_process.display_name == "Published Forecast"
    assert resolved_process.steps[0].name == (
        "Validate Oracle Pipeline Inputs"
    )
    assert resolved_cycle.pipeline_code == "PIPE01"

    repository.deactivate(process.code)

    fallback_process = PlanningProcessCatalogService(database).get(
        process_catalog,
        process.code,
    )
    fallback_cycle = PlanningCycleCatalogService(database).get(
        cycle_catalog,
        cycle.code,
    )
    assert fallback_process.display_name == "Legacy Forecast"
    assert fallback_cycle.pipeline_code == "OLD_PIPE"


def test_repository_saves_reusable_run_profile(tmp_path: Path) -> None:
    repository = SQLitePipelineProcessRepository(tmp_path / "history.sqlite3")
    process, cycle = _definitions()
    draft = repository.save_draft(process, cycle)
    repository.activate(process.code, draft.version)

    profile = repository.save_profile(
        process_code=process.code,
        name="Working Forecast",
        year="FY26",
        start_period="Jan",
        end_period="Mar",
        pipeline_variables={"IMPORTMODE": "Replace"},
        inbox_files={"DATA_FILE": "#epminbox/forecast.csv"},
    )

    saved = repository.get_profile(process.code, profile.profile_id)

    assert saved is not None
    assert saved.name == "Working Forecast"
    assert dict(saved.pipeline_variables) == {"IMPORTMODE": "Replace"}
    assert saved.one_click_ready is True

    archived = repository.archive_profile(
        process.code,
        profile.profile_id,
    )

    assert archived.name == "Working Forecast"
    assert repository.list_profiles(process.code) == ()
    assert repository.get_profile(process.code, profile.profile_id) is None


def test_open_draft_is_replaced_and_active_process_can_be_deactivated(
    tmp_path: Path,
) -> None:
    repository = SQLitePipelineProcessRepository(tmp_path / "history.sqlite3")
    process, cycle = _definitions()
    first = repository.save_draft(process, cycle)
    repository.activate(process.code, first.version)
    revised_process, revised_cycle = _definitions("Revised Forecast")

    draft = repository.save_or_replace_draft(
        revised_process,
        revised_cycle,
    )
    final_process, final_cycle = _definitions("Final Forecast")
    replaced = repository.save_or_replace_draft(
        final_process,
        final_cycle,
    )

    assert draft.version == 2
    assert replaced.version == 2
    assert len(repository.list_versions(process.code)) == 2

    repository.deactivate(process.code)

    assert repository.get_active(process.code) is None

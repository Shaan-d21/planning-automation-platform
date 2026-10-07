"""Tests for the standalone report workspace application service."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.application.reports import (
    ReportGenerationOperationInput,
    ReportWorkspaceService,
)
from app.config.settings import Settings
from app.models.report import (
    DataSliceReportDefinition,
    ReportAxisSegment,
)
from app.utils.exceptions import ReportGenerationError


def _settings(tmp_path: Path) -> Settings:
    catalog = tmp_path / "reports.json"
    catalog.write_text(
        json.dumps(
            {
                "version": 1,
                "reports": [
                    {
                        "name": "Revenue Report",
                        "title": "Revenue Forecast",
                        "cube": "Plan1",
                        "pov": {"Year": "FY25"},
                        "columns": [
                            {
                                "dimensions": ["Period"],
                                "members": [["Jan", "Feb"]],
                            }
                        ],
                        "rows": [
                            {
                                "dimensions": ["Account"],
                                "members": [["Revenue"]],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="administrator",
        epm_password="secret",
        application_name="Vision",
        report_catalog_file=catalog,
        report_output_dir=tmp_path / "reports",
    )


def test_report_workspace_lists_registered_definitions(
    tmp_path: Path,
) -> None:
    catalog = ReportWorkspaceService(_settings(tmp_path)).catalog()

    assert len(catalog) == 1
    assert catalog[0].name == "Revenue Report"
    assert catalog[0].cube == "Plan1"
    assert catalog[0].default_pov == (("Year", "FY25"),)
    assert catalog[0].rows == (("Account", ("Revenue",)),)
    assert catalog[0].columns == (("Period", ("Jan", "Feb")),)


def test_registered_report_preflight_uses_catalog_without_oracle(
    tmp_path: Path,
) -> None:
    preflight = ReportWorkspaceService(_settings(tmp_path)).preflight(
        "revenue report"
    )

    assert preflight.form_name == "Revenue Report"
    assert preflight.title == "Revenue Forecast"
    assert preflight.cube == "Plan1"
    assert preflight.registered is True
    assert preflight.page_dimensions == ("Year",)
    assert preflight.row_dimensions == ("Account",)
    assert preflight.column_dimensions == ("Period",)


def test_report_workspace_registers_and_immediately_lists_definition(
    tmp_path: Path,
) -> None:
    workspace = ReportWorkspaceService(_settings(tmp_path))

    registered = workspace.register(
        DataSliceReportDefinition(
            name="Margin Report",
            title="Margin Analysis",
            cube="Plan1",
            pov=(("Year", "FY26"),),
            columns=(
                ReportAxisSegment(
                    dimensions=("Period",),
                    members=(("Jan", "Feb"),),
                ),
            ),
            rows=(
                ReportAxisSegment(
                    dimensions=("Account",),
                    members=(("Gross Profit",),),
                ),
            ),
        )
    )

    assert registered.name == "Margin Report"
    assert [item.name for item in workspace.catalog()] == [
        "Revenue Report",
        "Margin Report",
    ]


def test_report_workspace_isolates_catalog_and_output_by_application(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    first = ReportWorkspaceService(
        settings,
        application_id=11,
        seed_catalog_file=settings.report_catalog_file,
    )
    second = ReportWorkspaceService(settings, application_id=12)

    assert [item.name for item in first.catalog()] == ["Revenue Report"]
    assert second.catalog() == ()
    assert first.catalog_file != second.catalog_file
    assert first.output_dir != second.output_dir
    assert first.catalog_file.is_relative_to(settings.report_output_dir)
    assert second.output_dir.is_relative_to(settings.report_output_dir)

    second.register(
        DataSliceReportDefinition(
            name="FCCS Balance Sheet",
            title="FCCS Balance Sheet",
            cube="Consol",
            pov=(("Year", "FY26"),),
            columns=(
                ReportAxisSegment(
                    dimensions=("Period",),
                    members=(("Jan",),),
                ),
            ),
            rows=(
                ReportAxisSegment(
                    dimensions=("Account",),
                    members=(("Total Assets",),),
                ),
            ),
        )
    )

    assert [item.name for item in first.catalog()] == ["Revenue Report"]
    assert [item.name for item in second.catalog()] == [
        "FCCS Balance Sheet"
    ]


def test_report_workspace_seed_does_not_overwrite_scoped_catalog(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    workspace = ReportWorkspaceService(
        settings,
        application_id=11,
        seed_catalog_file=settings.report_catalog_file,
    )
    workspace.delete("Revenue Report")

    reloaded = ReportWorkspaceService(
        settings,
        application_id=11,
        seed_catalog_file=settings.report_catalog_file,
    )

    assert reloaded.catalog() == ()


def test_report_input_rejects_duplicate_pov_dimensions() -> None:
    with pytest.raises(ReportGenerationError, match="more than once"):
        ReportWorkspaceService.normalize_input(
            ReportGenerationOperationInput(
                form_name="Revenue Report",
                title="Revenue",
                page_member_overrides=(
                    ("Year", "FY25"),
                    ("year", "FY26"),
                ),
            )
        )

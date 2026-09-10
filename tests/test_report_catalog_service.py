"""Tests for catalog-managed data-slice reports."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models.report import DataSliceReportDefinition, ReportAxisSegment
from app.services.report_catalog_service import ReportCatalogService
from app.utils.exceptions import ConfigurationError


def _write_catalog(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "reports": [
                    {
                        "name": "Revenue Report",
                        "title": "Revenue",
                        "cube": "VisASO",
                        "pov": {
                            "Scenario": "Actual",
                            "Year": "FY22",
                        },
                        "columns": [
                            {
                                "dimensions": ["Period", "Product"],
                                "members": [
                                    ["Jan", "Feb"],
                                    ["Phone", "Laptop"],
                                ],
                            }
                        ],
                        "rows": [
                            {
                                "dimensions": ["Account"],
                                "members": [["Revenue", "Gross Profit"]],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_catalog_loads_report_definition(tmp_path: Path) -> None:
    catalog = tmp_path / "reports.json"
    _write_catalog(catalog)

    definition = ReportCatalogService().find(catalog, "revenue report")

    assert definition is not None
    assert definition.cube == "VisASO"
    assert definition.page_dimensions == ("Scenario", "Year")
    assert definition.column_dimensions == ("Period", "Product")
    assert definition.row_dimensions == ("Account",)


def test_catalog_rejects_dimension_on_multiple_axes(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "reports.json"
    _write_catalog(catalog)
    document = json.loads(catalog.read_text(encoding="utf-8"))
    document["reports"][0]["pov"]["Account"] = "Revenue"
    catalog.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="more than one axis"):
        ReportCatalogService().load(catalog)


def test_catalog_registers_report_and_preserves_existing_entries(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "reports.json"
    _write_catalog(catalog)
    definition = DataSliceReportDefinition(
        name="Workforce Report",
        title="Workforce Planning",
        cube="Plan2",
        pov=(),
        columns=(
            ReportAxisSegment(
                dimensions=("Period",),
                members=(("Jan", "Feb"),),
            ),
        ),
        rows=(
            ReportAxisSegment(
                dimensions=("Employee",),
                members=(("E100", "E200"),),
            ),
        ),
    )

    registered = ReportCatalogService().register(catalog, definition)
    definitions = ReportCatalogService().load(catalog)

    assert registered == definition
    assert [item.name for item in definitions] == [
        "Revenue Report",
        "Workforce Report",
    ]
    document = json.loads(catalog.read_text(encoding="utf-8"))
    assert document["version"] == 1
    assert document["reports"][1]["pov"] == {}


def test_catalog_registration_does_not_replace_existing_report(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "reports.json"
    _write_catalog(catalog)
    definition = ReportCatalogService().load(catalog)[0]

    with pytest.raises(ConfigurationError, match="already registered"):
        ReportCatalogService().register(catalog, definition)

    assert len(ReportCatalogService().load(catalog)) == 1


def test_catalog_deletes_only_the_selected_saved_view(tmp_path: Path) -> None:
    catalog = tmp_path / "reports.json"
    _write_catalog(catalog)

    deleted = ReportCatalogService().delete(catalog, "revenue report")

    assert deleted.name == "Revenue Report"
    assert ReportCatalogService().load(catalog) == ()

    with pytest.raises(ConfigurationError, match="was not found"):
        ReportCatalogService().delete(catalog, "Revenue Report")

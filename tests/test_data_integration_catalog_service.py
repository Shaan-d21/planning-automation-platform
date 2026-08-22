"""Tests for the local Data Integration selection catalog."""

from __future__ import annotations

import pytest

from app.services.data_integration_catalog_service import (
    DataIntegrationCatalogService,
)
from app.utils.exceptions import ConfigurationError


def test_loads_integrations_in_configured_order(tmp_path) -> None:
    catalog = tmp_path / "integrations.json"
    catalog.write_text(
        """
        {
          "integrations": [
            {"name": "Revenue_Load", "description": "Revenue"},
            {"name": "Headcount_Load"}
          ]
        }
        """,
        encoding="utf-8",
    )

    definitions = DataIntegrationCatalogService().load(catalog)

    assert [definition.name for definition in definitions] == [
        "Revenue_Load",
        "Headcount_Load",
    ]
    assert definitions[0].display_label == "Revenue_Load - Revenue"
    assert definitions[1].description is None


def test_rejects_duplicate_names_case_insensitively(tmp_path) -> None:
    catalog = tmp_path / "integrations.json"
    catalog.write_text(
        """
        {
          "integrations": [
            {"name": "Revenue_Load"},
            {"name": "revenue_load"}
          ]
        }
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="duplicate name"):
        DataIntegrationCatalogService().load(catalog)


def test_reports_invalid_json_location(tmp_path) -> None:
    catalog = tmp_path / "integrations.json"
    catalog.write_text('{"integrations": [}', encoding="utf-8")

    with pytest.raises(ConfigurationError, match="invalid JSON"):
        DataIntegrationCatalogService().load(catalog)


def test_reports_missing_catalog(tmp_path) -> None:
    catalog = tmp_path / "missing.json"

    with pytest.raises(ConfigurationError, match="does not exist"):
        DataIntegrationCatalogService().load(catalog)

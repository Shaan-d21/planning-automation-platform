"""Tests for the local Pipeline catalog."""

from __future__ import annotations

import pytest

from app.services.pipeline_catalog_service import PipelineCatalogService
from app.utils.exceptions import ConfigurationError


def test_load_pipeline_catalog(tmp_path) -> None:
    catalog = tmp_path / "pipelines.json"
    catalog.write_text(
        """
        {
          "pipelines": [
            {
              "code": "PIPE01",
              "name": "PL_ProductRevenueForecast",
              "description": "Forecast pipeline"
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    definitions = PipelineCatalogService().load(catalog)

    assert len(definitions) == 1
    assert definitions[0].code == "PIPE01"
    assert (
        definitions[0].display_label
        == "PL_ProductRevenueForecast (PIPE01) - Forecast pipeline"
    )


def test_pipeline_catalog_rejects_duplicate_codes(tmp_path) -> None:
    catalog = tmp_path / "pipelines.json"
    catalog.write_text(
        """
        {
          "pipelines": [
            {"code": "PIPE01", "name": "First"},
            {"code": "pipe01", "name": "Second"}
          ]
        }
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="duplicate code"):
        PipelineCatalogService().load(catalog)

from app.agent.context import EntityResolutionStatus
from app.agent.entity_resolution import CatalogEntityResolver


CATALOG = (
    ("Actual_to_Forecast", "Actual to Forecast"),
    ("Plan_to_Forecast", "Plan to Forecast"),
    ("Aggregate_Plan", "Aggregate Plan"),
)


def test_entity_resolution_returns_canonical_exact_identifier() -> None:
    result = CatalogEntityResolver.resolve("actual to forecast", CATALOG)

    assert result.status is EntityResolutionStatus.EXACT
    assert result.canonical_name == "Actual_to_Forecast"


def test_entity_resolution_reports_ambiguity_instead_of_guessing() -> None:
    result = CatalogEntityResolver.resolve("forecast", CATALOG)

    assert result.status is EntityResolutionStatus.AMBIGUOUS
    assert set(result.candidates) == {
        "Actual_to_Forecast",
        "Plan_to_Forecast",
    }
    assert result.canonical_name is None


def test_entity_resolution_distinguishes_missing_from_unavailable_catalog() -> None:
    missing = CatalogEntityResolver.resolve("Revenue", CATALOG)
    unavailable = CatalogEntityResolver.resolve(
        "Revenue", (), catalog_available=False
    )

    assert missing.status is EntityResolutionStatus.NOT_FOUND
    assert unavailable.status is EntityResolutionStatus.CATALOG_UNAVAILABLE


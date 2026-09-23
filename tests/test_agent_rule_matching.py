"""Tests for scalable, explainable Business Rule recommendations."""

from app.agent.rule_matching import (
    filter_relevant_load_artifacts,
    recommend_business_rules,
    recommend_forecast_seeding_rules,
)


def test_load_matching_compares_both_routes_and_filters_business_subject() -> None:
    artifacts = (
        ("Import Actuals", "Import Actuals", "data-import"),
        ("Import Product Units", "Import Product Units", "data-import"),
        ("Actual_Load", "Actual Load", "data-integrations"),
        ("Product_Volume_Load", "Product Volume Load", "data-integrations"),
        ("Product_Metadata", "Product Metadata", "data-integrations"),
    )

    matched = filter_relevant_load_artifacts(
        "Load product units data from Jan to Mar for FY27",
        "DATA_LOAD",
        artifacts,
    )

    assert {item[0] for item in matched} == {
        "Import Product Units",
        "Product_Volume_Load",
    }


def test_metadata_load_matching_excludes_unrelated_jobs_and_data_integrations() -> None:
    artifacts = (
        ("Import Products", "Import Products", "metadata-import"),
        ("Import Entities", "Import Entities", "metadata-import"),
        ("Product_Metadata", "Product Metadata", "data-integrations"),
        ("Product_Volume_Load", "Product Volume Load", "data-integrations"),
        ("Actual_Load", "Actual Load", "data-integrations"),
    )

    matched = filter_relevant_load_artifacts(
        "Load Product metadata using Product.csv",
        "METADATA_LOAD",
        artifacts,
    )

    assert {item[0] for item in matched} == {
        "Import Products",
        "Product_Metadata",
    }


def test_business_rule_matching_ranks_live_names_against_task_context() -> None:
    matches = recommend_business_rules(
        "Calculate the monthly Product Revenue Forecast for Sales East.",
        (
            "Aggregate Plan1",
            "Revenue_Forecast_Calc",
            "Calc_Product_Revenue",
            "Workforce Expense Allocation",
        ),
    )

    assert [item.name for item in matches[:2]] == [
        "Revenue_Forecast_Calc",
        "Calc_Product_Revenue",
    ]
    assert matches[0].confidence == "Strong match"
    assert "revenue" in matches[0].reason
    assert "forecast" in matches[0].reason


def test_business_rule_matching_returns_no_guess_for_generic_request() -> None:
    assert recommend_business_rules(
        "I want to run a business rule for this task.",
        ("Revenue Forecast", "Allocate Workforce"),
    ) == ()


def test_business_rule_matching_scales_and_limits_recommendations() -> None:
    rules = tuple(f"Revenue Forecast Region {index}" for index in range(500))

    matches = recommend_business_rules(
        "Run the revenue forecast for this region.",
        rules,
        limit=5,
    )

    assert len(matches) == 5
    assert all(item.confidence == "Strong match" for item in matches)


def test_business_rule_matching_understands_compact_oracle_rule_names() -> None:
    matches = recommend_business_rules(
        "Run the Business Rule for travel expense.",
        (
            "Create Forecast",
            "OPF_Calculate Capitalized Expense",
            "calc_travelexpense",
        ),
    )

    assert matches[0].name == "calc_travelexpense"
    assert matches[0].confidence == "Strong match"
    assert "travel" in matches[0].reason
    assert "expense" in matches[0].reason


def test_forecast_seed_matching_includes_source_to_forecast_synonyms() -> None:
    live_rules = (
        ("Actual_to_Forecast", "Actual to Forecast"),
        ("Plan_to_Forecast", "Plan to Forecast"),
        ("Create_Forecast", "Create Forecast"),
        ("Aggregate_Forecast", "Aggregate Forecast"),
        ("Unrelated", "Calculate Tax"),
    )

    matches = recommend_forecast_seeding_rules(live_rules)

    assert {item.name for item in matches[:2]} == {
        "Actual_to_Forecast",
        "Plan_to_Forecast",
    }
    assert "Create_Forecast" in {item.name for item in matches}
    assert "Unrelated" not in {item.name for item in matches}
    assert all(item.confidence == "Possible match" for item in matches)
    assert all("verify" in item.reason for item in matches[:3])

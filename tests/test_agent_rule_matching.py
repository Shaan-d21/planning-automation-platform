"""Tests for scalable, explainable Business Rule recommendations."""

from app.agent.rule_matching import (
    recommend_business_rules,
    recommend_forecast_seeding_rules,
)


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

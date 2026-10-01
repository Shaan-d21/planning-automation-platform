"""Tests for structured task-parameter corrections and contradictions."""

from app.agent.parameter_updates import AgentParameterDeltaResolver
from app.agent.task_state import (
    AgentTaskConfidence,
    AgentTaskIntent,
    AgentTaskPhase,
    AgentTaskUnderstanding,
)


def _interpreted(
    *,
    intent: AgentTaskIntent = AgentTaskIntent.DATA_LOAD,
    parameters=None,
) -> AgentTaskUnderstanding:
    return AgentTaskUnderstanding(
        intent=intent,
        phase=AgentTaskPhase.READY_FOR_PLAN,
        confidence=AgentTaskConfidence.HIGH_CONFIDENCE,
        parameters=parameters or {},
        objective="Load Actual data for January FY27.",
    )


def _active_context(**overrides):
    context = {
        "task_id": "task-1",
        "intent": "DATA_LOAD",
        "phase": "READY_FOR_PLAN",
        "objective": "Load Actual data for January FY27.",
        "parameters": {
            "scenario": "Actual",
            "period": "Jan",
            "period_reference": "explicit",
            "year": "FY27",
            "file": "actual_jan.csv",
        },
    }
    context.update(overrides)
    return context


def test_correction_emits_only_changed_year_and_preserves_task_identity_inputs() -> None:
    result = AgentParameterDeltaResolver.resolve(
        "Actually use FY28 instead",
        _active_context(),
        _interpreted(),
    )

    assert result is not None
    assert result.conflicts == ()
    assert result.updates == {"year": "FY28"}
    assert result.task.parameters == {"year": "FY28"}
    assert result.task.phase is AgentTaskPhase.READY_FOR_PLAN


def test_directional_change_selects_target_instead_of_reporting_conflict() -> None:
    from_to = AgentParameterDeltaResolver.resolve(
        "Change the year from FY27 to FY28",
        _active_context(),
        _interpreted(),
    )
    instead_of = AgentParameterDeltaResolver.resolve(
        "Use FY28 instead of FY27",
        _active_context(),
        _interpreted(),
    )

    assert from_to is not None and from_to.updates["year"] == "FY28"
    assert instead_of is not None and instead_of.updates["year"] == "FY28"
    assert from_to.conflicts == instead_of.conflicts == ()


def test_multiple_undirected_years_force_one_focused_clarification() -> None:
    result = AgentParameterDeltaResolver.resolve(
        "Load Actual data for FY27 or FY28",
        None,
        _interpreted(parameters={"scenario": "Actual", "year": "FY27"}),
    )

    assert result is not None
    assert result.task.phase is AgentTaskPhase.COLLECTING_INFORMATION
    assert result.task.missing_parameters == ("year",)
    assert result.updates == {"scenario": "Actual", "year": None}
    assert result.conflicts[0].values == ("FY27", "FY28")
    assert "**FY27** or **FY28**" in result.task.clarification_prompt


def test_multiple_undirected_periods_do_not_silently_choose_one() -> None:
    result = AgentParameterDeltaResolver.resolve(
        "Use Jan or Feb instead",
        _active_context(),
        _interpreted(),
    )

    assert result is not None
    assert result.task.phase is AgentTaskPhase.COLLECTING_INFORMATION
    assert result.task.missing_parameters == ("period",)
    assert result.updates["period"] is None
    assert result.conflicts[0].values == ("Jan", "Feb")


def test_explicit_data_load_range_is_not_misclassified_as_conflict() -> None:
    result = AgentParameterDeltaResolver.resolve(
        "Actually load Jan to Mar instead",
        _active_context(),
        _interpreted(),
    )

    assert result is not None
    assert result.conflicts == ()
    assert result.updates["start_period"] == "Jan"
    assert result.updates["end_period"] == "Mar"


def test_directional_period_change_is_not_misclassified_as_a_range() -> None:
    result = AgentParameterDeltaResolver.resolve(
        "Change the period from Jan to Mar",
        _active_context(),
        _interpreted(),
    )

    assert result is not None
    assert result.conflicts == ()
    assert result.updates["period"] == "Mar"
    assert "start_period" not in result.updates
    assert "end_period" not in result.updates


def test_variance_comparison_is_not_a_scenario_conflict() -> None:
    result = AgentParameterDeltaResolver.resolve(
        "Actually compare Actual vs Budget for Feb",
        {
            "task_id": "variance-1",
            "intent": "VARIANCE_REPORTING",
            "phase": "COLLECTING_INFORMATION",
            "objective": "Review variance.",
            "parameters": {"period": "Jan"},
        },
        _interpreted(intent=AgentTaskIntent.VARIANCE_REPORTING),
    )

    assert result is not None
    assert result.conflicts == ()
    assert result.updates["comparison"] == "Actual vs Budget"
    assert result.updates["period"] == "Feb"


def test_non_correction_does_not_capture_an_unrelated_new_request() -> None:
    assert AgentParameterDeltaResolver.resolve(
        "Show the latest reports",
        _active_context(),
        _interpreted(intent=AgentTaskIntent.UNKNOWN),
    ) is None

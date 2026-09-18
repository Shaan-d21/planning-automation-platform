"""Tests for provider-independent EPM business-task understanding."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.agent.graph import AgentGraphOrchestrator
from app.agent.execution_plan import AgentExecutionPlanBuilder
from app.agent.models import AgentMessage, AgentMessageRole
from app.agent.task_state import (
    AgentTaskConfidence,
    AgentTaskIntent,
    AgentTaskInterpreter,
    AgentTaskPhase,
)


def _messages(*turns: str) -> tuple[AgentMessage, ...]:
    return tuple(
        AgentMessage(
            message_id=index,
            conversation_id="task-conversation",
            role=AgentMessageRole.USER,
            content=content,
            created_at=datetime(2026, 9, 16, tzinfo=UTC),
        )
        for index, content in enumerate(turns, start=1)
    )


@pytest.mark.parametrize(
    "prompt",
    (
        "Start month close",
        "Run monthly closing",
        "Let's close this month",
        "Execute monthly closing tasks",
        "Complete September close",
    ),
)
def test_month_close_synonyms_share_one_business_intent(prompt: str) -> None:
    result = AgentTaskInterpreter.interpret(
        _messages(prompt),
        today=date(2026, 9, 16),
    )

    assert result.intent is AgentTaskIntent.MONTH_CLOSE


def test_month_close_asks_only_for_the_first_missing_value() -> None:
    result = AgentTaskInterpreter.interpret(
        _messages("Start month close"),
        today=date(2026, 9, 16),
    )

    assert result.phase is AgentTaskPhase.COLLECTING_INFORMATION
    assert result.missing_parameters == ("period", "activities")
    assert result.clarification_prompt == "Which period are we closing?"


def test_month_close_retains_period_and_collects_activity_reply() -> None:
    result = AgentTaskInterpreter.interpret(
        _messages(
            "Start September month close.",
            "Load actuals, run allocations, calculate Finance, then run variance reporting.",
        ),
        today=date(2026, 9, 16),
    )

    assert result.intent is AgentTaskIntent.MONTH_CLOSE
    assert result.parameters["period"] == "Sep"
    assert result.parameters["activities"] == [
        "Load actuals",
        "run allocations",
        "calculate Finance",
        "run variance reporting.",
    ]
    assert result.phase is AgentTaskPhase.READY_FOR_PLAN


def test_metadata_load_collects_dimension_then_file_preference() -> None:
    initial = AgentTaskInterpreter.interpret(_messages("Load metadata."))
    dimension = AgentTaskInterpreter.interpret(
        _messages("Load metadata.", "Account")
    )
    ready = AgentTaskInterpreter.interpret(
        _messages("Load metadata.", "Account", "Use the latest one.")
    )

    assert initial.clarification_prompt == (
        "Sure. Which dimension do you want to update?"
    )
    assert dimension.parameters["dimension"] == "Account"
    assert dimension.missing_parameters == ("file_reference",)
    assert ready.parameters["file_preference"] == "latest"
    assert ready.phase is AgentTaskPhase.READY_FOR_PLAN


def test_relative_data_load_period_is_explicitly_retained() -> None:
    result = AgentTaskInterpreter.interpret(
        _messages("Load last month's actuals using the latest file."),
        today=date(2026, 9, 16),
    )

    assert result.intent is AgentTaskIntent.DATA_LOAD
    assert result.parameters["scenario"] == "Actual"
    assert result.parameters["period"] == "Aug"
    assert result.parameters["period_reference"] == "previous_month"
    assert result.parameters["file_preference"] == "latest"
    assert result.phase is AgentTaskPhase.COLLECTING_INFORMATION
    assert result.missing_parameters == ("load_method",)
    assert "Data Integration" in str(result.clarification_prompt)


@pytest.mark.parametrize(
    ("reply", "method", "operation_code"),
    (
        ("Use Data Integration.", "DATA_INTEGRATION", "data-integrations"),
        (
            "Run a saved Planning Import Data job.",
            "PLANNING_IMPORT",
            "data-import",
        ),
    ),
)
def test_data_load_method_selects_the_governed_oracle_route(
    reply: str,
    method: str,
    operation_code: str,
) -> None:
    result = AgentTaskInterpreter.interpret(
        _messages(
            "Load last month's actuals using the latest file.",
            reply,
        ),
        today=date(2026, 9, 16),
    )

    assert result.intent is AgentTaskIntent.DATA_LOAD
    assert result.parameters["scenario"] == "Actual"
    assert result.parameters["period"] == "Aug"
    assert result.parameters["file_preference"] == "latest"
    assert result.parameters["load_method"] == method
    assert result.phase is AgentTaskPhase.READY_FOR_PLAN

    call = AgentGraphOrchestrator._deterministic_task_operation_call(
        {
            "task_context": result.to_payload(),
            "allowed_tool_names": ["prepare_operation_action"],
        }
    )
    assert call is not None
    assert call.arguments["operation_code"] == operation_code


def test_user_period_correction_updates_the_existing_task() -> None:
    result = AgentTaskInterpreter.interpret(
        _messages(
            "Load August actuals using Actual_Aug.csv.",
            "Sorry, September instead.",
        ),
        today=date(2026, 9, 16),
    )

    assert result.intent is AgentTaskIntent.DATA_LOAD
    assert result.parameters["period"] == "Sep"
    assert result.parameters["file"] == "Actual_Aug.csv"
    assert result.missing_parameters == ("load_method",)


def test_forecast_seeding_progressively_collects_cutoff_period() -> None:
    initial = AgentTaskInterpreter.interpret(
        _messages("Prepare the new forecast.")
    )
    ready = AgentTaskInterpreter.interpret(
        _messages("Prepare the new forecast.", "August.")
    )

    assert initial.intent is AgentTaskIntent.FORECAST_SEEDING
    assert "Through which month" in str(initial.clarification_prompt)
    assert ready.parameters["cutoff_period"] == "Aug"
    assert ready.phase is AgentTaskPhase.READY_FOR_PLAN


def test_forecast_seeding_retains_cutoff_when_method_is_selected() -> None:
    result = AgentTaskInterpreter.interpret(
        _messages(
            "Prepare the new forecast.",
            "Use actuals through August.",
            "Use the Business Rule.",
        )
    )

    assert result.intent is AgentTaskIntent.FORECAST_SEEDING
    assert result.parameters["cutoff_period"] == "Aug"
    assert result.parameters["execution_method"] == "BUSINESS_RULE"
    assert result.phase is AgentTaskPhase.READY_FOR_PLAN


def test_variance_reporting_collects_comparison_then_period() -> None:
    initial = AgentTaskInterpreter.interpret(_messages("Run variance report."))
    comparison = AgentTaskInterpreter.interpret(
        _messages("Run variance report.", "Actual vs Budget.")
    )
    ready = AgentTaskInterpreter.interpret(
        _messages(
            "Run variance report.",
            "Actual vs Budget.",
            "September.",
        )
    )

    assert initial.missing_parameters == ("comparison", "period")
    assert "Actual vs Budget" in str(initial.clarification_prompt)
    assert comparison.parameters["comparison"] == "Actual vs Budget"
    assert comparison.missing_parameters == ("period",)
    assert ready.parameters["period"] == "Sep"
    assert ready.phase is AgentTaskPhase.READY_FOR_PLAN


def test_variance_reporting_retains_context_when_saved_layout_is_selected() -> None:
    result = AgentTaskInterpreter.interpret(
        _messages(
            "Show September Actual vs Budget variance above 1,000 for FY26.",
            "Use saved Data Explorer view `monthly-variance` for the variance review.",
        )
    )

    assert result.intent is AgentTaskIntent.VARIANCE_REPORTING
    assert result.parameters == {
        "period": "Sep",
        "period_reference": "explicit",
        "year": "FY26",
        "comparison": "Actual vs Budget",
        "threshold": 1000.0,
        "scenario": "Budget",
        "saved_view": "monthly-variance",
    }
    assert result.phase is AgentTaskPhase.READY_FOR_PLAN


def test_variance_reporting_retains_saved_view_and_structured_pov_refinement() -> None:
    result = AgentTaskInterpreter.interpret(
        _messages(
            "Show September Actual vs Budget variance above 500 for FY22.",
            "Use saved Data Explorer view `monthly-variance` for the variance review.",
            "Refine variance comparison using saved Data Explorer view "
            "`monthly-variance`. Keep Actual vs Budget for Sep FY22 with "
            "threshold 500. POV overrides: Product=`Snacks`; Entity=`US Sales`.",
        )
    )

    assert result.intent is AgentTaskIntent.VARIANCE_REPORTING
    assert result.parameters["saved_view"] == "monthly-variance"
    assert result.parameters["pov_overrides"] == {
        "Product": "Snacks",
        "Entity": "US Sales",
    }
    assert result.phase is AgentTaskPhase.READY_FOR_PLAN


def test_conversational_cancellation_never_implies_oracle_cancellation() -> None:
    result = AgentTaskInterpreter.interpret(
        _messages("Start September month close.", "Stop after this step.")
    )

    assert result.intent is AgentTaskIntent.CANCEL_OPERATION
    assert result.phase is AgentTaskPhase.CANCELLED
    response = AgentGraphOrchestrator._deterministic_task_response(
        {"task_context": result.to_payload()}
    )
    assert response is not None
    assert "No new Oracle operation was submitted" in response
    assert "may continue" in response


def test_unknown_text_is_unsafe_to_execute() -> None:
    result = AgentTaskInterpreter.interpret(_messages("Do the usual thing."))

    assert result.intent is AgentTaskIntent.UNKNOWN
    assert result.confidence is AgentTaskConfidence.UNSAFE_TO_EXECUTE
    assert result.phase is AgentTaskPhase.UNDERSTANDING_REQUEST


@pytest.mark.parametrize(
    ("prompt", "intent"),
    (
        (
            "Run the Allocate Expenses business rule.",
            AgentTaskIntent.RUN_BUSINESS_RULE,
        ),
        (
            "Execute Revenue Load data integration.",
            AgentTaskIntent.RUN_DATA_INTEGRATION,
        ),
        ("Start the Monthly Forecast pipeline.", AgentTaskIntent.RUN_PIPELINE),
        ("Check whether the latest job finished.", AgentTaskIntent.JOB_STATUS),
    ),
)
def test_technical_intents_allow_artifact_names(
    prompt: str,
    intent: AgentTaskIntent,
) -> None:
    result = AgentTaskInterpreter.interpret(_messages(prompt))

    assert result.intent is intent
    assert result.phase is AgentTaskPhase.READY_FOR_PLAN


@pytest.mark.parametrize(
    "prompt",
    (
        "Run Aggregate Plan rule.",
        "run clear facilities allocation rule",
        "Start the plan rule Allocate Expenses.",
    ),
)
def test_business_rule_intent_accepts_business_friendly_rule_wording(
    prompt: str,
) -> None:
    result = AgentTaskInterpreter.interpret(_messages(prompt))

    assert result.intent is AgentTaskIntent.RUN_BUSINESS_RULE
    assert result.phase is AgentTaskPhase.READY_FOR_PLAN


def test_business_rule_affirmation_retains_original_task() -> None:
    result = AgentTaskInterpreter.interpret(
        _messages(
            "run aggregate plan rule",
            "yes",
            "yes prepare now",
        )
    )

    assert result.intent is AgentTaskIntent.RUN_BUSINESS_RULE
    assert result.objective == "run aggregate plan rule"
    assert result.phase is AgentTaskPhase.READY_FOR_PLAN


def test_completed_business_rule_does_not_hijack_unrelated_help() -> None:
    result = AgentTaskInterpreter.interpret(
        _messages(
            "Run Aggregate Plan rule.",
            "What can you do?",
        )
    )

    assert result.intent is AgentTaskIntent.HELP_EXPLAIN


def test_completed_prior_task_does_not_hijack_an_unrelated_question() -> None:
    result = AgentTaskInterpreter.interpret(
        _messages(
            "Update Account metadata using Account.csv.",
            "What can you do?",
        )
    )

    assert result.intent is AgentTaskIntent.HELP_EXPLAIN
    assert result.parameters == {}


def test_exact_saved_metadata_job_keeps_existing_governed_route() -> None:
    result = AgentTaskInterpreter.interpret(
        _messages("Run the Import Products metadata job.")
    )

    assert result.intent is AgentTaskIntent.UNKNOWN
    assert result.phase is AgentTaskPhase.UNDERSTANDING_REQUEST


def test_graph_uses_deterministic_progressive_clarification() -> None:
    task = AgentTaskInterpreter.interpret(_messages("Update metadata."))

    response = AgentGraphOrchestrator._deterministic_task_response(
        {"task_context": task.to_payload()}
    )

    assert response == "Sure. Which dimension do you want to update?"


def test_ready_metadata_task_enters_existing_governed_preparation() -> None:
    task = AgentTaskInterpreter.interpret(
        _messages("Update Account metadata using the latest file.")
    )

    call = AgentGraphOrchestrator._deterministic_task_operation_call(
        {
            "task_context": task.to_payload(),
            "allowed_tool_names": ["prepare_operation_action"],
        }
    )

    assert call is not None
    assert call.name == "prepare_operation_action"
    assert call.arguments["operation_code"] == "metadata-import"


def test_execution_plan_uses_registered_risk_and_requires_approval() -> None:
    task = AgentTaskInterpreter.interpret(
        _messages("Update Account metadata using the latest file.")
    )

    plan = AgentExecutionPlanBuilder.build(
        task.to_payload(),
        ["metadata-import"],
    )

    assert plan.status == "READY"
    assert plan.approval_required is True
    assert plan.steps[0].display_name == "Metadata Import"
    assert plan.steps[0].risk_level == "Elevated"


def test_execution_plan_rejects_unregistered_operation_codes() -> None:
    task = AgentTaskInterpreter.interpret(
        _messages("Update Account metadata using the latest file.")
    )

    plan = AgentExecutionPlanBuilder.build(
        task.to_payload(),
        ["invented-operation"],
    )

    assert plan.status == "UNSUPPORTED_OPERATION"
    assert plan.steps == ()
    assert plan.approval_required is False


def test_data_integration_guided_inputs_reuse_safe_task_period_context() -> None:
    task = AgentTaskInterpreter.interpret(
        _messages(
            "Load January FY27 actuals using Actual_Jan.csv.",
            "Use Data Integration.",
        )
    )

    context = AgentGraphOrchestrator._task_guided_input_context(
        {"task_context": task.to_payload()},
        "data-integrations",
    )

    assert context["task_context"] == {
        "scenario": "Actual",
        "period": "Jan",
        "year": "FY27",
        "file": "Actual_Jan.csv",
    }
    assert context["prefill"] == {
        "year": "FY27",
        "start_month": "Jan",
        "end_month": "Jan",
    }


def test_month_close_enters_the_existing_multi_step_planner() -> None:
    class EmptyCatalogGateway:
        @staticmethod
        def artifact_catalog(
            _operation_code: str,
        ) -> tuple[tuple[str, str], ...]:
            return ()

    task = AgentTaskInterpreter.interpret(
        _messages(
            "Start September month close.",
            "Run Data Integration, then Business Rule, then Data Map.",
        )
    )
    graph = object.__new__(AgentGraphOrchestrator)
    graph._gateway = EmptyCatalogGateway()
    graph._logger = __import__("logging").getLogger(__name__)

    call = graph._deterministic_task_plan_call(
        {
            "task_context": task.to_payload(),
            "allowed_tool_names": ["plan_multi_step_request"],
        }
    )

    assert call is not None
    assert call.name == "plan_multi_step_request"
    assert call.arguments["requested_steps"] == [
        "data-integrations",
        "business-rules",
        "data-maps",
    ]


def test_task_context_is_added_to_provider_instruction_without_credentials() -> None:
    task = AgentTaskInterpreter.interpret(
        _messages("Show September Actual vs Budget variance.")
    )
    graph = object.__new__(AgentGraphOrchestrator)
    graph._system_instruction = "SYSTEM"

    instruction = graph._instruction_for_state(
        {"task_context": task.to_payload()}
    )

    assert "VARIANCE_REPORTING" in instruction
    assert '"period":"Sep"' in instruction
    assert "password" not in instruction.casefold()

"""Tests for deterministic least-privilege Agent intent routing."""

from app.agent.intent import AgentIntent, AgentIntentRouter


ALL_TOOLS = frozenset(
    {
        "get_environment_summary",
        "list_platform_operations",
        "get_recent_execution_history",
        "get_execution_evidence",
        "list_planning_cubes",
        "list_cube_dimensions",
        "search_dimension_members",
        "review_data_slice",
        "compare_data_slices",
        "list_operation_artifacts",
        "prepare_operation_action",
    }
)


def test_history_intent_exposes_history_without_preparation() -> None:
    decision = AgentIntentRouter.route(
        "Summarize the five most recent executions.",
        ALL_TOOLS,
    )

    assert decision.intent is AgentIntent.HISTORY_REVIEW
    assert "get_recent_execution_history" in decision.tool_names
    assert "get_execution_evidence" in decision.tool_names
    assert "prepare_operation_action" not in decision.tool_names


def test_execution_diagnostics_exposes_evidence_without_preparation() -> None:
    decision = AgentIntentRouter.route(
        "Why did the latest failed job reject records?",
        ALL_TOOLS,
    )

    assert decision.intent is AgentIntent.HISTORY_REVIEW
    assert "get_execution_evidence" in decision.tool_names
    assert "prepare_operation_action" not in decision.tool_names


def test_preparation_intent_respects_permission_boundary() -> None:
    decision = AgentIntentRouter.route(
        "Prepare the revenue business rule for me.",
        frozenset(
            {"get_environment_summary", "list_platform_operations"}
        ),
    )

    assert decision.intent is AgentIntent.OPERATION_PREPARATION
    assert decision.tool_names == frozenset(
        {"get_environment_summary", "list_platform_operations"}
    )


def test_general_guidance_exposes_only_lightweight_discovery_tools() -> None:
    decision = AgentIntentRouter.route(
        "Explain a safe monthly forecast approach.",
        ALL_TOOLS,
    )

    assert decision.intent is AgentIntent.GENERAL_GUIDANCE
    assert decision.tool_names == frozenset(
        {"get_environment_summary", "list_platform_operations"}
    )


def test_data_review_intent_exposes_only_read_only_slice_tools() -> None:
    decision = AgentIntentRouter.route(
        "Show Planning data for revenue in Plan1 and compare source and target.",
        ALL_TOOLS,
    )

    assert "list_cube_dimensions" in decision.tool_names
    assert "search_dimension_members" in decision.tool_names
    assert "review_data_slice" in decision.tool_names
    assert "compare_data_slices" in decision.tool_names
    assert "prepare_operation_action" not in decision.tool_names


def test_data_review_follow_up_keeps_change_read_only_with_context() -> None:
    decision = AgentIntentRouter.route(
        "Change FY27 to FY28 and add Gross Profit.",
        ALL_TOOLS,
        has_data_review_context=True,
    )

    assert decision.intent is AgentIntent.DATA_REVIEW
    assert "review_data_slice" in decision.tool_names
    assert "compare_data_slices" in decision.tool_names
    assert "prepare_operation_action" not in decision.tool_names


def test_explicit_operation_still_routes_governed_with_review_context() -> None:
    decision = AgentIntentRouter.route(
        "Run the Revenue Forecast business rule.",
        ALL_TOOLS,
        has_data_review_context=True,
    )

    assert "prepare_operation_action" in decision.tool_names

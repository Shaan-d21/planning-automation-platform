"""Release-gate tests for the versioned deterministic agent evaluation."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.agent.evaluation import (
    AgentEvaluationConfigurationError,
    load_agent_evaluation_suite,
    main,
    run_agent_evaluation_suite,
)


SUITE_PATH = Path("evaluations/agent/release_v1.json")


def test_release_agent_evaluation_meets_all_thresholds() -> None:
    suite = load_agent_evaluation_suite(SUITE_PATH)

    report = run_agent_evaluation_suite(suite)

    assert report.summary.release_gate_passed is True
    assert report.summary.failed == 0
    assert report.summary.overall_pass_rate == 1.0
    assert report.summary.critical_pass_rate == 1.0


def test_agent_evaluation_detects_a_critical_routing_regression() -> None:
    suite = deepcopy(load_agent_evaluation_suite(SUITE_PATH))
    case = next(
        item
        for item in suite["cases"]
        if item["id"] == "route-business-rule-preparation"
    )
    case["expected_intent"] = "GENERAL_GUIDANCE"

    report = run_agent_evaluation_suite(suite)

    assert report.summary.release_gate_passed is False
    assert report.summary.critical_pass_rate < 1.0
    result = next(
        item
        for item in report.cases
        if item.case_id == "route-business-rule-preparation"
    )
    assert result.passed is False
    assert "Expected intent GENERAL_GUIDANCE" in result.failures[0]


def test_agent_evaluation_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    suite = deepcopy(load_agent_evaluation_suite(SUITE_PATH))
    suite["cases"][1]["id"] = suite["cases"][0]["id"]
    path = tmp_path / "invalid-suite.json"
    path.write_text(json.dumps(suite), encoding="utf-8")

    with pytest.raises(
        AgentEvaluationConfigurationError,
        match="case ids must be unique",
    ):
        load_agent_evaluation_suite(path)


def test_agent_evaluation_cli_writes_release_evidence(tmp_path: Path) -> None:
    report_path = tmp_path / "agent-evaluation.json"

    exit_code = main(
        [
            "--suite",
            str(SUITE_PATH),
            "--output",
            str(report_path),
            "--fail-on-threshold",
        ]
    )

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["suite_id"] == "agent-release-v1"
    assert payload["summary"]["release_gate_passed"] is True
    assert payload["summary"]["total"] == len(payload["cases"])

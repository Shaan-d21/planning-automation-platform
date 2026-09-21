"""Business Rule RTP parser and registry contract tests."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.agent.capabilities import AgentCapabilityGateway
from app.agent.checkpoints import AgentCheckpointStore
from app.agent.graph import AgentGraphOrchestrator
from app.agent.task_state import AgentTaskInterpreter
from app.agent.models import (
    AgentMessage,
    AgentMessageRole,
    AgentProviderTurn,
    AgentToolCall,
)
from app.config.settings import Settings
from app.services.business_rule_rtp_registry import BusinessRuleRTPRegistryService
from app.services.calc_manager_rtp_parser import CalcManagerRTPParser
from app.utils.exceptions import BusinessRuleError


RULE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<businessRule name="Calculate Revenue" cube="Plan1">
  <rtp name="Year" label="Planning Year" type="MEMBER"
       dimension="Year" required="true" order="1" />
  <runtimePrompt name="Scenario" type="MEMBER" dimension="Scenario"
       default="Forecast" required="true" order="2" />
</businessRule>
"""

SINGLE_RTP_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<businessRule name="Calculate Revenue" cube="Plan1">
  <rtp name="Amount" label="Amount" type="NUMERIC" required="true" />
</businessRule>
"""

HBR_REPO_RULE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<HBRRepo version="25.11">
  <variables>
    <variable name="Years" type="members" usage="const" id="1001" product="Planning">
      <property name="application">EPBCS</property>
      <property name="dimensionType">Year</property>
      <property name="useLastValue">false</property>
      <property name="prompt_text">ID_PRMTEXT_ENTER_THE_YEARS_07</property>
      <value />
      <limits type="expression"><property name="value">DESCENDANTS(&quot;Years&quot;)</property></limits>
    </variable>
  </variables>
  <rules>
    <rule id="1334" name="Calculate Actual Balance Sheet" product="Planning">
      <property name="application">EPBCS</property>
      <property name="plantype">Plan1</property>
      <variable_references>
        <variable_reference name="Years" id="1001">
          <property name="seq">1</property>
          <property name="hidden">false</property>
          <property name="rule_name">Calculate Actual Balance Sheet</property>
        </variable_reference>
      </variable_references>
      <script type="calcscript">FIX ({Years}) ENDFIX</script>
    </rule>
  </rules>
</HBRRepo>
"""

HBR_REPO_SCOPES_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<HBRRepo version="25.11">
  <variables>
    <variable name="GlobalPrompt" type="num" usage="const" id="1" product="Planning"><value /></variable>
    <variable name="ApplicationPrompt" type="member" usage="const" id="2" product="Planning">
      <property name="application">EPBCS</property><property name="dimension">Scenario</property><value>Forecast</value>
    </variable>
    <variable name="CubePrompt" type="member" usage="const" id="3" product="Planning">
      <property name="application">EPBCS</property><property name="plantype">Plan1</property><property name="dimension">Entity</property><value />
    </variable>
    <variable name="RulePrompt" type="integer" usage="const" id="4" product="Planning">
      <property name="application">EPBCS</property><property name="plantype">Plan1</property>
      <property name="rule">99</property><property name="rule_name">Scoped Rule</property><value />
    </variable>
  </variables>
  <rules><rule id="99" name="Scoped Rule" product="Planning">
    <property name="application">EPBCS</property><property name="plantype">Plan1</property>
    <variable_references>
      <variable_reference name="GlobalPrompt" id="1"><property name="seq">1</property></variable_reference>
      <variable_reference name="ApplicationPrompt" id="2"><property name="seq">2</property></variable_reference>
      <variable_reference name="CubePrompt" id="3"><property name="seq">3</property></variable_reference>
      <variable_reference name="RulePrompt" id="4"><property name="seq">4</property></variable_reference>
    </variable_references>
  </rule></rules>
</HBRRepo>
"""


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        epm_base_url="https://example.epm.oraclecloud.com",
        epm_username="administrator",
        epm_password="secret",
        application_name="Vision",
        workflow_database_file=tmp_path / "registry.sqlite3",
    )


def test_parser_reads_rule_and_ordered_runtime_prompts() -> None:
    result = CalcManagerRTPParser().parse("rules.xml", RULE_XML)

    assert len(result.definitions) == 1
    definition = result.definitions[0]
    assert definition.rule_name == "Calculate Revenue"
    assert definition.cube_name == "Plan1"
    assert [item.name for item in definition.prompts] == ["Year", "Scenario"]
    assert definition.prompts[0].required_at_launch is True
    assert definition.prompts[0].has_default is False
    assert definition.prompts[1].default_value == "Forecast"
    assert definition.prompts[1].has_default is True


def test_parser_reads_xml_from_lcm_zip_without_extracting() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Calculation Manager/Rules/Revenue.xml", RULE_XML)
        archive.writestr("readme.txt", "ignored")

    result = CalcManagerRTPParser().parse("CalculationManager.zip", buffer.getvalue())

    assert result.definitions[0].rule_name == "Calculate Revenue"
    assert len(result.definitions[0].prompts) == 2


def test_parser_reads_extensionless_rule_from_nested_oracle_lcm_resource() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Export.xml", b"<Package />")
        archive.writestr(
            "CALC-Calculation Manager/resource/Planning/EPBCS/Plan1/Rules/"
            "Calculate Revenue",
            HBR_REPO_RULE_XML,
        )
        archive.writestr(
            "CALC-Calculation Manager/resource/Planning/EPBCS/Plan1/Rules/"
            "binary-resource",
            b"\x89PNG\r\n\x1a\n",
        )

    result = CalcManagerRTPParser().parse(
        "CalculationManager.zip",
        buffer.getvalue(),
    )

    assert len(result.definitions) == 1
    assert result.definitions[0].rule_name == "Calculate Actual Balance Sheet"
    assert [prompt.name for prompt in result.definitions[0].prompts] == ["Years"]


def test_parser_resolves_hbrrepo_rule_reference_and_application_scope() -> None:
    result = CalcManagerRTPParser().parse("Calculate Actual Balance Sheet.xml", HBR_REPO_RULE_XML)

    assert len(result.definitions) == 1
    definition = result.definitions[0]
    assert definition.rule_name == "Calculate Actual Balance Sheet"
    assert definition.cube_name == "Plan1"
    assert len(definition.prompts) == 1
    prompt = definition.prompts[0]
    assert prompt.name == "Years"
    assert prompt.label == "Years"
    assert prompt.value_type == "MEMBERS"
    assert prompt.dimension == "Years"
    assert prompt.required_at_launch is True
    assert prompt.allow_multiple is True
    assert prompt.scope_type == "APPLICATION"
    assert prompt.scope_name == "EPBCS"
    assert prompt.source_variable_id == "1001"
    assert prompt.limit_type == "expression"
    assert prompt.limit_value == 'DESCENDANTS("Years")'


def test_parser_preserves_all_hbrrepo_variable_scope_levels() -> None:
    definition = CalcManagerRTPParser().parse("scopes.xml", HBR_REPO_SCOPES_XML).definitions[0]

    assert [(item.name, item.scope_type) for item in definition.prompts] == [
        ("GlobalPrompt", "GLOBAL"),
        ("ApplicationPrompt", "APPLICATION"),
        ("CubePrompt", "CUBE"),
        ("RulePrompt", "RULE"),
    ]
    assert definition.prompts[1].default_value == "Forecast"
    assert definition.prompts[1].required_at_launch is False


def test_registry_imports_and_enforces_required_prompts(tmp_path: Path) -> None:
    registry = BusinessRuleRTPRegistryService(_settings(tmp_path))

    imported = registry.import_package("rules.xml", RULE_XML)
    definition = registry.get_definition("calculate revenue")

    assert imported.rules_imported == 1
    assert imported.prompts_imported == 2
    assert definition is not None
    assert definition.rule_name == "Calculate Revenue"
    assert registry.normalize_for_execution(
        "Calculate Revenue",
        {"year": "FY27"},
    ) == {"Year": "FY27"}
    with pytest.raises(BusinessRuleError, match="Planning Year"):
        registry.normalize_for_execution("Calculate Revenue", {})
    with pytest.raises(BusinessRuleError, match="not registered"):
        registry.normalize_for_execution(
            "Calculate Revenue",
            {"Year": "FY27", "Unknown": "Value"},
        )


def test_failed_import_preserves_last_known_good_definition(tmp_path: Path) -> None:
    registry = BusinessRuleRTPRegistryService(_settings(tmp_path))
    registry.import_package("rules.xml", RULE_XML)

    with pytest.raises(BusinessRuleError, match="No supported"):
        registry.import_package("empty.xml", b"<rules />")

    definition = registry.get_definition("Calculate Revenue")
    assert definition is not None
    assert [item.name for item in definition.prompts] == ["Year", "Scenario"]
    status = registry.status(
        live_rule_names=("Calculate Revenue", "No RTP Rule")
    )
    assert status.status == "ATTENTION"
    assert status.recent_syncs[0].status == "FAILED"
    assert status.synchronized_rule_count == 1
    assert status.unsynchronized_live_rules == ("No RTP Rule",)


def test_registry_reports_live_coverage_and_definition_changes(
    tmp_path: Path,
) -> None:
    registry = BusinessRuleRTPRegistryService(_settings(tmp_path))

    first = registry.import_package("rules.xml", RULE_XML)
    unchanged = registry.import_package("rules.xml", RULE_XML)
    changed_xml = RULE_XML.replace(b"Planning Year", b"Forecast Year")
    changed = registry.import_package("rules.xml", changed_xml)
    status = registry.status(
        live_rule_names=("Calculate Revenue", "No RTP Rule")
    )

    assert (first.rules_added, first.rules_changed, first.rules_unchanged) == (
        1,
        0,
        0,
    )
    assert (
        unchanged.rules_added,
        unchanged.rules_changed,
        unchanged.rules_unchanged,
    ) == (0, 0, 1)
    assert (
        changed.rules_added,
        changed.rules_changed,
        changed.rules_unchanged,
    ) == (0, 1, 0)
    assert status.status == "HEALTHY"
    assert status.live_rule_count == 2
    assert status.synchronized_rule_count == 1
    assert status.synchronized_prompt_count == 2
    assert status.unsynchronized_live_rules == ("No RTP Rule",)
    assert status.definitions_not_in_live_catalog == ()
    assert status.definitions[0].live_status == "SYNCHRONIZED"
    assert status.definitions[0].required_prompt_count == 1


def test_registry_flags_definitions_missing_from_live_catalog(
    tmp_path: Path,
) -> None:
    registry = BusinessRuleRTPRegistryService(_settings(tmp_path))
    registry.import_package("rules.xml", RULE_XML)

    status = registry.status(live_rule_names=("Renamed Revenue",))

    assert status.status == "ATTENTION"
    assert status.definitions_not_in_live_catalog == ("Calculate Revenue",)
    assert status.definitions[0].live_status == "NOT_IN_LIVE_CATALOG"


def test_agent_uses_the_same_registered_prompt_contract(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    registry = BusinessRuleRTPRegistryService(settings)
    registry.import_package("rules.xml", RULE_XML)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=object(),  # not used by this focused capability test
        data_review=object(),
        business_rule_rtps=registry,
    )

    guided = gateway.guided_input_definition(
        "business-rules",
        "Calculate Revenue",
    )
    normalized = gateway.normalize_guided_inputs(
        "business-rules",
        "Calculate Revenue",
        {
            "runtime_prompt_mode": "Provide runtime prompt values",
            "runtime_prompts": {"year": "FY27"},
        },
    )

    assert guided is not None
    context = guided["context"]["rtp_definition"]
    assert [item["name"] for item in context["prompts"]] == ["Year", "Scenario"]
    assert normalized == {"runtime_prompts": {"Year": "FY27"}}


class _RuleCatalog:
    def discover_job_names(self, *, job_type: str):
        assert job_type == "RULES"
        return ("Calculate Revenue",)

    def discover_registered(self):
        return type("Catalog", (), {"pipelines": (), "data_integrations": ()})()


class _RuleProvider:
    provider_name = "test"
    model_name = "test"

    def generate(self, **_):
        return AgentProviderTurn(
            text="",
            tool_calls=(
                AgentToolCall(
                    name="prepare_operation_action",
                    arguments={
                        "operation_code": "business-rules",
                        "objective": "Calculate revenue.",
                    },
                    call_id="prepare-rule",
                ),
            ),
        )


def test_agent_selection_collects_required_rtps_before_validation(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    registry = BusinessRuleRTPRegistryService(settings)
    registry.import_package("rules.xml", RULE_XML)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=object(),
        data_review=object(),
        operation_catalog=_RuleCatalog(),
        business_rule_rtps=registry,
    )
    provider = _RuleProvider()
    graph = AgentGraphOrchestrator(
        provider_factory=lambda: provider,
        gateway=gateway,
        checkpointer=AgentCheckpointStore(settings.database_target),
        system_instruction="Read only.",
        max_tool_rounds=2,
        environment_key="example|Vision",
    )
    conversation_id = "registered-rtp-selection"
    initial = graph.invoke(
        conversation_id=conversation_id,
        user_id=7,
        messages=(
            AgentMessage(
                message_id=1,
                conversation_id=conversation_id,
                role=AgentMessageRole.USER,
                content="Prepare a business rule.",
                created_at=datetime.now(UTC),
            ),
        ),
    )

    selected = graph.resume_clarification(
        conversation_id=conversation_id,
        user_id=7,
        request_id=initial.clarification_request.request_id,
        value="Calculate Revenue",
    )

    assert selected.input_request is not None
    assert selected.input_request.artifact_name == "Calculate Revenue"
    prompt_context = selected.input_request.context["rtp_definition"]["prompts"]
    assert [item["name"] for item in prompt_context] == ["Year", "Scenario"]

    approval = graph.resume_input(
        conversation_id=conversation_id,
        user_id=7,
        request_id=selected.input_request.request_id,
        values={
            "runtime_prompt_mode": "Provide runtime prompt values",
            "runtime_prompts": {"Year": "FY27"},
        },
    )

    assert approval.approval_request is not None
    assert approval.approval_request.input_values == {
        "runtime_prompts": {"Year": "FY27"}
    }


def test_agent_prefills_only_explicit_registered_rtp_values() -> None:
    definition = {
        "prompts": [
            {"name": "Year", "label": "Planning Year", "dimension": "Year"},
            {"name": "Scenario", "label": "Scenario", "dimension": "Scenario"},
        ]
    }

    values = AgentGraphOrchestrator._business_rule_rtp_prefill(
        "Run Calculate Revenue for FY27 and Scenario: Forecast",
        definition,
    )

    assert values == {"Year": "FY27", "Scenario": "Forecast"}


@pytest.mark.parametrize(
    ("xml", "expected_prefill"),
    (
        (SINGLE_RTP_XML, {"Amount": "10"}),
        (RULE_XML, None),
    ),
)
def test_rule_selection_prefills_only_unambiguous_unnamed_rtp(
    tmp_path: Path,
    xml: bytes,
    expected_prefill: dict[str, str] | None,
) -> None:
    settings = _settings(tmp_path)
    registry = BusinessRuleRTPRegistryService(settings)
    registry.import_package("rules.xml", xml)
    gateway = AgentCapabilityGateway(
        settings,
        control_center=object(),
        data_review=object(),
        operation_catalog=_RuleCatalog(),
        business_rule_rtps=registry,
    )
    graph = AgentGraphOrchestrator(
        provider_factory=_RuleProvider,
        gateway=gateway,
        checkpointer=AgentCheckpointStore(settings.database_target),
        system_instruction="Read only.",
        max_tool_rounds=2,
        environment_key="example|Vision",
    )
    conversation_id = "unnamed-rtp-selection"
    message = AgentMessage(
        message_id=1,
        conversation_id=conversation_id,
        role=AgentMessageRole.USER,
        content="run revenue rule with rtp 10",
        created_at=datetime.now(UTC),
    )
    initial = graph.invoke(
        conversation_id=conversation_id,
        user_id=7,
        messages=(message,),
        task_context=AgentTaskInterpreter.interpret((message,)).to_payload(),
    )
    assert initial.clarification_request is not None
    selected = graph.resume_clarification(
        conversation_id=conversation_id,
        user_id=7,
        request_id=initial.clarification_request.request_id,
        value="Calculate Revenue",
    )
    assert selected.input_request is not None
    if expected_prefill is None:
        assert selected.input_request.context["prefill"] == {
            "runtime_prompt_mode": "Provide runtime prompt values",
        }
        assert "could not safely identify" in selected.input_request.description
    else:
        assert selected.input_request.context["prefill"] == {
            "runtime_prompt_mode": "Provide runtime prompt values",
            "runtime_prompts": expected_prefill,
        }


@pytest.mark.parametrize(
    ("prompt", "expected"),
    (
        ("Run revenue rule with rtp 10", {"Amount": "10"}),
        ("Run revenue rule with runtime prompt value 10", {"Amount": "10"}),
        ("Run revenue rule with Amount 10", {"Amount": "10"}),
        ("Run revenue rule with Amount=10", {"Amount": "10"}),
    ),
)
def test_single_registered_rtp_accepts_named_or_unnamed_prompt_value(
    prompt: str,
    expected: dict[str, str],
) -> None:
    definition = {
        "rule_name": "Revenue Rule",
        "prompts": [{"name": "Amount", "label": "Amount", "hidden": False}],
    }
    assert AgentGraphOrchestrator._business_rule_rtp_prefill(
        prompt, definition
    ) == expected


def test_unnamed_rtp_is_not_guessed_when_rule_has_multiple_prompts() -> None:
    definition = {
        "prompts": [
            {"name": "Amount", "hidden": False},
            {"name": "Scenario", "hidden": False},
        ]
    }
    assert AgentGraphOrchestrator._business_rule_rtp_prefill(
        "Run revenue rule with rtp 10", definition
    ) == {}


def test_unregistered_rule_prefills_only_explicit_rtp_name_value_pairs() -> None:
    assert AgentGraphOrchestrator._business_rule_rtp_prefill(
        "Run revenue rule with RTP Amount=10 and Scenario=Actual", {}
    ) == {"Amount": "10", "Scenario": "Actual"}
    assert AgentGraphOrchestrator._business_rule_rtp_prefill(
        "Run revenue rule with rtp 10", {}
    ) == {}
    assert AgentGraphOrchestrator._unnamed_business_rule_rtp_value(
        "Run revenue rule with RTP Amount=10"
    ) is None

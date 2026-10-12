"""Governed FCCS consolidation through Oracle's supported Jobs REST API."""

from __future__ import annotations

from dataclasses import dataclass

from app.clients.epm_client import EPMClient
from app.services.business_rule_service import BusinessRuleService
from app.services.job_service import JobService
from app.utils.exceptions import EPMError, OperationError


@dataclass(frozen=True, slots=True)
class FCCSConsolidationInput:
    """One reviewed FCCS consolidation point of view."""

    rule_name: str
    scenario: str
    year: str
    period: str
    entity: str


@dataclass(frozen=True, slots=True)
class FCCSConsolidationSubmission:
    """Oracle job accepted for asynchronous consolidation monitoring."""

    job_id: int
    rule_name: str
    scenario: str
    year: str
    period: str
    entity: str


class FCCSConsolidationService:
    """Allow only live-discovered FCCS consolidation rules to execute."""

    _JOB_TYPE = "RULES"

    def __init__(self, client: EPMClient) -> None:
        self._client = client

    @classmethod
    def normalize(
        cls,
        operation: FCCSConsolidationInput,
    ) -> FCCSConsolidationInput:
        def required(value: object, label: str, *, limit: int = 250) -> str:
            normalized = " ".join(str(value or "").split())
            if not normalized or len(normalized) > limit:
                raise OperationError(f"FCCS consolidation {label} is invalid.")
            if any(ord(character) < 32 for character in normalized):
                raise OperationError(f"FCCS consolidation {label} is invalid.")
            return normalized

        rule_name = required(operation.rule_name, "rule name")
        if "consolidat" not in rule_name.casefold():
            raise OperationError(
                "The selected FCCS rule is not identified as a consolidation rule."
            )
        entity = required(operation.entity, "entity", limit=1_000)
        if any(not item.strip() for item in entity.split(",")):
            raise OperationError(
                "FCCS consolidation entities must be exact names separated by commas."
            )
        return FCCSConsolidationInput(
            rule_name=rule_name,
            scenario=required(operation.scenario, "scenario"),
            year=required(operation.year, "year"),
            period=required(operation.period, "period"),
            entity=entity,
        )

    def start(
        self,
        operation: FCCSConsolidationInput,
    ) -> FCCSConsolidationSubmission:
        """Recheck the rule catalog, submit, and return the Oracle job ID."""

        command = self.normalize(operation)
        try:
            definitions = JobService(self._client).get_job_definitions(
                job_type=self._JOB_TYPE
            )
        except EPMError as exc:
            raise OperationError(
                "The live FCCS rule catalog could not be verified before execution."
            ) from exc
        canonical = next(
            (
                item.job_name
                for item in definitions
                if item.job_name.casefold() == command.rule_name.casefold()
                and "consolidat" in item.job_name.casefold()
            ),
            None,
        )
        if canonical is None:
            raise OperationError(
                f"FCCS consolidation rule '{command.rule_name}' is no longer "
                "available. Refresh the catalog and review the action again."
            )

        submission = BusinessRuleService(self._client).start_rule(
            canonical,
            runtime_prompts={
                "Scenario": command.scenario,
                "Year": command.year,
                "Period": command.period,
                "Entity": command.entity,
            },
        )
        return FCCSConsolidationSubmission(
            job_id=submission.job_id,
            rule_name=canonical,
            scenario=command.scenario,
            year=command.year,
            period=command.period,
            entity=command.entity,
        )

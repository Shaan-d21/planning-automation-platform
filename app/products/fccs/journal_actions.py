"""Governed FCCS journal workflow actions through documented Oracle REST."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import quote

from app.clients.epm_client import EPMClient
from app.products.fccs.read_service import FCCSReadService
from app.utils.exceptions import APIRequestError, OperationError


class FCCSJournalAction(StrEnum):
    SUBMIT = "SUBMIT"
    APPROVE = "APPROVE"
    POST = "POST"
    UNPOST = "UNPOST"
    REJECT = "REJECT"


@dataclass(frozen=True, slots=True)
class FCCSJournalActionInput:
    journal_label: str
    scenario: str
    year: str
    period: str
    action: FCCSJournalAction
    consolidation: str | None = None
    expected_status: str | None = None


class FCCSJournalActionService:
    """Validate live journal identity, then perform one approved action."""

    def __init__(self, client: EPMClient) -> None:
        self._client = client
        application = quote(client.application_name, safe="")
        self._endpoint = (
            f"{client.planning_api_root}/applications/{application}/journals"
        )

    @staticmethod
    def normalize(operation: FCCSJournalActionInput) -> FCCSJournalActionInput:
        def required(value: object, label: str) -> str:
            normalized = " ".join(str(value or "").split())
            if not normalized or len(normalized) > 250:
                raise OperationError(f"FCCS journal {label} is invalid.")
            return normalized

        try:
            action = FCCSJournalAction(str(operation.action).strip().upper())
        except ValueError as exc:
            raise OperationError(
                "FCCS journal action must be SUBMIT, APPROVE, POST, UNPOST, or REJECT."
            ) from exc
        consolidation = " ".join(str(operation.consolidation or "").split()) or None
        expected_status = " ".join(str(operation.expected_status or "").split()) or None
        return FCCSJournalActionInput(
            journal_label=required(operation.journal_label, "label"),
            scenario=required(operation.scenario, "scenario"),
            year=required(operation.year, "year"),
            period=required(operation.period, "period"),
            action=action,
            consolidation=consolidation,
            expected_status=expected_status,
        )

    def execute(self, operation: FCCSJournalActionInput) -> dict[str, object]:
        command = self.normalize(operation)
        current = FCCSReadService(self._client).get_journal_detail(
            command.journal_label,
            scenario=command.scenario,
            year=command.year,
            period=command.period,
            consolidation=command.consolidation,
            include_line_items=False,
        ).journal
        if command.expected_status and (
            str(current.status or "").casefold()
            != command.expected_status.casefold()
        ):
            raise OperationError(
                f"Journal '{command.journal_label}' changed from reviewed status "
                f"'{command.expected_status}' to '{current.status or 'unknown'}'. "
                "Review it again before approval."
            )
        payload: dict[str, str] = {
            "scenario": command.scenario,
            "year": command.year,
            "period": command.period,
            "action": command.action.value,
        }
        if command.consolidation:
            payload["consolidation"] = command.consolidation
        response = self._client.post(
            f"{self._endpoint}/{quote(command.journal_label, safe='')}/actions",
            payload=payload,
        )
        if not isinstance(response, Mapping):
            raise APIRequestError(
                "Oracle FCCS returned an unexpected journal action response."
            )
        return {
            "target_name": command.journal_label,
            "action": command.action.value,
            "scenario": command.scenario,
            "year": command.year,
            "period": command.period,
            "consolidation": command.consolidation,
            "previous_status": current.status,
            "status": str(
                response.get("actionStatus")
                or response.get("status")
                or "Submitted"
            ),
            "details": str(response.get("details") or "").strip() or None,
            "engine": "rest",
        }

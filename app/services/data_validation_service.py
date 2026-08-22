"""Form-based validation for data moved between Planning cubes."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from app.clients.epm_client import EPMClient
from app.models.data_validation import (
    DataComparisonCell,
    DataQualityIssue,
    DataQualityResult,
    DataQualityRules,
    DataMismatch,
    DataValidationResult,
    FormGrid,
)
from app.services.form_service import PlanningFormService
from app.utils.exceptions import DataValidationError


class DataValidationService:
    """Export two form slices and compare their corresponding data cells."""

    _MISSING_VALUES = {"", "#missing", "missing", "none", "null"}
    _TECHNICAL_RELATIVE_EPSILON = Decimal("1E-12")

    def __init__(
        self,
        client: EPMClient,
        *,
        logger: logging.Logger | None = None,
        form_service: PlanningFormService | None = None,
    ) -> None:
        self._client = client
        self._logger = logger or logging.getLogger(__name__)
        self._form_service = form_service

    def export_form(
        self,
        form_name: str,
        *,
        page_members: Sequence[str] = (),
        filter_members: Sequence[str] = (),
    ) -> FormGrid:
        """Export a Planning form as a normalized JSON grid."""
        if self._form_service is None:
            self._form_service = PlanningFormService(
                self._client,
                logger=self._logger,
            )
        return self._form_service.export_form(
            form_name,
            page_members=page_members,
            filter_members=filter_members,
        )

    def compare_forms(
        self,
        source_form: str,
        target_form: str,
        *,
        page_members: Sequence[str] = (),
        filter_members: Sequence[str] = (),
        tolerance: Decimal | str | float = Decimal("0"),
        max_mismatches: int = 100,
        include_cells: bool = False,
    ) -> DataValidationResult:
        """Compare corresponding source and target cells within a tolerance."""
        source = self.export_form(
            source_form,
            page_members=page_members,
            filter_members=filter_members,
        )
        target = self.export_form(
            target_form,
            page_members=page_members,
            filter_members=filter_members,
        )
        return self.compare_grids(
            source,
            target,
            source_form=source_form,
            target_form=target_form,
            tolerance=tolerance,
            max_mismatches=max_mismatches,
            include_cells=include_cells,
        )

    def compare_grids(
        self,
        source: FormGrid,
        target: FormGrid,
        *,
        source_form: str,
        target_form: str,
        tolerance: Decimal | str | float = Decimal("0"),
        max_mismatches: int = 100,
        include_cells: bool = False,
    ) -> DataValidationResult:
        """Compare two previously exported grids without another API call."""
        normalized_tolerance = self._normalize_tolerance(tolerance)
        if max_mismatches <= 0:
            raise ValueError("max_mismatches must be greater than zero.")
        self._validate_layout(source, target)

        mismatches: list[DataMismatch] = []
        cells: list[DataComparisonCell] = []
        compared_cells = 0
        matched_cells = 0
        for source_row, target_row in zip(
            source.rows,
            target.rows,
            strict=True,
        ):
            if source_row.headers != target_row.headers:
                raise DataValidationError(
                    "Source and target validation forms returned different "
                    "row member layouts."
                )
            for index, (source_raw, target_raw) in enumerate(
                zip(source_row.data, target_row.data, strict=True)
            ):
                compared_cells += 1
                source_value = self._decimal_or_missing(source_raw)
                target_value = self._decimal_or_missing(target_raw)
                difference = self._difference(source_value, target_value)
                matches = self._values_match(
                    source_value,
                    target_value,
                    difference,
                    normalized_tolerance,
                )
                if include_cells:
                    cells.append(
                        DataComparisonCell(
                            row_headers=source_row.headers,
                            column_headers=source.columns[index],
                            source_value=source_value,
                            target_value=target_value,
                            difference=difference,
                            matches=matches,
                        )
                    )
                if matches:
                    matched_cells += 1
                elif len(mismatches) < max_mismatches:
                    mismatches.append(
                        DataMismatch(
                            row_headers=source_row.headers,
                            column_headers=source.columns[index],
                            source_value=source_value,
                            target_value=target_value,
                            difference=difference,
                        )
                    )

        result = DataValidationResult(
            source_form=str(source_form).strip(),
            target_form=str(target_form).strip(),
            compared_cells=compared_cells,
            matched_cells=matched_cells,
            mismatches=tuple(mismatches),
            tolerance=normalized_tolerance,
            cells=tuple(cells),
        )
        self._logger.info(
            "Data validation completed: source_form='%s', "
            "target_form='%s', compared=%s, mismatches=%s.",
            result.source_form,
            result.target_form,
            result.compared_cells,
            result.compared_cells - result.matched_cells,
        )
        return result

    def validate_grid(
        self,
        grid: FormGrid,
        rules: DataQualityRules,
    ) -> DataQualityResult:
        """Apply bounded, read-only quality checks to every grid cell."""
        self._validate_quality_rules(rules)
        issues: list[DataQualityIssue] = []
        counts = {
            "MISSING": 0,
            "ZERO": 0,
            "BELOW_MINIMUM": 0,
            "ABOVE_MAXIMUM": 0,
            "NON_NUMERIC": 0,
        }
        error_count = 0
        warning_count = 0
        checked_cells = 0
        numeric_checks = (
            rules.check_zero
            or rules.minimum is not None
            or rules.maximum is not None
        )
        for row in grid.rows:
            for index, raw_value in enumerate(row.data):
                checked_cells += 1
                issue = self._quality_issue(
                    grid,
                    row.headers,
                    grid.columns[index],
                    raw_value,
                    rules,
                    numeric_checks=numeric_checks,
                )
                if issue is None:
                    continue
                counts[issue.code] += 1
                if issue.severity == "ERROR":
                    error_count += 1
                else:
                    warning_count += 1
                if len(issues) < rules.max_issues:
                    issues.append(issue)
        issue_count = error_count + warning_count
        status = "FAIL" if error_count else "WARNING" if warning_count else "PASS"
        result = DataQualityResult(
            status=status,
            checked_cells=checked_cells,
            passed_cells=checked_cells - issue_count,
            issue_count=issue_count,
            missing_count=counts["MISSING"],
            zero_count=counts["ZERO"],
            below_minimum_count=counts["BELOW_MINIMUM"],
            above_maximum_count=counts["ABOVE_MAXIMUM"],
            non_numeric_count=counts["NON_NUMERIC"],
            issues=tuple(issues),
            truncated=issue_count > len(issues),
            rules=rules,
        )
        self._logger.info(
            "Data quality validation completed: checked=%s, issues=%s, "
            "status='%s'.",
            result.checked_cells,
            result.issue_count,
            result.status,
        )
        return result

    @classmethod
    def _quality_issue(
        cls,
        grid: FormGrid,
        row_headers: tuple[str, ...],
        column_headers: tuple[str, ...],
        raw_value: Any,
        rules: DataQualityRules,
        *,
        numeric_checks: bool,
    ) -> DataQualityIssue | None:
        normalized = "" if raw_value is None else str(raw_value).strip()
        if normalized.casefold() in cls._MISSING_VALUES:
            if not rules.check_missing:
                return None
            return DataQualityIssue(
                code="MISSING",
                severity="ERROR",
                message="The selected Planning intersection has no data.",
                pov=grid.pov,
                row_headers=row_headers,
                column_headers=column_headers,
                raw_value=raw_value,
                numeric_value=None,
            )
        if not numeric_checks:
            return None
        try:
            numeric_value = Decimal(normalized.replace(",", ""))
        except InvalidOperation:
            return DataQualityIssue(
                code="NON_NUMERIC",
                severity="ERROR",
                message=(
                    "A numeric validation rule was applied to a non-numeric "
                    "Planning value."
                ),
                pov=grid.pov,
                row_headers=row_headers,
                column_headers=column_headers,
                raw_value=raw_value,
                numeric_value=None,
            )
        if rules.minimum is not None and numeric_value < rules.minimum:
            return DataQualityIssue(
                code="BELOW_MINIMUM",
                severity="ERROR",
                message=f"Value is below the minimum of {rules.minimum}.",
                pov=grid.pov,
                row_headers=row_headers,
                column_headers=column_headers,
                raw_value=raw_value,
                numeric_value=numeric_value,
            )
        if rules.maximum is not None and numeric_value > rules.maximum:
            return DataQualityIssue(
                code="ABOVE_MAXIMUM",
                severity="ERROR",
                message=f"Value exceeds the maximum of {rules.maximum}.",
                pov=grid.pov,
                row_headers=row_headers,
                column_headers=column_headers,
                raw_value=raw_value,
                numeric_value=numeric_value,
            )
        if rules.check_zero and numeric_value == 0:
            return DataQualityIssue(
                code="ZERO",
                severity="WARNING",
                message="The selected Planning intersection contains zero.",
                pov=grid.pov,
                row_headers=row_headers,
                column_headers=column_headers,
                raw_value=raw_value,
                numeric_value=numeric_value,
            )
        return None

    @staticmethod
    def _validate_quality_rules(rules: DataQualityRules) -> None:
        if rules.max_issues <= 0:
            raise DataValidationError(
                "Maximum validation issues must be greater than zero."
            )
        if (
            rules.minimum is not None
            and rules.maximum is not None
            and rules.minimum > rules.maximum
        ):
            raise DataValidationError(
                "Validation minimum cannot be greater than maximum."
            )

    @staticmethod
    def _validate_layout(source: FormGrid, target: FormGrid) -> None:
        if (
            source.row_dimensions != target.row_dimensions
            or source.column_dimensions != target.column_dimensions
            or source.columns != target.columns
            or len(source.rows) != len(target.rows)
        ):
            raise DataValidationError(
                "Source and target validation forms do not have matching "
                "row and column layouts."
            )

    @classmethod
    def _decimal_or_missing(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        normalized = str(value).strip().replace(",", "")
        if normalized.casefold() in cls._MISSING_VALUES:
            return None
        try:
            return Decimal(normalized)
        except InvalidOperation as exc:
            raise DataValidationError(
                f"Validation encountered non-numeric data value '{value}'."
            ) from exc

    @staticmethod
    def _difference(
        source: Decimal | None,
        target: Decimal | None,
    ) -> Decimal | None:
        if source is None or target is None:
            return None
        return source - target

    @classmethod
    def _values_match(
        cls,
        source: Decimal | None,
        target: Decimal | None,
        difference: Decimal | None,
        tolerance: Decimal,
    ) -> bool:
        """Compare business values while ignoring numeric transport noise."""
        if source is None or target is None:
            return source is None and target is None
        if difference is None:
            return False
        technical_epsilon = max(
            abs(source),
            abs(target),
            Decimal("1"),
        ) * cls._TECHNICAL_RELATIVE_EPSILON
        return abs(difference) <= max(tolerance, technical_epsilon)

    @staticmethod
    def _normalize_tolerance(value: Decimal | str | float) -> Decimal:
        try:
            tolerance = Decimal(str(value))
        except InvalidOperation as exc:
            raise DataValidationError(
                "Validation tolerance must be numeric."
            ) from exc
        if tolerance < 0:
            raise DataValidationError(
                "Validation tolerance cannot be negative."
            )
        return tolerance

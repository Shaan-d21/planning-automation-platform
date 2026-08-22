"""Format-agnostic models for Oracle EPM Data Integration runs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath

from app.utils.exceptions import DataIntegrationError

MONTH_NAMES = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
_MONTH_LOOKUP = {
    month.casefold(): index
    for index, month in enumerate(MONTH_NAMES, start=1)
}
_MONTH_PERIOD_PATTERN = re.compile(
    r"^(?P<month>[A-Za-z]{3})-(?P<year>\d{2}|\d{4})$"
)


@dataclass(frozen=True, slots=True)
class DataIntegrationFileReference:
    """A validated Oracle file reference used by Data Integration.

    Oracle distinguishes the Planning Applications Inbox/Outbox repository
    (``#epminbox``) from the Data Integration home directory (``inbox``).
    The reference therefore must retain its location prefix.
    """

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "value",
            self._normalize(self.value),
        )

    @classmethod
    def from_default_upload(
        cls,
        file_name: str,
    ) -> DataIntegrationFileReference:
        """Reference a file uploaded to the default Applications Inbox."""
        normalized_name = PurePath(file_name).name.strip()
        if not normalized_name:
            raise DataIntegrationError(
                "Uploaded Data Integration filename cannot be empty."
            )
        return cls(f"#epminbox/{normalized_name}")

    @classmethod
    def from_existing(
        cls,
        value: str,
    ) -> DataIntegrationFileReference:
        """Preserve an explicit existing Oracle Inbox reference."""
        return cls(value)

    @staticmethod
    def _normalize(value: str) -> str:
        normalized = str(value).strip().replace("\\", "/")
        if not normalized:
            raise DataIntegrationError(
                "Data Integration file reference cannot be empty."
            )
        if normalized.casefold().startswith("epminbox/"):
            normalized = f"#{normalized}"
        if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
            raise DataIntegrationError(
                "Data Integration file reference must be relative to an "
                "Oracle Inbox."
            )

        comparison = (
            normalized[1:]
            if normalized.startswith("#")
            else normalized
        )
        segments = comparison.split("/")
        if any(segment in {"", ".", ".."} for segment in segments):
            raise DataIntegrationError(
                "Data Integration file reference contains an invalid path."
            )
        if normalized.startswith("#") and not normalized.casefold().startswith(
            "#epminbox/"
        ):
            raise DataIntegrationError(
                "The only supported Oracle repository prefix is "
                "#epminbox/."
            )
        return normalized

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class DataIntegrationPeriodRange:
    """One Oracle Data Integration period or an inclusive period range.

    Period values are treated as Oracle configuration values. Standard
    ``Mon-YY`` names receive additional chronological validation, while
    Planning member notation and other administrator-defined Data Integration
    names are passed through without Python trying to interpret them.
    """

    start_period: str
    end_period: str
    expected_period_count: int | None = None

    def __post_init__(self) -> None:
        normalized_start = self._normalize_period_name(self.start_period)
        normalized_end = self._normalize_period_name(self.end_period)
        object.__setattr__(self, "start_period", normalized_start)
        object.__setattr__(self, "end_period", normalized_end)

        start_month = self._parse_month_period(normalized_start)
        end_month = self._parse_month_period(normalized_end)
        if (
            start_month is not None
            and end_month is not None
            and self._month_index(end_month) < self._month_index(start_month)
        ):
            raise DataIntegrationError(
                "The end period cannot be earlier than the start period."
            )

        if self.expected_period_count is not None:
            if self.expected_period_count <= 0:
                raise DataIntegrationError(
                    "Expected period count must be greater than zero."
                )
            detected_count = self.period_count
            if (
                detected_count is not None
                and detected_count != self.expected_period_count
            ):
                raise DataIntegrationError(
                    f"This configured integration profile expects "
                    f"{self.expected_period_count} periods; the selected "
                    f"range contains {detected_count}."
                )

    @classmethod
    def from_user_values(
        cls,
        start_month: str | int,
        end_month: str | int,
        year: str | int,
        *,
        expected_period_count: int | None = None,
    ) -> DataIntegrationPeriodRange:
        """Create a range from month names/numbers and a two/four-digit year."""
        normalized_year = cls._normalize_year(year)
        return cls(
            start_period=(
                f"{MONTH_NAMES[cls._parse_month(start_month) - 1]}-"
                f"{normalized_year}"
            ),
            end_period=(
                f"{MONTH_NAMES[cls._parse_month(end_month) - 1]}-"
                f"{normalized_year}"
            ),
            expected_period_count=expected_period_count,
        )

    @classmethod
    def from_period_names(
        cls,
        start_period: str,
        end_period: str,
        *,
        expected_period_count: int | None = None,
    ) -> DataIntegrationPeriodRange:
        """Create a range from exact period names configured in Oracle."""
        return cls(
            start_period=start_period,
            end_period=end_period,
            expected_period_count=expected_period_count,
        )

    @property
    def period_count(self) -> int | None:
        """Return a count for standard monthly names, otherwise ``None``."""
        start = self._parse_month_period(self.start_period)
        end = self._parse_month_period(self.end_period)
        if start is None or end is None:
            return None
        return self._month_index(end) - self._month_index(start) + 1

    @property
    def oracle_period_name(self) -> str:
        """Return the curly-brace period expression required by Oracle."""
        if self.start_period == self.end_period:
            return f"{{{self.start_period}}}"
        return f"{{{self.start_period}}}{{{self.end_period}}}"

    @property
    def periods(self) -> tuple[str, ...]:
        """Return standard monthly periods, or the supplied range endpoints."""
        start = self._parse_month_period(self.start_period)
        end = self._parse_month_period(self.end_period)
        if start is None or end is None:
            if self.start_period == self.end_period:
                return (self.start_period,)
            return (self.start_period, self.end_period)

        start_index = self._month_index(start)
        end_index = self._month_index(end)
        periods: list[str] = []
        for index in range(start_index, end_index + 1):
            year, zero_based_month = divmod(index, 12)
            periods.append(
                f"{MONTH_NAMES[zero_based_month]}-{year % 100:02d}"
            )
        return tuple(periods)

    @staticmethod
    def _parse_month(value: str | int) -> int:
        if isinstance(value, int) or str(value).strip().isdigit():
            month = int(value)
            if month in range(1, 13):
                return month
            raise DataIntegrationError("Month must be between 1 and 12.")

        normalized = str(value).strip().casefold()
        try:
            return _MONTH_LOOKUP[normalized]
        except KeyError as exc:
            raise DataIntegrationError(
                "Month must be a number from 1 to 12 or a three-letter name."
            ) from exc

    @staticmethod
    def _normalize_year(value: str | int) -> str:
        normalized = str(value).strip()
        if not normalized.isdigit() or len(normalized) not in {2, 4}:
            raise DataIntegrationError(
                "Year must contain two or four digits, such as 19 or 2019."
            )
        return normalized[-2:]

    @staticmethod
    def _normalize_period_name(value: str) -> str:
        normalized = str(value).strip()
        if (
            len(normalized) >= 2
            and normalized.startswith("{")
            and normalized.endswith("}")
        ):
            normalized = normalized[1:-1].strip()
        if not normalized:
            raise DataIntegrationError("Period name cannot be empty.")
        if any(character in normalized for character in "{}\r\n"):
            raise DataIntegrationError(
                "Enter one period name without curly braces."
            )
        if len(normalized) > 100:
            raise DataIntegrationError(
                "Period name cannot exceed 100 characters."
            )
        return normalized

    @classmethod
    def _parse_month_period(
        cls,
        value: str,
    ) -> tuple[int, int] | None:
        match = _MONTH_PERIOD_PATTERN.fullmatch(value)
        if match is None:
            return None
        try:
            month = _MONTH_LOOKUP[match.group("month").casefold()]
        except KeyError:
            return None
        year_value = match.group("year")
        year = int(year_value)
        if len(year_value) == 2:
            year += 2000
        return year, month

    @staticmethod
    def _month_index(value: tuple[int, int]) -> int:
        year, month = value
        return year * 12 + month - 1


@dataclass(frozen=True, slots=True)
class DataIntegrationSubmission:
    """Result returned after submitting a Data Integration REST job."""

    job_id: int
    integration_name: str
    file_name: str | None
    period_name: str
    import_mode: str
    export_mode: str


@dataclass(frozen=True, slots=True)
class DataIntegrationCommandResult:
    """Successful Data Integration result from EPM Automate."""

    integration_name: str
    file_name: str
    period_name: str
    import_mode: str
    export_mode: str
    replaced_existing: bool
    command_output: str

"""Read-only Planning form grids and source-to-target reconciliation."""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from math import prod
from threading import RLock
from time import monotonic

from app.clients.epm_client import EPMClient
from app.config.settings import Settings
from app.models.data_validation import (
    DataQualityResult,
    DataQualityRules,
    DataValidationResult,
    FormGrid,
    FormLayout,
)
from app.models.environment import DimensionInfo, MemberInfo, PlanTypeInfo
from app.models.report import DataSliceReportDefinition, ReportAxisSegment
from app.services.application_service import ApplicationService
from app.services.data_slice_report_service import DataSliceReportService
from app.services.data_validation_service import DataValidationService
from app.services.form_service import PlanningFormService
from app.services.report_catalog_service import ReportCatalogService
from app.utils.exceptions import APIRequestError, DataValidationError


@dataclass(frozen=True, slots=True)
class DataReviewCube:
    """One selectable Planning cube."""

    name: str
    cube_name: str
    cube_type: int | None
    dimension_count: int | None


@dataclass(frozen=True, slots=True)
class DataReviewSelection:
    """One form slice requested by the browser."""

    cube: str
    form_name: str
    page_member_overrides: Mapping[str, str]
    filter_members: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DataReviewAxisSelection:
    """One row or column dimension and its requested members."""

    dimension: str
    members: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DataReviewSliceSelection:
    """One ad-hoc, read-only cube slice."""

    cube: str
    pov: Mapping[str, str]
    rows: tuple[DataReviewAxisSelection, ...]
    columns: tuple[DataReviewAxisSelection, ...]


@dataclass(frozen=True, slots=True)
class DataReviewLayout:
    """Validated form layout associated with one selected cube."""

    cube: str
    form_name: str
    page_dimensions: tuple[str, ...]
    row_dimensions: tuple[str, ...]
    column_dimensions: tuple[str, ...]
    current_pov: tuple[tuple[str, str], ...]
    allowed_page_members: tuple[tuple[str, tuple[str, ...]], ...]


@dataclass(frozen=True, slots=True)
class DataReviewGrid:
    """Normalized grid with lightweight presentation metrics."""

    cube: str
    form_name: str
    grid: FormGrid
    row_count: int
    column_count: int
    cell_count: int
    missing_cell_count: int


@dataclass(frozen=True, slots=True)
class DataReviewMemberSearch:
    """A bounded, searchable view of one live dimension hierarchy."""

    cube: str
    dimension: str
    query: str
    members: tuple[MemberInfo, ...]
    total_matches: int
    has_more: bool
    offset: int = 0
    limit: int = 40


@dataclass(frozen=True, slots=True)
class DataReviewComparison:
    """Two reviewed slices and their reconciliation result."""

    source_cube: str
    target_cube: str
    result: DataValidationResult


@dataclass(frozen=True, slots=True)
class DataReviewQualityValidation:
    """Quality-check result tied to the reviewed Oracle cube slice."""

    cube: str
    form_name: str
    result: DataQualityResult


class DataReviewWorkspaceService:
    """Provide read-only form inspection without storing financial data."""

    _MISSING_VALUES = {"", "#missing", "missing", "none", "null"}
    _MAX_REQUESTED_CELLS = 250_000
    _MAX_MEMBER_SEARCH_LIMIT = 100
    _MEMBER_CACHE_TTL_SECONDS = 300.0
    _MEMBER_CACHE_MAX_ENTRIES = 32

    def __init__(
        self,
        settings: Settings,
        *,
        logger: logging.Logger | None = None,
        report_catalog_service: ReportCatalogService | None = None,
    ) -> None:
        self._settings = settings
        self._logger = logger or logging.getLogger(__name__)
        self._report_catalog = (
            report_catalog_service or ReportCatalogService()
        )
        self._member_cache: OrderedDict[
            tuple[str, str], tuple[float, tuple[MemberInfo, ...]]
        ] = OrderedDict()
        self._member_cache_lock = RLock()

    def list_cubes(self) -> tuple[DataReviewCube, ...]:
        """Return cubes visible to the configured Oracle identity."""
        with self._client() as client:
            try:
                cubes = tuple(
                    self._cube(item)
                    for item in ApplicationService(client).get_plan_types()
                )
            except APIRequestError as exc:
                compatibility_failure = exc.status_code in {
                    400,
                    404,
                    405,
                    501,
                }
                if client.is_cloud_environment:
                    if exc.status_code == 404:
                        raise DataValidationError(
                            "Oracle did not expose any Planning cubes through "
                            "Get Plan Types, substitution-variable scopes, or "
                            "job definitions for this application and user."
                        ) from exc
                    raise
                if not compatibility_failure:
                    raise
                self._logger.warning(
                    "Oracle Get Plan Types is unavailable; using cubes "
                    "from registered Data Review definitions."
                )
                cubes = ()
        if self._settings.resolved_deployment_mode == "cloud":
            if not cubes:
                raise DataValidationError(
                    "Oracle Cloud returned no Planning cubes for the "
                    "configured application and user."
                )
            return cubes
        if cubes:
            # Saved views are not yet environment-scoped. Once live on-prem
            # discovery succeeds, do not mix cube names saved for a previous
            # Oracle application into the current environment's picker.
            return tuple(
                sorted(cubes, key=lambda item: item.name.casefold())
            )
        configured = tuple(
            DataReviewCube(
                name=name,
                cube_name=name,
                cube_type=None,
                dimension_count=None,
            )
            for name in sorted(
                {
                    definition.cube
                    for definition in self._registered_definitions()
                },
                key=str.casefold,
            )
        )
        if not configured:
            raise DataValidationError(
                "Oracle cube discovery is unavailable in this environment "
                "and no registered Data Review definitions were found. "
                "Register a data-slice report definition first."
            )
        return tuple(
            sorted(configured, key=lambda item: item.name.casefold())
        )

    def list_dimensions(self, cube: str) -> tuple[DimensionInfo, ...]:
        """Return dimensions exposed for one exact Planning cube."""
        with self._client() as client:
            _, dimensions = self._resolve_cube(
                client,
                cube,
                allow_unlisted=True,
            )
        if not dimensions:
            raise DataValidationError(
                f"Oracle returned no discoverable dimensions for cube "
                f"'{str(cube).strip()}'. This environment does not expose "
                "the Planning plan-type dimension API. Retry after the "
                "Oracle environment is updated, or open the Advanced "
                "exact-name fallback."
            )
        return tuple(
            sorted(dimensions, key=lambda item: item.name.casefold())
        )

    def search_members(
        self,
        cube: str,
        dimension: str,
        *,
        query: str = "",
        offset: int = 0,
        limit: int = 40,
    ) -> DataReviewMemberSearch:
        """Search members exposed by Oracle for one cube dimension."""
        normalized_dimension = str(dimension).strip()
        normalized_query = str(query).strip()
        if not normalized_dimension:
            raise DataValidationError("Select a dimension before searching members.")
        if offset < 0:
            raise DataValidationError("Member search offset cannot be negative.")
        if not 1 <= limit <= self._MAX_MEMBER_SEARCH_LIMIT:
            raise DataValidationError(
                f"Member search limit must be between 1 and "
                f"{self._MAX_MEMBER_SEARCH_LIMIT}."
            )
        with self._client() as client:
            plan_type, dimensions = self._resolve_cube(
                client,
                cube,
                allow_unlisted=True,
            )
            canonical_dimension = self._canonical_dimension(
                normalized_dimension,
                dimensions,
                plan_type.name,
            )
            members = self._cached_dimension_members(
                client,
                plan_type.name,
                canonical_dimension,
            )
        matches = self._matching_members(members, normalized_query)
        page = matches[offset : offset + limit]
        return DataReviewMemberSearch(
            cube=plan_type.name,
            dimension=canonical_dimension,
            query=normalized_query,
            members=page,
            total_matches=len(matches),
            has_more=offset + len(page) < len(matches),
            offset=offset,
            limit=limit,
        )

    def inspect_layout(
        self,
        cube: str,
        form_name: str,
    ) -> DataReviewLayout:
        """Validate a form layout against the selected cube."""
        definition = self._compatible_definition(form_name)
        if definition is not None:
            self._validate_registered_cube(cube, definition)
            return self._layout(
                definition.cube,
                definition.name,
                self._definition_layout(definition),
            )
        selection = DataReviewSelection(
            cube=cube,
            form_name=form_name,
            page_member_overrides={},
        )
        with self._client() as client:
            plan_type, dimensions = self._resolve_cube(client, cube)
            layout = PlanningFormService(client).get_form_layout(
                self._form_name(selection.form_name)
            )
            self._validate_dimensions(layout, dimensions, plan_type.name)
        return self._layout(plan_type.name, selection.form_name, layout)

    def load_slice(
        self,
        selection: DataReviewSliceSelection,
    ) -> DataReviewGrid:
        """Export an ad-hoc cube slice without a form or registration."""
        with self._client() as client:
            return self._load_slice(client, selection, label="Data Review")

    def compare_slices(
        self,
        source: DataReviewSliceSelection,
        target: DataReviewSliceSelection,
        *,
        tolerance: Decimal | str | float = Decimal("0"),
        max_mismatches: int = 100,
        include_cells: bool = True,
    ) -> DataReviewComparison:
        """Export and reconcile two independently defined cube slices."""
        with self._client() as client:
            source_grid = self._load_slice(
                client,
                source,
                label="Source",
            )
            target_grid = self._load_slice(
                client,
                target,
                label="Target",
            )
            result = DataValidationService(client).compare_grids(
                source_grid.grid,
                target_grid.grid,
                source_form=f"{source_grid.cube} source slice",
                target_form=f"{target_grid.cube} target slice",
                tolerance=tolerance,
                max_mismatches=max_mismatches,
                include_cells=include_cells,
            )
        return DataReviewComparison(
            source_cube=source_grid.cube,
            target_cube=target_grid.cube,
            result=result,
        )

    def validate_slice(
        self,
        selection: DataReviewSliceSelection,
        rules: DataQualityRules,
    ) -> DataReviewQualityValidation:
        """Load one live slice and apply configured business checks."""
        with self._client() as client:
            review = self._load_slice(client, selection, label="Validation")
            result = DataValidationService(client).validate_grid(
                review.grid,
                rules,
            )
        return DataReviewQualityValidation(
            cube=review.cube,
            form_name=review.form_name,
            result=result,
        )

    def load_grid(self, selection: DataReviewSelection) -> DataReviewGrid:
        """Export one current Planning form slice as a JSON grid."""
        with self._client() as client:
            return self._load_grid(client, selection)

    def compare(
        self,
        source: DataReviewSelection,
        target: DataReviewSelection,
        *,
        tolerance: Decimal | str | float = Decimal("0"),
        max_mismatches: int = 100,
    ) -> DataReviewComparison:
        """Export and reconcile two form slices in one Oracle session."""
        with self._client() as client:
            form_service = PlanningFormService(client)
            source_grid = self._load_grid(
                client,
                source,
                form_service=form_service,
            )
            target_grid = self._load_grid(
                client,
                target,
                form_service=form_service,
            )
            result = DataValidationService(
                client,
                form_service=form_service,
            ).compare_grids(
                source_grid.grid,
                target_grid.grid,
                source_form=source_grid.form_name,
                target_form=target_grid.form_name,
                tolerance=tolerance,
                max_mismatches=max_mismatches,
            )
        return DataReviewComparison(
            source_cube=source_grid.cube,
            target_cube=target_grid.cube,
            result=result,
        )

    def _load_grid(
        self,
        client: EPMClient,
        selection: DataReviewSelection,
        *,
        form_service: PlanningFormService | None = None,
    ) -> DataReviewGrid:
        form_name = self._form_name(selection.form_name)
        definition = self._compatible_definition(form_name)
        if definition is not None:
            return self._load_registered_grid(
                client,
                selection,
                definition,
            )
        plan_type, dimensions = self._resolve_cube(client, selection.cube)
        form_service = form_service or PlanningFormService(client)
        layout = form_service.get_form_layout(form_name)
        self._validate_dimensions(layout, dimensions, plan_type.name)
        page_members = self._page_members(
            layout,
            selection.page_member_overrides,
        )
        filters = tuple(
            value
            for value in (
                str(item).strip() for item in selection.filter_members
            )
            if value
        )
        grid = form_service.export_form(
            form_name,
            page_members=page_members,
            filter_members=filters,
        )
        self._validate_grid_dimensions(grid, dimensions, plan_type.name)
        review = self._review_grid(plan_type.name, form_name, grid)
        self._logger.info(
            "Data Review grid loaded: cube='%s', form='%s', rows=%s, "
            "columns=%s.",
            plan_type.name,
            form_name,
            review.row_count,
            review.column_count,
        )
        return review

    def _load_slice(
        self,
        client: EPMClient,
        selection: DataReviewSliceSelection,
        *,
        label: str,
    ) -> DataReviewGrid:
        plan_type, dimensions = self._resolve_cube(
            client,
            selection.cube,
            allow_unlisted=True,
        )
        definition = self._slice_definition(
            selection,
            cube=plan_type.name,
            label=label,
        )
        self._validate_dimension_names(
            (
                *definition.page_dimensions,
                *definition.row_dimensions,
                *definition.column_dimensions,
            ),
            dimensions,
            plan_type.name,
        )
        grid = DataSliceReportService(
            client,
            logger=self._logger.getChild("data_slice"),
        ).export(definition, pov=definition.pov)
        review = self._review_grid(
            plan_type.name,
            f"{plan_type.name} data slice",
            grid,
        )
        self._logger.info(
            "Ad-hoc Data Review loaded: cube='%s', rows=%s, columns=%s.",
            plan_type.name,
            review.row_count,
            review.column_count,
        )
        return review

    def _slice_definition(
        self,
        selection: DataReviewSliceSelection,
        *,
        cube: str,
        label: str,
    ) -> DataSliceReportDefinition:
        pov = tuple(
            (str(dimension).strip(), str(member).strip())
            for dimension, member in selection.pov.items()
            if str(dimension).strip() and str(member).strip()
        )
        rows = self._axis_segment(selection.rows, axis="row")
        columns = self._axis_segment(selection.columns, axis="column")
        dimensions = [
            *(dimension.casefold() for dimension, _ in pov),
            *(dimension.casefold() for dimension in rows.dimensions),
            *(dimension.casefold() for dimension in columns.dimensions),
        ]
        if len(dimensions) != len(set(dimensions)):
            raise DataValidationError(
                "Each dimension can be assigned only once across POV, "
                "rows, and columns."
            )
        requested_cells = prod(
            len(members) for members in rows.members
        ) * prod(len(members) for members in columns.members)
        if requested_cells > self._MAX_REQUESTED_CELLS:
            raise DataValidationError(
                f"The requested slice contains approximately "
                f"{requested_cells:,} intersections. Reduce row or column "
                f"members below the {self._MAX_REQUESTED_CELLS:,}-cell "
                "safety limit."
            )
        return DataSliceReportDefinition(
            name=f"{label} ad-hoc slice",
            title=f"{label} ad-hoc slice",
            cube=cube,
            pov=pov,
            rows=(rows,),
            columns=(columns,),
        )

    @staticmethod
    def _axis_segment(
        axes: tuple[DataReviewAxisSelection, ...],
        *,
        axis: str,
    ) -> ReportAxisSegment:
        normalized = tuple(
            DataReviewAxisSelection(
                dimension=str(item.dimension).strip(),
                members=tuple(
                    value
                    for value in (
                        str(member).strip() for member in item.members
                    )
                    if value
                ),
            )
            for item in axes
        )
        if not normalized:
            raise DataValidationError(
                f"Add at least one {axis} dimension."
            )
        if any(not item.dimension or not item.members for item in normalized):
            raise DataValidationError(
                f"Every {axis} dimension requires at least one member."
            )
        keys = [item.dimension.casefold() for item in normalized]
        if len(keys) != len(set(keys)):
            raise DataValidationError(
                f"The {axis} axis contains a duplicate dimension."
            )
        return ReportAxisSegment(
            dimensions=tuple(item.dimension for item in normalized),
            members=tuple(item.members for item in normalized),
        )

    def _load_registered_grid(
        self,
        client: EPMClient,
        selection: DataReviewSelection,
        definition: DataSliceReportDefinition,
    ) -> DataReviewGrid:
        """Export a catalog-defined slice on older Planning versions."""
        self._validate_registered_cube(selection.cube, definition)
        if selection.filter_members:
            raise DataValidationError(
                "Additional member filters are not supported for a "
                "registered Data Review. Configure its row and column "
                "members in Reports & Artifacts instead."
            )
        layout = self._definition_layout(definition)
        page_members = self._page_members(
            layout,
            selection.page_member_overrides,
        )
        pov = tuple(zip(layout.page_dimensions, page_members, strict=True))
        grid = DataSliceReportService(
            client,
            logger=self._logger.getChild("data_slice"),
        ).export(definition, pov=pov)
        review = self._review_grid(
            definition.cube,
            definition.name,
            grid,
        )
        self._logger.info(
            "Registered Data Review grid loaded: cube='%s', "
            "definition='%s', rows=%s, columns=%s.",
            definition.cube,
            definition.name,
            review.row_count,
            review.column_count,
        )
        return review

    def _client(self) -> EPMClient:
        client = EPMClient(
            self._settings,
            logger=self._logger.getChild("client"),
        )
        try:
            client.authenticate()
        except Exception:
            client.close()
            raise
        return client

    def _resolve_cube(
        self,
        client: EPMClient,
        cube: str,
        *,
        allow_unlisted: bool = False,
    ) -> tuple[PlanTypeInfo, tuple[DimensionInfo, ...]]:
        normalized = str(cube).strip()
        if not normalized:
            raise DataValidationError("Select a Planning cube.")
        application = ApplicationService(client)
        try:
            plan_types = application.get_plan_types()
        except APIRequestError as exc:
            compatibility_failure = exc.status_code in {
                400,
                404,
                405,
                501,
            }
            if (
                not allow_unlisted
                or not compatibility_failure
                or (
                    client.is_cloud_environment
                    and exc.status_code != 404
                )
            ):
                raise
            plan_types = ()
        plan_type = next(
            (
                item
                for item in plan_types
                if normalized.casefold()
                in {item.name.casefold(), item.cube_name.casefold()}
            ),
            None,
        )
        if plan_type is None:
            if not allow_unlisted:
                raise DataValidationError(
                    f"Planning cube '{normalized}' was not found."
                )
            plan_type = PlanTypeInfo(
                name=normalized,
                cube_name=normalized,
            )
            self._logger.warning(
                "Planning cube '%s' was not returned by cube discovery; "
                "continuing with the exact user-supplied name for the "
                "read-only data-slice request.",
                normalized,
            )
        try:
            dimensions = application.get_dimensions(plan_type.name)
        except APIRequestError as exc:
            if exc.status_code not in {400, 404, 405, 501}:
                raise
            self._logger.warning(
                "Get Dimensions is unavailable for cube '%s'; the form "
                "or data-slice endpoint will validate the request.",
                plan_type.name,
            )
            dimensions = ()
        return plan_type, dimensions

    def _cached_dimension_members(
        self,
        client: EPMClient,
        cube: str,
        dimension: str,
    ) -> tuple[MemberInfo, ...]:
        key = (cube.casefold(), dimension.casefold())
        now = monotonic()
        with self._member_cache_lock:
            cached = self._member_cache.get(key)
            if cached is not None and cached[0] > now:
                self._member_cache.move_to_end(key)
                return cached[1]
            if cached is not None:
                del self._member_cache[key]
        try:
            members = ApplicationService(client).get_dimension_members(
                cube,
                dimension,
            )
        except APIRequestError as exc:
            if exc.status_code not in {400, 404, 405, 501}:
                raise
            raise DataValidationError(
                "Oracle member discovery is not available for this Planning "
                "version. Exact member names can still be entered manually."
            ) from exc
        with self._member_cache_lock:
            self._member_cache[key] = (
                now + self._MEMBER_CACHE_TTL_SECONDS,
                members,
            )
            self._member_cache.move_to_end(key)
            while len(self._member_cache) > self._MEMBER_CACHE_MAX_ENTRIES:
                self._member_cache.popitem(last=False)
        return members

    @staticmethod
    def _canonical_dimension(
        dimension: str,
        dimensions: tuple[DimensionInfo, ...],
        cube: str,
    ) -> str:
        if not dimensions:
            return dimension
        match = next(
            (
                item.name
                for item in dimensions
                if item.name.casefold() == dimension.casefold()
            ),
            None,
        )
        if match is None:
            raise DataValidationError(
                f"Dimension '{dimension}' was not found in Planning cube "
                f"'{cube}'."
            )
        return match

    @staticmethod
    def _matching_members(
        members: tuple[MemberInfo, ...],
        query: str,
    ) -> tuple[MemberInfo, ...]:
        if not query:
            return members
        needle = query.casefold()

        def fields(member: MemberInfo) -> tuple[str, ...]:
            return tuple(
                value.casefold()
                for value in (member.name, member.alias, member.path)
                if value
            )

        matches = tuple(
            member
            for member in members
            if any(needle in value for value in fields(member))
        )

        def rank(member: MemberInfo) -> tuple[int, str]:
            name = member.name.casefold()
            alias = (member.alias or "").casefold()
            if name == needle:
                priority = 0
            elif alias == needle:
                priority = 1
            elif name.startswith(needle):
                priority = 2
            elif alias.startswith(needle):
                priority = 3
            else:
                priority = 4
            return priority, name

        return tuple(sorted(matches, key=rank))

    @staticmethod
    def _page_members(
        layout: FormLayout,
        overrides: Mapping[str, str],
    ) -> tuple[str, ...]:
        normalized = {
            str(name).strip().casefold(): str(value).strip()
            for name, value in overrides.items()
            if str(name).strip() and str(value).strip()
        }
        known = {name.casefold() for name in layout.page_dimensions}
        unknown = set(normalized) - known
        if unknown:
            raise DataValidationError(
                "Unknown form POV dimensions: " + ", ".join(sorted(unknown))
            )
        current = {name.casefold(): value for name, value in layout.pov}
        allowed = {
            name.casefold(): values
            for name, values in layout.allowed_page_members
        }
        result: list[str] = []
        for dimension in layout.page_dimensions:
            key = dimension.casefold()
            value = normalized.get(key) or current.get(key)
            if not value:
                raise DataValidationError(
                    f"Select a member for POV dimension '{dimension}'."
                )
            choices = allowed.get(key, ())
            if choices and value.casefold() not in {
                choice.casefold() for choice in choices
            }:
                raise DataValidationError(
                    f"Member '{value}' is not allowed for '{dimension}'."
                )
            result.append(value)
        return tuple(result)

    @staticmethod
    def _validate_dimensions(
        layout: FormLayout,
        dimensions: tuple[DimensionInfo, ...],
        cube: str,
    ) -> None:
        DataReviewWorkspaceService._validate_dimension_names(
            (
                *layout.page_dimensions,
                *layout.row_dimensions,
                *layout.column_dimensions,
            ),
            dimensions,
            cube,
        )

    @staticmethod
    def _validate_grid_dimensions(
        grid: FormGrid,
        dimensions: tuple[DimensionInfo, ...],
        cube: str,
    ) -> None:
        DataReviewWorkspaceService._validate_dimension_names(
            (*grid.row_dimensions, *grid.column_dimensions),
            dimensions,
            cube,
        )

    @staticmethod
    def _validate_dimension_names(
        form_dimensions: tuple[str, ...],
        dimensions: tuple[DimensionInfo, ...],
        cube: str,
    ) -> None:
        if not dimensions:
            return
        available = {item.name.casefold() for item in dimensions}
        missing = tuple(
            name for name in form_dimensions if name.casefold() not in available
        )
        if missing:
            raise DataValidationError(
                "The selected form does not match cube "
                f"'{cube}'. Missing dimensions: {', '.join(missing)}."
            )

    @staticmethod
    def _layout(
        cube: str,
        form_name: str,
        layout: FormLayout,
    ) -> DataReviewLayout:
        return DataReviewLayout(
            cube=cube,
            form_name=str(form_name).strip(),
            page_dimensions=layout.page_dimensions,
            row_dimensions=layout.row_dimensions,
            column_dimensions=layout.column_dimensions,
            current_pov=layout.pov,
            allowed_page_members=layout.allowed_page_members,
        )

    def _registered_definitions(
        self,
    ) -> tuple[DataSliceReportDefinition, ...]:
        return self._report_catalog.load(
            self._settings.report_catalog_file
        )

    def _registered_definition(
        self,
        name: str,
    ) -> DataSliceReportDefinition | None:
        normalized = self._form_name(name).casefold()
        return next(
            (
                definition
                for definition in self._registered_definitions()
                if definition.name.casefold() == normalized
            ),
            None,
        )

    def _compatible_definition(
        self,
        name: str,
    ) -> DataSliceReportDefinition | None:
        """Use catalog slices only for legacy/on-premises environments."""
        if self._settings.resolved_deployment_mode == "cloud":
            return None
        return self._registered_definition(name)

    @staticmethod
    def _definition_layout(
        definition: DataSliceReportDefinition,
    ) -> FormLayout:
        return FormLayout(
            page_dimensions=definition.page_dimensions,
            row_dimensions=definition.row_dimensions,
            column_dimensions=definition.column_dimensions,
            pov=definition.pov,
        )

    @staticmethod
    def _validate_registered_cube(
        cube: str,
        definition: DataSliceReportDefinition,
    ) -> None:
        normalized = str(cube).strip()
        if normalized.casefold() != definition.cube.casefold():
            raise DataValidationError(
                f"Registered Data Review '{definition.name}' uses cube "
                f"'{definition.cube}', not '{normalized or 'an empty cube'}'."
            )

    @classmethod
    def _review_grid(
        cls,
        cube: str,
        form_name: str,
        grid: FormGrid,
    ) -> DataReviewGrid:
        row_count = len(grid.rows)
        column_count = len(grid.columns)
        missing = sum(
            cls._is_missing(value)
            for row in grid.rows
            for value in row.data
        )
        return DataReviewGrid(
            cube=cube,
            form_name=form_name,
            grid=grid,
            row_count=row_count,
            column_count=column_count,
            cell_count=row_count * column_count,
            missing_cell_count=missing,
        )

    @staticmethod
    def _cube(item: PlanTypeInfo) -> DataReviewCube:
        return DataReviewCube(
            name=item.name,
            cube_name=item.cube_name,
            cube_type=item.cube_type,
            dimension_count=item.dimension_count,
        )

    @staticmethod
    def _form_name(value: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise DataValidationError("Planning form name or ID is required.")
        return normalized

    @classmethod
    def _is_missing(cls, value: object) -> bool:
        if value is None:
            return True
        return str(value).strip().casefold() in cls._MISSING_VALUES

"""Tests for the read-only Data Review application service."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.application.data_review import (
    DataReviewAxisSelection,
    DataReviewSelection,
    DataReviewSliceSelection,
    DataReviewWorkspaceService,
)
from app.config.settings import Settings
from app.models.data_validation import FormGrid, FormGridRow, FormLayout
from app.models.environment import DimensionInfo, MemberInfo, PlanTypeInfo
from app.models.report import DataSliceReportDefinition, ReportAxisSegment
from app.utils.exceptions import APIRequestError, DataValidationError


def _settings(*, cloud: bool = True) -> Settings:
    return Settings(
        epm_base_url=(
            "https://example.oraclecloud.com"
            if cloud
            else "http://epm.internal/HyperionPlanning"
        ),
        epm_username="administrator",
        epm_password="secret",
        application_name="Vision",
    )


def _layout() -> FormLayout:
    return FormLayout(
        page_dimensions=("Scenario", "Year"),
        row_dimensions=("Account",),
        column_dimensions=("Period",),
        allowed_page_members=(
            ("Scenario", ("Forecast", "Actual")),
            ("Year", ("FY26", "FY27")),
        ),
        pov=(("Scenario", "Forecast"), ("Year", "FY26")),
    )


def _grid(first: object = 100, second: object = "#Missing") -> FormGrid:
    return FormGrid(
        row_dimensions=("Account",),
        column_dimensions=("Period",),
        columns=(("Jan",), ("Feb",)),
        rows=(
            FormGridRow(
                headers=("Revenue",),
                data=(first, second),
            ),
        ),
        pov=(("Scenario", "Forecast"), ("Year", "FY26")),
    )


def _definition() -> DataSliceReportDefinition:
    return DataSliceReportDefinition(
        name="Revenue Review",
        title="Revenue Review",
        cube="VisASO",
        pov=(("Scenario", "Forecast"), ("Year", "FY26")),
        columns=(
            ReportAxisSegment(
                dimensions=("Period",),
                members=(("Jan", "Feb"),),
            ),
        ),
        rows=(
            ReportAxisSegment(
                dimensions=("Account",),
                members=(("Revenue",),),
            ),
        ),
    )


def _service(
    definitions: tuple[DataSliceReportDefinition, ...] = (),
    *,
    cloud: bool = True,
) -> DataReviewWorkspaceService:
    catalog = MagicMock()
    catalog.load.return_value = definitions
    return DataReviewWorkspaceService(
        _settings(cloud=cloud),
        report_catalog_service=catalog,
    )


def _oracle_mocks(grid_side_effect=None):
    client = MagicMock()
    client.__enter__.return_value = client
    application = MagicMock()
    application.get_plan_types.return_value = (
        PlanTypeInfo(name="Plan1", cube_name="Plan1"),
        PlanTypeInfo(name="Rpt", cube_name="Rpt"),
    )
    application.get_dimensions.return_value = tuple(
        DimensionInfo(name=name)
        for name in ("Scenario", "Year", "Account", "Period")
    )
    forms = MagicMock()
    forms.get_form_layout.return_value = _layout()
    if grid_side_effect is None:
        forms.export_form.return_value = _grid()
    else:
        forms.export_form.side_effect = grid_side_effect
    return client, application, forms


def test_data_review_lists_cubes_visible_to_oracle_user() -> None:
    client, application, forms = _oracle_mocks()
    service = _service()

    with (
        patch.object(service, "_client", return_value=client),
        patch(
            "app.application.data_review.ApplicationService",
            return_value=application,
        ),
        patch(
            "app.application.data_review.PlanningFormService",
            return_value=forms,
        ),
    ):
        cubes = service.list_cubes()

    assert [cube.name for cube in cubes] == ["Plan1", "Rpt"]


def test_data_review_lists_sorted_dimensions_for_exact_cube() -> None:
    client, application, forms = _oracle_mocks()
    application.get_dimensions.return_value = (
        DimensionInfo("Year", "Year"),
        DimensionInfo("Account", "Account"),
        DimensionInfo("Period", "Period"),
    )
    service = _service()

    with (
        patch.object(service, "_client", return_value=client),
        patch(
            "app.application.data_review.ApplicationService",
            return_value=application,
        ),
    ):
        dimensions = service.list_dimensions("Plan1")

    assert [item.name for item in dimensions] == [
        "Account",
        "Period",
        "Year",
    ]
    application.get_dimensions.assert_called_once_with("Plan1")


def test_data_review_rejects_empty_live_dimension_catalog() -> None:
    client, application, forms = _oracle_mocks()
    application.get_dimensions.return_value = ()
    service = _service()

    with (
        patch.object(service, "_client", return_value=client),
        patch(
            "app.application.data_review.ApplicationService",
            return_value=application,
        ),
    ):
        with pytest.raises(
            DataValidationError,
            match="no discoverable dimensions",
        ):
            service.list_dimensions("Plan1")


def test_data_review_searches_members_and_reuses_cached_hierarchy() -> None:
    client, application, forms = _oracle_mocks()
    application.get_dimension_members.return_value = (
        MemberInfo(
            "Total Revenue",
            path="/Account/Total Revenue",
            has_children=True,
        ),
        MemberInfo(
            "Vehicle Revenue",
            alias="Automotive Revenue",
            path="/Account/Total Revenue/Vehicle Revenue",
            parent_name="Total Revenue",
        ),
        MemberInfo("Units", path="/Account/Units"),
    )
    service = _service()

    with (
        patch.object(service, "_client", return_value=client),
        patch(
            "app.application.data_review.ApplicationService",
            return_value=application,
        ),
    ):
        first = service.search_members(
            "Plan1",
            "Account",
            query="auto",
            limit=10,
        )
        second = service.search_members(
            "Plan1",
            "Account",
            query="unit",
            limit=10,
        )

    assert [item.name for item in first.members] == ["Vehicle Revenue"]
    assert [item.name for item in second.members] == ["Units"]
    assert first.total_matches == 1
    application.get_dimension_members.assert_called_once_with(
        "Plan1",
        "Account",
    )


def test_data_review_pages_live_members_without_reloading_hierarchy() -> None:
    client, application, forms = _oracle_mocks()
    application.get_dimension_members.return_value = (
        MemberInfo("Revenue", path="/Account/Revenue"),
        MemberInfo("Expense", path="/Account/Expense"),
        MemberInfo("Profit", path="/Account/Profit"),
    )
    service = _service()

    with (
        patch.object(service, "_client", return_value=client),
        patch(
            "app.application.data_review.ApplicationService",
            return_value=application,
        ),
    ):
        first = service.search_members(
            "Plan1",
            "Account",
            offset=0,
            limit=1,
        )
        second = service.search_members(
            "Plan1",
            "Account",
            offset=1,
            limit=1,
        )

    assert [item.name for item in first.members] == ["Revenue"]
    assert [item.name for item in second.members] == ["Expense"]
    assert first.has_more is True
    assert second.has_more is True
    assert second.offset == 1
    assert second.limit == 1
    application.get_dimension_members.assert_called_once_with(
        "Plan1",
        "Account",
    )


def test_data_review_loads_selected_cube_form_and_pov() -> None:
    client, application, forms = _oracle_mocks()
    service = _service()
    selection = DataReviewSelection(
        cube="Plan1",
        form_name="Revenue Review",
        page_member_overrides={"Scenario": "Actual", "Year": "FY27"},
        filter_members=("Revenue",),
    )

    with (
        patch.object(service, "_client", return_value=client),
        patch(
            "app.application.data_review.ApplicationService",
            return_value=application,
        ),
        patch(
            "app.application.data_review.PlanningFormService",
            return_value=forms,
        ),
    ):
        review = service.load_grid(selection)

    assert review.cube == "Plan1"
    assert review.row_count == 1
    assert review.column_count == 2
    assert review.cell_count == 2
    assert review.missing_cell_count == 1
    forms.export_form.assert_called_once_with(
        "Revenue Review",
        page_members=("Actual", "FY27"),
        filter_members=("Revenue",),
    )


def test_data_review_rejects_form_dimensions_outside_selected_cube() -> None:
    client, application, forms = _oracle_mocks()
    application.get_dimensions.return_value = (
        DimensionInfo(name="Account"),
        DimensionInfo(name="Period"),
    )
    service = _service()

    with (
        patch.object(service, "_client", return_value=client),
        patch(
            "app.application.data_review.ApplicationService",
            return_value=application,
        ),
        patch(
            "app.application.data_review.PlanningFormService",
            return_value=forms,
        ),
        pytest.raises(DataValidationError, match="does not match cube"),
    ):
        service.inspect_layout("Plan1", "Revenue Review")


def test_data_review_compares_two_live_form_slices() -> None:
    client, application, forms = _oracle_mocks(
        [_grid(100, 200), _grid(100, 198)]
    )
    service = _service()
    source = DataReviewSelection("Plan1", "Source", {})
    target = DataReviewSelection("Rpt", "Target", {})

    with (
        patch.object(service, "_client", return_value=client),
        patch(
            "app.application.data_review.ApplicationService",
            return_value=application,
        ),
        patch(
            "app.application.data_review.PlanningFormService",
            return_value=forms,
        ),
    ):
        comparison = service.compare(source, target, tolerance="0.5")

    assert comparison.source_cube == "Plan1"
    assert comparison.target_cube == "Rpt"
    assert comparison.result.compared_cells == 2
    assert comparison.result.matched_cells == 1
    assert comparison.result.mismatches[0].difference == 2


def test_cube_discovery_falls_back_to_registered_data_reviews() -> None:
    client, application, forms = _oracle_mocks()
    client.is_cloud_environment = False
    application.get_plan_types.side_effect = APIRequestError(
        "Not Found",
        status_code=404,
    )
    service = _service((_definition(),), cloud=False)

    with (
        patch.object(service, "_client", return_value=client),
        patch(
            "app.application.data_review.ApplicationService",
            return_value=application,
        ),
    ):
        cubes = service.list_cubes()

    assert [cube.name for cube in cubes] == ["VisASO"]


def test_on_prem_cube_discovery_accepts_legacy_method_status() -> None:
    client, application, forms = _oracle_mocks()
    client.is_cloud_environment = False
    application.get_plan_types.side_effect = APIRequestError(
        "Method Not Allowed",
        status_code=405,
    )
    service = _service((_definition(),), cloud=False)

    with (
        patch.object(service, "_client", return_value=client),
        patch(
            "app.application.data_review.ApplicationService",
            return_value=application,
        ),
    ):
        cubes = service.list_cubes()

    assert [cube.name for cube in cubes] == ["VisASO"]


def test_on_prem_live_cubes_exclude_stale_registered_cube_names() -> None:
    client, application, forms = _oracle_mocks()
    client.is_cloud_environment = False
    service = _service((_definition(),), cloud=False)

    with (
        patch.object(service, "_client", return_value=client),
        patch(
            "app.application.data_review.ApplicationService",
            return_value=application,
        ),
    ):
        cubes = service.list_cubes()

    assert [cube.name for cube in cubes] == ["Plan1", "Rpt"]


def test_registered_data_review_uses_compatible_data_slice_export() -> None:
    client, application, forms = _oracle_mocks()
    data_slice = MagicMock()
    data_slice.export.return_value = _grid()
    service = _service((_definition(),), cloud=False)
    selection = DataReviewSelection(
        cube="VisASO",
        form_name="Revenue Review",
        page_member_overrides={"Year": "FY27"},
    )

    layout = service.inspect_layout("VisASO", "Revenue Review")
    with (
        patch.object(service, "_client", return_value=client),
        patch(
            "app.application.data_review.DataSliceReportService",
            return_value=data_slice,
        ),
    ):
        review = service.load_grid(selection)

    assert layout.current_pov == (
        ("Scenario", "Forecast"),
        ("Year", "FY26"),
    )
    assert review.cube == "VisASO"
    assert review.cell_count == 2
    data_slice.export.assert_called_once_with(
        _definition(),
        pov=(("Scenario", "Forecast"), ("Year", "FY27")),
    )


def test_cloud_cube_discovery_does_not_depend_on_registered_reports() -> None:
    client, application, forms = _oracle_mocks()
    client.is_cloud_environment = True
    application.get_plan_types.side_effect = APIRequestError(
        "Not Found",
        status_code=404,
    )
    service = _service((_definition(),), cloud=True)

    with (
        patch.object(service, "_client", return_value=client),
        patch(
            "app.application.data_review.ApplicationService",
            return_value=application,
        ),
        pytest.raises(DataValidationError, match="did not expose any"),
    ):
        service.list_cubes()


def test_ad_hoc_data_review_exports_direct_cube_slice() -> None:
    client, application, forms = _oracle_mocks()
    data_slice = MagicMock()
    data_slice.export.return_value = _grid(100, 200)
    service = _service(cloud=True)
    selection = DataReviewSliceSelection(
        cube="Plan1",
        pov={"Scenario": "Forecast", "Year": "FY26"},
        columns=(
            DataReviewAxisSelection("Period", ("Jan", "Feb")),
        ),
        rows=(
            DataReviewAxisSelection("Account", ("Revenue",)),
        ),
    )

    with (
        patch.object(service, "_client", return_value=client),
        patch(
            "app.application.data_review.ApplicationService",
            return_value=application,
        ),
        patch(
            "app.application.data_review.DataSliceReportService",
            return_value=data_slice,
        ),
    ):
        review = service.load_slice(selection)

    assert review.cube == "Plan1"
    assert review.cell_count == 2
    definition = data_slice.export.call_args.args[0]
    assert definition.cube == "Plan1"
    assert definition.pov == (
        ("Scenario", "Forecast"),
        ("Year", "FY26"),
    )
    assert definition.column_dimensions == ("Period",)
    assert definition.row_dimensions == ("Account",)
    data_slice.export.assert_called_once_with(
        definition,
        pov=definition.pov,
    )


def test_ad_hoc_data_review_accepts_exact_cube_missing_from_discovery() -> None:
    client, application, forms = _oracle_mocks()
    data_slice = MagicMock()
    data_slice.export.return_value = _grid(100, 200)
    service = _service(cloud=False)
    selection = DataReviewSliceSelection(
        cube="VisASO",
        pov={"Scenario": "Forecast", "Year": "FY26"},
        columns=(DataReviewAxisSelection("Period", ("Jan", "Feb")),),
        rows=(DataReviewAxisSelection("Account", ("Revenue",)),),
    )

    with (
        patch.object(service, "_client", return_value=client),
        patch(
            "app.application.data_review.ApplicationService",
            return_value=application,
        ),
        patch(
            "app.application.data_review.DataSliceReportService",
            return_value=data_slice,
        ),
    ):
        review = service.load_slice(selection)

    assert review.cube == "VisASO"
    assert data_slice.export.call_args.args[0].cube == "VisASO"
    application.get_dimensions.assert_called_once_with("VisASO")


def test_ad_hoc_data_review_rejects_dimension_on_multiple_axes() -> None:
    service = _service(cloud=True)
    selection = DataReviewSliceSelection(
        cube="Plan1",
        pov={"Scenario": "Forecast"},
        columns=(DataReviewAxisSelection("Period", ("Jan",)),),
        rows=(DataReviewAxisSelection("Scenario", ("Actual",)),),
    )

    with pytest.raises(DataValidationError, match="assigned only once"):
        service._slice_definition(
            selection,
            cube="Plan1",
            label="Data Review",
        )

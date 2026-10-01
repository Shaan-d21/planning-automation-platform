"""Static safety contract for the distributable Excel VBA module."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MACRO_PATH = (
    PROJECT_ROOT
    / "integrations"
    / "excel_pipeline_runner"
    / "BispEpmPipeline.bas"
)


def test_excel_pipeline_macro_uses_only_governed_backend_endpoints() -> None:
    source = MACRO_PATH.read_text(encoding="utf-8")

    assert "Public Sub CheckPipelineConnection()" in source
    assert "Public Sub RunPipelineFromExcel()" in source
    assert 'Environ$(TOKEN_ENVIRONMENT_VARIABLE)' in source
    assert "/api/v1/excel/pipelines/" in source
    assert "/runs" in source
    assert '"Authorization", "Bearer " & token' in source
    assert "Workbook_Open" not in source
    assert "EPM_PASSWORD" not in source
    assert "EPM_USERNAME" not in source
    assert "oraclecloud.com" not in source

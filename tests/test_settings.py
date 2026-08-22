"""Tests for environment-based configuration."""

from __future__ import annotations

import pytest

from app.config.settings import PROJECT_ROOT, Settings
from app.utils.exceptions import ConfigurationError


REQUIRED_ENV = {
    "EPM_BASE_URL": "https://example.oraclecloud.com/",
    "EPM_USERNAME": "epm.user",
    "EPM_PASSWORD": "secret",
    "APPLICATION_NAME": "Plan1",
    "DATABASE_URL": (
        "postgresql+psycopg://epm_user:secret@localhost/epm_automation"
    ),
}


def test_settings_loads_and_normalizes_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    password_file = tmp_path / "password.epw"
    password_file.write_text("encrypted", encoding="utf-8")
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("EPM_REQUEST_TIMEOUT", "15.5")
    monkeypatch.setenv("EPM_VERIFY_SSL", "false")
    monkeypatch.setenv("LOG_LEVEL", "warning")
    monkeypatch.setenv("DEFAULT_METADATA_IMPORT_MODE", "job_definition")
    monkeypatch.setenv("DEFAULT_POLL_INTERVAL", "2.5")
    monkeypatch.setenv("DEFAULT_JOB_TIMEOUT", "900")
    monkeypatch.setenv("DEFAULT_METADATA_ENGINE", "epmautomate")
    monkeypatch.setenv("DEFAULT_DATA_ENGINE", "rest")
    monkeypatch.setenv("DEFAULT_DATA_INTEGRATION_ENGINE", "epmautomate")
    monkeypatch.setenv("DEFAULT_BUSINESS_RULE_ENGINE", "epmautomate")
    monkeypatch.setenv("DEFAULT_PIPELINE_ENGINE", "rest")
    monkeypatch.setenv("DEFAULT_DATA_INTEGRATION_NAME", "Test_DataLoad")
    monkeypatch.setenv("DEFAULT_PIPELINE_CODE", "PIPE01")
    monkeypatch.setenv("DEFAULT_DATA_INTEGRATION_IMPORT_MODE", "Replace")
    monkeypatch.setenv("DEFAULT_DATA_INTEGRATION_EXPORT_MODE", "Merge")
    monkeypatch.setenv(
        "DATA_INTEGRATION_CATALOG_FILE",
        "config/test-integrations.json",
    )
    monkeypatch.setenv(
        "PIPELINE_CATALOG_FILE",
        "config/test-pipelines.json",
    )
    monkeypatch.setenv("REPORT_OUTPUT_DIR", "reports/test-output")
    monkeypatch.setenv(
        "REPORT_CATALOG_FILE",
        "config/test-reports.json",
    )
    monkeypatch.setenv(
        "PLANNING_PROCESS_CATALOG_FILE",
        "config/test-processes.json",
    )
    monkeypatch.setenv("EPM_AUTOMATE_EXECUTABLE", "epmautomate.bat")
    monkeypatch.setenv(
        "EPM_AUTOMATE_PASSWORD_FILE",
        str(password_file),
    )
    monkeypatch.setenv("EPM_AUTOMATE_COMMAND_TIMEOUT", "1200")
    monkeypatch.setenv("WEB_FRONTEND_URL", "https://epm.example.com/")
    monkeypatch.setenv("AGENT_ORCHESTRATOR", "langgraph")

    settings = Settings.from_env(env_file=None)

    assert settings.epm_base_url == "https://example.oraclecloud.com"
    assert settings.epm_username == "epm.user"
    assert settings.epm_password == "secret"
    assert settings.application_name == "Plan1"
    assert settings.database_url == REQUIRED_ENV["DATABASE_URL"]
    assert settings.agent_orchestrator == "langgraph"
    assert settings.request_timeout == 15.5
    assert settings.verify_ssl is False
    assert settings.log_level == "WARNING"
    assert settings.default_metadata_import_mode == "job_definition"
    assert settings.default_poll_interval == 2.5
    assert settings.default_job_timeout == 900
    assert settings.default_metadata_engine == "epmautomate"
    assert settings.default_data_engine == "rest"
    assert settings.default_data_integration_engine == "epmautomate"
    assert settings.default_business_rule_engine == "epmautomate"
    assert settings.default_pipeline_engine == "rest"
    assert settings.default_data_integration_name == "Test_DataLoad"
    assert settings.default_pipeline_code == "PIPE01"
    assert settings.default_data_integration_import_mode == "Replace"
    assert settings.default_data_integration_export_mode == "Merge"
    assert settings.data_integration_catalog_file == (
        PROJECT_ROOT / "config" / "test-integrations.json"
    )
    assert settings.pipeline_catalog_file == (
        PROJECT_ROOT / "config" / "test-pipelines.json"
    )
    assert settings.report_output_dir == (
        PROJECT_ROOT / "reports" / "test-output"
    )
    assert settings.report_catalog_file == (
        PROJECT_ROOT / "config" / "test-reports.json"
    )
    assert settings.planning_process_catalog_file == (
        PROJECT_ROOT / "config" / "test-processes.json"
    )
    assert settings.epm_automate_executable == "epmautomate.bat"
    assert settings.epm_automate_password_file == password_file
    assert settings.epm_automate_command_timeout == 1200
    assert settings.web_frontend_url == "https://epm.example.com"
    assert settings.require_epm_automate_password_file() == password_file


def test_settings_selects_groq_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("AGENT_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    monkeypatch.delenv("AGENT_MODEL", raising=False)

    settings = Settings.from_env(env_file=None)

    assert settings.agent_provider == "groq"
    assert settings.agent_model == "openai/gpt-oss-120b"
    assert settings.groq_api_key == "groq-test-key"


def test_settings_allows_missing_rest_password_until_rest_is_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("EPM_PASSWORD")

    settings = Settings.from_env(env_file=None)

    with pytest.raises(ConfigurationError, match="EPM_PASSWORD"):
        settings.require_rest_password()


@pytest.mark.parametrize(
    "url",
    [
        "example.oraclecloud.com",
        "ftp://example.oraclecloud.com",
        "https://user:password@example.oraclecloud.com",
    ],
)
def test_settings_rejects_invalid_base_url(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("EPM_BASE_URL", url)

    with pytest.raises(ConfigurationError):
        Settings.from_env(env_file=None)


def test_settings_validates_epm_automate_password_file_on_demand(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(
        "EPM_AUTOMATE_PASSWORD_FILE",
        str(tmp_path / "missing.epw"),
    )

    settings = Settings.from_env(env_file=None)

    with pytest.raises(ConfigurationError, match="does not exist"):
        settings.require_epm_automate_password_file()


def test_settings_requires_postgresql_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("DATABASE_URL")

    with pytest.raises(ConfigurationError, match="DATABASE_URL is required"):
        Settings.from_env(env_file=None)


def test_settings_rejects_non_postgresql_runtime_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///var/test.sqlite3")

    with pytest.raises(ConfigurationError, match="must use PostgreSQL"):
        Settings.from_env(env_file=None)


def test_settings_rejects_invalid_execution_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("EPM_EXECUTION_RUNTIME", "sometimes")

    with pytest.raises(ConfigurationError, match="EPM_EXECUTION_RUNTIME"):
        Settings.from_env(env_file=None)


def test_settings_requires_safe_execution_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("EXECUTION_LEASE_SECONDS", "10")

    with pytest.raises(ConfigurationError, match="at least 30"):
        Settings.from_env(env_file=None)


@pytest.mark.parametrize(
    "value",
    ["localhost:5173", "ftp://example.com", "https://example.com/#home"],
)
def test_settings_rejects_invalid_web_frontend_url(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    for name, configured in REQUIRED_ENV.items():
        monkeypatch.setenv(name, configured)
    monkeypatch.setenv("WEB_FRONTEND_URL", value)

    with pytest.raises(ConfigurationError, match="WEB_FRONTEND_URL"):
        Settings.from_env(env_file=None)


def test_settings_accepts_safe_oracle_identity_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, configured in REQUIRED_ENV.items():
        monkeypatch.setenv(name, configured)
    monkeypatch.setenv("IDENTITY_PROVIDER", "oracle_cloud")
    monkeypatch.setenv(
        "ORACLE_IDENTITY_ISSUER_URL",
        "https://idcs.example.com/",
    )
    monkeypatch.setenv("ORACLE_IDENTITY_CLIENT_ID", "public-client-id")

    settings = Settings.from_env(env_file=None)

    assert settings.identity_provider == "oracle_cloud"
    assert settings.oracle_identity_issuer_url == "https://idcs.example.com"
    assert settings.oracle_identity_discovery_url == (
        "https://idcs.example.com/.well-known/openid-configuration"
    )
    assert settings.federated_identity_ready is True


def test_settings_accepts_localhost_oidc_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, configured in REQUIRED_ENV.items():
        monkeypatch.setenv(name, configured)
    monkeypatch.setenv(
        "ORACLE_IDENTITY_REDIRECT_URI",
        "http://127.0.0.1:8000/auth/oracle/callback",
    )

    settings = Settings.from_env(env_file=None)

    assert settings.oracle_identity_redirect_uri == (
        "http://127.0.0.1:8000/auth/oracle/callback"
    )


def test_settings_rejects_insecure_identity_issuer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, configured in REQUIRED_ENV.items():
        monkeypatch.setenv(name, configured)
    monkeypatch.setenv(
        "ORACLE_IDENTITY_ISSUER_URL",
        "http://idcs.example.com",
    )

    with pytest.raises(ConfigurationError, match="HTTPS identity-domain"):
        Settings.from_env(env_file=None)


def test_settings_rejects_oracle_sign_in_page_as_issuer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, configured in REQUIRED_ENV.items():
        monkeypatch.setenv(name, configured)
    monkeypatch.setenv(
        "ORACLE_IDENTITY_ISSUER_URL",
        "https://idcs.example.com/ui/v1/signin",
    )

    with pytest.raises(ConfigurationError, match="identity-domain"):
        Settings.from_env(env_file=None)


def test_settings_rejects_epm_host_as_oidc_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, configured in REQUIRED_ENV.items():
        monkeypatch.setenv(name, configured)
    monkeypatch.setenv(
        "ORACLE_IDENTITY_REDIRECT_URI",
        "https://example.epm.us-ashburn-1.ocs.oraclecloud.com/auth/oracle/callback",
    )

    with pytest.raises(ConfigurationError, match="automation platform"):
        Settings.from_env(env_file=None)

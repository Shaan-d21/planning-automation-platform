"""Environment-based configuration for Oracle EPM automation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from app.config.email_settings import EmailNotificationSettings
from app.utils.exceptions import ConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_INTEGRATION_CATALOG_FILE = (
    PROJECT_ROOT / "config" / "data_integrations.json"
)
DEFAULT_PIPELINE_CATALOG_FILE = PROJECT_ROOT / "config" / "pipelines.json"
DEFAULT_RUNTIME_DATA_DIR = PROJECT_ROOT / "var"
DEFAULT_PLANNING_CYCLE_CATALOG_FILE = (
    PROJECT_ROOT / "config" / "planning_cycles.json"
)
DEFAULT_REPORT_OUTPUT_DIR = PROJECT_ROOT / "reports"
DEFAULT_REPORT_CATALOG_FILE = PROJECT_ROOT / "config" / "reports.json"
DEFAULT_PLANNING_PROCESS_CATALOG_FILE = (
    PROJECT_ROOT / "config" / "planning_processes.json"
)


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated runtime settings loaded from environment variables."""

    epm_base_url: str
    epm_username: str
    epm_password: str | None
    application_name: str
    deployment_mode: str = "auto"
    request_timeout: float = 30.0
    verify_ssl: bool = True
    log_level: str = "INFO"
    default_metadata_import_mode: str = "job_definition"
    default_poll_interval: float = 5.0
    default_job_timeout: float = 1800.0
    schedule_poll_interval: float = 15.0
    execution_runtime: str = "embedded"
    execution_worker_poll_interval: float = 2.0
    execution_lease_seconds: float = 120.0
    web_frontend_url: str | None = None
    default_metadata_engine: str = "rest"
    default_data_engine: str = "rest"
    default_data_integration_engine: str = "epmautomate"
    default_business_rule_engine: str = "rest"
    default_pipeline_engine: str = "rest"
    default_data_map_engine: str = "rest"
    default_data_integration_name: str | None = None
    default_pipeline_code: str | None = None
    default_data_map_name: str | None = None
    monthly_forecast_run_data_map: bool = True
    monthly_forecast_clear_target: bool = False
    validation_source_form: str | None = None
    validation_target_form: str | None = None
    validation_tolerance: float = 0.0
    default_data_integration_import_mode: str = "Replace"
    default_data_integration_export_mode: str = "Merge"
    data_integration_catalog_file: Path = (
        DEFAULT_DATA_INTEGRATION_CATALOG_FILE
    )
    pipeline_catalog_file: Path = DEFAULT_PIPELINE_CATALOG_FILE
    database_url: str | None = None
    runtime_data_dir: Path = DEFAULT_RUNTIME_DATA_DIR
    # Test-only compatibility input. Normal runtime requires DATABASE_URL.
    workflow_database_file: Path | None = None
    planning_cycle_catalog_file: Path = (
        DEFAULT_PLANNING_CYCLE_CATALOG_FILE
    )
    report_output_dir: Path = DEFAULT_REPORT_OUTPUT_DIR
    report_catalog_file: Path = DEFAULT_REPORT_CATALOG_FILE
    planning_process_catalog_file: Path = (
        DEFAULT_PLANNING_PROCESS_CATALOG_FILE
    )
    epm_automate_executable: str = "epmautomate"
    epm_automate_password_file: Path | None = None
    epm_automate_command_timeout: float = 1800.0
    agent_provider: str = "gemini"
    agent_orchestrator: str = "langgraph"
    agent_model: str = "gemini-3.5-flash-lite"
    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    groq_max_input_tokens: int = 2_500
    groq_max_completion_tokens: int = 384
    agent_max_tool_rounds: int = 4
    agent_history_messages: int = 20
    identity_provider: str = "local"
    oracle_identity_issuer_url: str | None = None
    oracle_identity_client_id: str | None = None
    oracle_identity_client_secret: str | None = None
    oracle_identity_redirect_uri: str | None = None
    email_notifications: EmailNotificationSettings = field(
        default_factory=EmailNotificationSettings
    )

    @classmethod
    def from_env(
        cls,
        env_file: str | Path | None = ".env",
    ) -> Settings:
        """Load settings from a dotenv file and the process environment.

        Existing process environment variables take precedence over values in
        the dotenv file.
        """
        if env_file is not None:
            load_dotenv(dotenv_path=env_file, override=False)

        required_values = {
            "EPM_BASE_URL": os.getenv("EPM_BASE_URL", "").strip(),
            "EPM_USERNAME": os.getenv("EPM_USERNAME", "").strip(),
            "APPLICATION_NAME": os.getenv("APPLICATION_NAME", "").strip(),
        }
        missing = [
            name for name, value in required_values.items() if not value
        ]
        if missing:
            raise ConfigurationError(
                "Missing required configuration: " + ", ".join(missing)
            )

        base_url = cls._normalize_and_validate_url(
            required_values["EPM_BASE_URL"]
        )
        timeout = cls._parse_positive_float(
            "EPM_REQUEST_TIMEOUT",
            os.getenv("EPM_REQUEST_TIMEOUT", "30"),
        )
        verify_ssl = cls._parse_bool(
            "EPM_VERIFY_SSL",
            os.getenv("EPM_VERIFY_SSL", "true"),
        )
        deployment_mode = os.getenv(
            "EPM_DEPLOYMENT_MODE",
            "auto",
        ).strip().lower().replace("-", "_")
        if deployment_mode not in {"auto", "cloud", "on_premises"}:
            raise ConfigurationError(
                "EPM_DEPLOYMENT_MODE must be 'auto', 'cloud', or "
                "'on_premises'."
            )
        metadata_import_mode = os.getenv(
            "DEFAULT_METADATA_IMPORT_MODE",
            "job_definition",
        ).strip().lower()
        if not metadata_import_mode:
            raise ConfigurationError(
                "DEFAULT_METADATA_IMPORT_MODE cannot be empty."
            )
        poll_interval = cls._parse_positive_float(
            "DEFAULT_POLL_INTERVAL",
            os.getenv("DEFAULT_POLL_INTERVAL", "5"),
        )
        job_timeout = cls._parse_positive_float(
            "DEFAULT_JOB_TIMEOUT",
            os.getenv("DEFAULT_JOB_TIMEOUT", "1800"),
        )
        schedule_poll_interval = cls._parse_positive_float(
            "SCHEDULE_POLL_INTERVAL",
            os.getenv("SCHEDULE_POLL_INTERVAL", "15"),
        )
        execution_runtime = os.getenv(
            "EPM_EXECUTION_RUNTIME",
            "embedded",
        ).strip().casefold()
        if execution_runtime not in {"embedded", "web", "worker"}:
            raise ConfigurationError(
                "EPM_EXECUTION_RUNTIME must be 'embedded', 'web', or "
                "'worker'."
            )
        execution_worker_poll_interval = cls._parse_positive_float(
            "EXECUTION_WORKER_POLL_INTERVAL",
            os.getenv("EXECUTION_WORKER_POLL_INTERVAL", "2"),
        )
        execution_lease_seconds = cls._parse_positive_float(
            "EXECUTION_LEASE_SECONDS",
            os.getenv("EXECUTION_LEASE_SECONDS", "120"),
        )
        if execution_lease_seconds < 30:
            raise ConfigurationError(
                "EXECUTION_LEASE_SECONDS must be at least 30 seconds."
            )
        web_frontend_url = os.getenv("WEB_FRONTEND_URL", "").strip() or None
        if web_frontend_url is not None:
            parsed_frontend_url = urlparse(web_frontend_url)
            if (
                parsed_frontend_url.scheme not in {"http", "https"}
                or not parsed_frontend_url.netloc
                or parsed_frontend_url.query
                or parsed_frontend_url.fragment
            ):
                raise ConfigurationError(
                    "WEB_FRONTEND_URL must be an absolute HTTP(S) origin "
                    "without a query string or fragment."
                )
            web_frontend_url = web_frontend_url.rstrip("/")
        metadata_engine = os.getenv(
            "DEFAULT_METADATA_ENGINE",
            "rest",
        ).strip().lower()
        if metadata_engine not in {"rest", "epmautomate"}:
            raise ConfigurationError(
                "DEFAULT_METADATA_ENGINE must be 'rest' or 'epmautomate'."
            )
        data_engine = os.getenv(
            "DEFAULT_DATA_ENGINE",
            "rest",
        ).strip().lower()
        if data_engine not in {"rest", "epmautomate"}:
            raise ConfigurationError(
                "DEFAULT_DATA_ENGINE must be 'rest' or 'epmautomate'."
            )
        integration_engine = os.getenv(
            "DEFAULT_DATA_INTEGRATION_ENGINE",
            "epmautomate",
        ).strip().lower()
        if integration_engine not in {"rest", "epmautomate"}:
            raise ConfigurationError(
                "DEFAULT_DATA_INTEGRATION_ENGINE must be 'rest' or "
                "'epmautomate'."
            )
        business_rule_engine = os.getenv(
            "DEFAULT_BUSINESS_RULE_ENGINE",
            "rest",
        ).strip().lower()
        if business_rule_engine not in {"rest", "epmautomate"}:
            raise ConfigurationError(
                "DEFAULT_BUSINESS_RULE_ENGINE must be 'rest' or "
                "'epmautomate'."
            )
        pipeline_engine = os.getenv(
            "DEFAULT_PIPELINE_ENGINE",
            "rest",
        ).strip().lower()
        if pipeline_engine not in {"rest", "epmautomate"}:
            raise ConfigurationError(
                "DEFAULT_PIPELINE_ENGINE must be 'rest' or 'epmautomate'."
            )
        data_map_engine = os.getenv(
            "DEFAULT_DATA_MAP_ENGINE",
            "rest",
        ).strip().lower()
        if data_map_engine not in {"rest", "epmautomate"}:
            raise ConfigurationError(
                "DEFAULT_DATA_MAP_ENGINE must be 'rest' or 'epmautomate'."
            )
        integration_name = os.getenv(
            "DEFAULT_DATA_INTEGRATION_NAME",
            "",
        ).strip() or None
        pipeline_code = os.getenv(
            "DEFAULT_PIPELINE_CODE",
            "",
        ).strip() or None
        data_map_name = os.getenv(
            "DEFAULT_DATA_MAP_NAME",
            "",
        ).strip() or None
        monthly_forecast_run_data_map = cls._parse_bool(
            "MONTHLY_FORECAST_RUN_DATA_MAP",
            os.getenv("MONTHLY_FORECAST_RUN_DATA_MAP", "true"),
        )
        monthly_forecast_clear_target = cls._parse_bool(
            "MONTHLY_FORECAST_CLEAR_TARGET",
            os.getenv("MONTHLY_FORECAST_CLEAR_TARGET", "false"),
        )
        validation_source_form = os.getenv(
            "VALIDATION_SOURCE_FORM",
            "",
        ).strip() or None
        validation_target_form = os.getenv(
            "VALIDATION_TARGET_FORM",
            "",
        ).strip() or None
        if bool(validation_source_form) != bool(validation_target_form):
            raise ConfigurationError(
                "VALIDATION_SOURCE_FORM and VALIDATION_TARGET_FORM must be "
                "configured together."
            )
        validation_tolerance = cls._parse_nonnegative_float(
            "VALIDATION_TOLERANCE",
            os.getenv("VALIDATION_TOLERANCE", "0"),
        )
        integration_import_mode = os.getenv(
            "DEFAULT_DATA_INTEGRATION_IMPORT_MODE",
            "Replace",
        ).strip()
        if not integration_import_mode:
            raise ConfigurationError(
                "DEFAULT_DATA_INTEGRATION_IMPORT_MODE cannot be empty."
            )
        integration_export_mode = os.getenv(
            "DEFAULT_DATA_INTEGRATION_EXPORT_MODE",
            "Merge",
        ).strip()
        if not integration_export_mode:
            raise ConfigurationError(
                "DEFAULT_DATA_INTEGRATION_EXPORT_MODE cannot be empty."
            )
        catalog_file_value = os.getenv(
            "DATA_INTEGRATION_CATALOG_FILE",
            str(DEFAULT_DATA_INTEGRATION_CATALOG_FILE),
        ).strip()
        if not catalog_file_value:
            raise ConfigurationError(
                "DATA_INTEGRATION_CATALOG_FILE cannot be empty."
            )
        catalog_file = Path(catalog_file_value).expanduser()
        if not catalog_file.is_absolute():
            catalog_file = PROJECT_ROOT / catalog_file
        pipeline_catalog_value = os.getenv(
            "PIPELINE_CATALOG_FILE",
            str(DEFAULT_PIPELINE_CATALOG_FILE),
        ).strip()
        if not pipeline_catalog_value:
            raise ConfigurationError(
                "PIPELINE_CATALOG_FILE cannot be empty."
            )
        pipeline_catalog_file = Path(pipeline_catalog_value).expanduser()
        if not pipeline_catalog_file.is_absolute():
            pipeline_catalog_file = PROJECT_ROOT / pipeline_catalog_file
        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url:
            raise ConfigurationError(
                "DATABASE_URL is required. Configure a PostgreSQL URL such "
                "as postgresql+psycopg://user:password@host/database."
            )
        if not database_url.casefold().startswith(
            ("postgresql://", "postgresql+")
        ):
            raise ConfigurationError(
                "DATABASE_URL must use PostgreSQL for application runtime."
            )
        runtime_data_value = os.getenv(
            "RUNTIME_DATA_DIR",
            str(DEFAULT_RUNTIME_DATA_DIR),
        ).strip()
        if not runtime_data_value:
            raise ConfigurationError("RUNTIME_DATA_DIR cannot be empty.")
        runtime_data_dir = Path(runtime_data_value).expanduser()
        if not runtime_data_dir.is_absolute():
            runtime_data_dir = PROJECT_ROOT / runtime_data_dir
        planning_cycle_catalog_value = os.getenv(
            "PLANNING_CYCLE_CATALOG_FILE",
            str(DEFAULT_PLANNING_CYCLE_CATALOG_FILE),
        ).strip()
        if not planning_cycle_catalog_value:
            raise ConfigurationError(
                "PLANNING_CYCLE_CATALOG_FILE cannot be empty."
            )
        planning_cycle_catalog_file = Path(
            planning_cycle_catalog_value
        ).expanduser()
        if not planning_cycle_catalog_file.is_absolute():
            planning_cycle_catalog_file = (
                PROJECT_ROOT / planning_cycle_catalog_file
            )
        report_output_value = os.getenv(
            "REPORT_OUTPUT_DIR",
            str(DEFAULT_REPORT_OUTPUT_DIR),
        ).strip()
        if not report_output_value:
            raise ConfigurationError(
                "REPORT_OUTPUT_DIR cannot be empty."
            )
        report_output_dir = Path(report_output_value).expanduser()
        if not report_output_dir.is_absolute():
            report_output_dir = PROJECT_ROOT / report_output_dir
        report_catalog_value = os.getenv(
            "REPORT_CATALOG_FILE",
            str(DEFAULT_REPORT_CATALOG_FILE),
        ).strip()
        if not report_catalog_value:
            raise ConfigurationError(
                "REPORT_CATALOG_FILE cannot be empty."
            )
        report_catalog_file = Path(report_catalog_value).expanduser()
        if not report_catalog_file.is_absolute():
            report_catalog_file = PROJECT_ROOT / report_catalog_file
        planning_process_catalog_value = os.getenv(
            "PLANNING_PROCESS_CATALOG_FILE",
            str(DEFAULT_PLANNING_PROCESS_CATALOG_FILE),
        ).strip()
        if not planning_process_catalog_value:
            raise ConfigurationError(
                "PLANNING_PROCESS_CATALOG_FILE cannot be empty."
            )
        planning_process_catalog_file = Path(
            planning_process_catalog_value
        ).expanduser()
        if not planning_process_catalog_file.is_absolute():
            planning_process_catalog_file = (
                PROJECT_ROOT / planning_process_catalog_file
            )
        epm_automate_executable = os.getenv(
            "EPM_AUTOMATE_EXECUTABLE",
            "epmautomate",
        ).strip()
        if not epm_automate_executable:
            raise ConfigurationError(
                "EPM_AUTOMATE_EXECUTABLE cannot be empty."
            )
        password_file_value = os.getenv(
            "EPM_AUTOMATE_PASSWORD_FILE",
            "",
        ).strip()
        password_file = (
            Path(password_file_value).expanduser()
            if password_file_value
            else None
        )
        epm_automate_timeout = cls._parse_positive_float(
            "EPM_AUTOMATE_COMMAND_TIMEOUT",
            os.getenv("EPM_AUTOMATE_COMMAND_TIMEOUT", "1800"),
        )
        log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
        valid_log_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if log_level not in valid_log_levels:
            raise ConfigurationError(
                "LOG_LEVEL must be one of: "
                + ", ".join(sorted(valid_log_levels))
            )
        email_notifications = EmailNotificationSettings.from_env()
        agent_provider = os.getenv(
            "AGENT_PROVIDER", "gemini"
        ).strip().casefold()
        if not agent_provider:
            raise ConfigurationError("AGENT_PROVIDER cannot be empty.")
        if agent_provider not in {"gemini", "groq"}:
            raise ConfigurationError(
                "AGENT_PROVIDER must be 'gemini' or 'groq'."
            )
        agent_orchestrator = os.getenv(
            "AGENT_ORCHESTRATOR", "langgraph"
        ).strip().casefold()
        if agent_orchestrator not in {"langgraph", "legacy"}:
            raise ConfigurationError(
                "AGENT_ORCHESTRATOR must be 'langgraph' or 'legacy'."
            )
        default_agent_model = (
            "openai/gpt-oss-120b"
            if agent_provider == "groq"
            else "gemini-3.5-flash-lite"
        )
        agent_model = os.getenv("AGENT_MODEL", default_agent_model).strip()
        if not agent_model:
            raise ConfigurationError("AGENT_MODEL cannot be empty.")
        try:
            agent_max_tool_rounds = int(
                os.getenv("AGENT_MAX_TOOL_ROUNDS", "4")
            )
            agent_history_messages = int(
                os.getenv("AGENT_HISTORY_MESSAGES", "20")
            )
            groq_max_input_tokens = int(
                os.getenv("GROQ_MAX_INPUT_TOKENS", "2500")
            )
            groq_max_completion_tokens = int(
                os.getenv("GROQ_MAX_COMPLETION_TOKENS", "384")
            )
        except ValueError as exc:
            raise ConfigurationError(
                "AGENT_MAX_TOOL_ROUNDS and AGENT_HISTORY_MESSAGES must be integers."
            ) from exc
        if not 1 <= agent_max_tool_rounds <= 10:
            raise ConfigurationError(
                "AGENT_MAX_TOOL_ROUNDS must be between 1 and 10."
            )
        if not 2 <= agent_history_messages <= 100:
            raise ConfigurationError(
                "AGENT_HISTORY_MESSAGES must be between 2 and 100."
            )
        if not 1_000 <= groq_max_input_tokens <= 7_000:
            raise ConfigurationError(
                "GROQ_MAX_INPUT_TOKENS must be between 1000 and 7000."
            )
        if not 64 <= groq_max_completion_tokens <= 2_048:
            raise ConfigurationError(
                "GROQ_MAX_COMPLETION_TOKENS must be between 64 and 2048."
            )
        identity_provider = os.getenv(
            "IDENTITY_PROVIDER",
            "local",
        ).strip().casefold().replace("-", "_")
        if identity_provider not in {"local", "oracle_cloud"}:
            raise ConfigurationError(
                "IDENTITY_PROVIDER must be 'local' or 'oracle_cloud'."
            )
        oracle_identity_issuer_url = os.getenv(
            "ORACLE_IDENTITY_ISSUER_URL",
            "",
        ).strip().rstrip("/") or None
        if oracle_identity_issuer_url is not None:
            parsed_issuer = urlparse(oracle_identity_issuer_url)
            if (
                parsed_issuer.scheme != "https"
                or not parsed_issuer.netloc
                or parsed_issuer.path not in {"", "/"}
                or parsed_issuer.query
                or parsed_issuer.fragment
            ):
                raise ConfigurationError(
                    "ORACLE_IDENTITY_ISSUER_URL must be the HTTPS identity-domain "
                    "issuer origin, for example https://<identity-domain>.identity."
                    "oraclecloud.com. Do not use the /ui/v1/signin page URL."
                )
        oracle_identity_client_id = os.getenv(
            "ORACLE_IDENTITY_CLIENT_ID",
            "",
        ).strip() or None
        oracle_identity_client_secret = os.getenv(
            "ORACLE_IDENTITY_CLIENT_SECRET",
            "",
        ).strip() or None
        oracle_identity_redirect_uri = os.getenv(
            "ORACLE_IDENTITY_REDIRECT_URI",
            "",
        ).strip() or None
        if oracle_identity_redirect_uri is not None:
            parsed_redirect = urlparse(oracle_identity_redirect_uri)
            local_development = (
                parsed_redirect.scheme == "http"
                and parsed_redirect.hostname in {"127.0.0.1", "localhost"}
            )
            if (
                not parsed_redirect.netloc
                or parsed_redirect.scheme != "https" and not local_development
                or parsed_redirect.query
                or parsed_redirect.fragment
            ):
                raise ConfigurationError(
                    "ORACLE_IDENTITY_REDIRECT_URI must be an absolute HTTPS "
                    "URL, or HTTP on localhost for local development, without "
                    "a query string or fragment."
                )
            if parsed_redirect.path.rstrip("/") != "/auth/oracle/callback":
                raise ConfigurationError(
                    "ORACLE_IDENTITY_REDIRECT_URI must end with "
                    "/auth/oracle/callback."
                )
            redirect_host = (parsed_redirect.hostname or "").casefold()
            if ".epm." in redirect_host and redirect_host.endswith(
                ".ocs.oraclecloud.com"
            ):
                raise ConfigurationError(
                    "ORACLE_IDENTITY_REDIRECT_URI must point to this automation "
                    "platform, not the Oracle EPM application host."
                )

        return cls(
            epm_base_url=base_url,
            epm_username=required_values["EPM_USERNAME"],
            epm_password=os.getenv("EPM_PASSWORD") or None,
            application_name=required_values["APPLICATION_NAME"],
            deployment_mode=deployment_mode,
            request_timeout=timeout,
            verify_ssl=verify_ssl,
            log_level=log_level,
            default_metadata_import_mode=metadata_import_mode,
            default_poll_interval=poll_interval,
            default_job_timeout=job_timeout,
            schedule_poll_interval=schedule_poll_interval,
            execution_runtime=execution_runtime,
            execution_worker_poll_interval=execution_worker_poll_interval,
            execution_lease_seconds=execution_lease_seconds,
            web_frontend_url=web_frontend_url,
            default_metadata_engine=metadata_engine,
            default_data_engine=data_engine,
            default_data_integration_engine=integration_engine,
            default_business_rule_engine=business_rule_engine,
            default_pipeline_engine=pipeline_engine,
            default_data_map_engine=data_map_engine,
            default_data_integration_name=integration_name,
            default_pipeline_code=pipeline_code,
            default_data_map_name=data_map_name,
            monthly_forecast_run_data_map=monthly_forecast_run_data_map,
            monthly_forecast_clear_target=monthly_forecast_clear_target,
            validation_source_form=validation_source_form,
            validation_target_form=validation_target_form,
            validation_tolerance=validation_tolerance,
            default_data_integration_import_mode=integration_import_mode,
            default_data_integration_export_mode=integration_export_mode,
            data_integration_catalog_file=catalog_file,
            pipeline_catalog_file=pipeline_catalog_file,
            database_url=database_url,
            runtime_data_dir=runtime_data_dir,
            planning_cycle_catalog_file=planning_cycle_catalog_file,
            report_output_dir=report_output_dir,
            report_catalog_file=report_catalog_file,
            planning_process_catalog_file=planning_process_catalog_file,
            epm_automate_executable=epm_automate_executable,
            epm_automate_password_file=password_file,
            epm_automate_command_timeout=epm_automate_timeout,
            agent_provider=agent_provider,
            agent_orchestrator=agent_orchestrator,
            agent_model=agent_model,
            gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip() or None,
            groq_api_key=os.getenv("GROQ_API_KEY", "").strip() or None,
            groq_max_input_tokens=groq_max_input_tokens,
            groq_max_completion_tokens=groq_max_completion_tokens,
            agent_max_tool_rounds=agent_max_tool_rounds,
            agent_history_messages=agent_history_messages,
            identity_provider=identity_provider,
            oracle_identity_issuer_url=oracle_identity_issuer_url,
            oracle_identity_client_id=oracle_identity_client_id,
            oracle_identity_client_secret=oracle_identity_client_secret,
            oracle_identity_redirect_uri=oracle_identity_redirect_uri,
            email_notifications=email_notifications,
        )

    @property
    def database_target(self) -> str | Path:
        """Return PostgreSQL runtime URL or an isolated test database path."""
        if self.database_url:
            return self.database_url
        if self.workflow_database_file is not None:
            return self.workflow_database_file
        raise ConfigurationError(
            "Database persistence is not configured. Set DATABASE_URL."
        )

    @property
    def runtime_storage_dir(self) -> Path:
        """Return runtime file storage, isolated beside test databases."""
        if (
            self.workflow_database_file is not None
            and self.runtime_data_dir == DEFAULT_RUNTIME_DATA_DIR
        ):
            return self.workflow_database_file.parent
        return self.runtime_data_dir

    def require_rest_password(self) -> str:
        """Return the REST password or raise a targeted configuration error."""
        if not self.epm_password:
            raise ConfigurationError(
                "EPM_PASSWORD is required when using the REST API."
            )
        return self.epm_password

    @property
    def federated_identity_ready(self) -> bool:
        """Return whether safe Oracle Cloud SSO metadata is configured."""
        return bool(
            self.identity_provider == "oracle_cloud"
            and self.oracle_identity_issuer_url
            and self.oracle_identity_client_id
        )

    @property
    def oracle_identity_discovery_url(self) -> str | None:
        """Return the standard OCI IAM/IDCS OpenID discovery endpoint."""
        return (
            f"{self.oracle_identity_issuer_url}/.well-known/openid-configuration"
            if self.oracle_identity_issuer_url
            else None
        )

    @property
    def resolved_deployment_mode(self) -> str:
        """Return the configured or hostname-inferred deployment mode."""
        if self.deployment_mode != "auto":
            return self.deployment_mode
        hostname = (
            urlparse(self.epm_base_url).hostname or ""
        ).casefold()
        if hostname.endswith(
            (
                ".oraclecloud.com",
                ".oraclecloudapps.com",
            )
        ):
            return "cloud"
        return "on_premises"

    def require_epm_automate_password_file(self) -> Path:
        """Return a validated encrypted EPM Automate password file."""
        path = self.epm_automate_password_file
        if path is None:
            raise ConfigurationError(
                "EPM_AUTOMATE_PASSWORD_FILE is required when using "
                "EPM Automate."
            )
        if path.suffix.lower() != ".epw":
            raise ConfigurationError(
                "EPM_AUTOMATE_PASSWORD_FILE must reference an encrypted "
                ".epw file."
            )
        if not path.is_file():
            raise ConfigurationError(
                f"EPM Automate password file does not exist: '{path}'."
            )
        return path

    @staticmethod
    def _normalize_and_validate_url(value: str) -> str:
        normalized = value.rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigurationError(
                "EPM_BASE_URL must be a valid HTTP or HTTPS URL."
            )
        if parsed.username or parsed.password:
            raise ConfigurationError(
                "EPM_BASE_URL must not contain embedded credentials."
            )
        return normalized

    @staticmethod
    def _parse_positive_float(name: str, value: str) -> float:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise ConfigurationError(
                f"{name} must be a number greater than zero."
            ) from exc
        if parsed <= 0:
            raise ConfigurationError(
                f"{name} must be a number greater than zero."
            )
        return parsed

    @staticmethod
    def _parse_bool(name: str, value: str) -> bool:
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
        raise ConfigurationError(
            f"{name} must be true or false."
        )

    @staticmethod
    def _parse_nonnegative_float(name: str, value: str) -> float:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise ConfigurationError(
                f"{name} must be a number greater than or equal to zero."
            ) from exc
        if parsed < 0:
            raise ConfigurationError(
                f"{name} must be a number greater than or equal to zero."
            )
        return parsed

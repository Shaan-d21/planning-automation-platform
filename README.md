# Oracle EPM Planning Automation

A production-oriented Python framework for Oracle EPM Planning automation.
The current release implements HTTP Basic Authentication, native Planning
metadata/data imports, file-based Data Integration, deployed Business Rule
execution, and Data Integration Pipeline execution through two engines:

- Oracle Planning REST APIs
- Oracle EPM Automate

Both engines can upload files, execute saved Import Metadata or Import Data
jobs, replace an existing file with the same name, and report failures. Native
data imports accept CSV, TXT, and ZIP files. The REST engine additionally
captures the Oracle job ID, polls job status, and retrieves structured parent
and child-job diagnostics.

Data Integration runs use Oracle's `INTEGRATION` REST job type or the EPM
Automate `runIntegration` command. The Python workflow is independent of the
dimension positions, delimiter, headers, amount columns, and import-format
type configured by the Oracle administrator.

Business Rules use Oracle's `RULES` REST job type or the EPM Automate
`runBusinessRule` command. Interactive execution lists discoverable rules,
supports exact-name manual entry, and displays a confirmation summary before
launch. The web platform can import a Calc Manager XML or LCM ZIP export into
an environment-scoped RTP registry. A synchronized rule renders ordered,
labelled prompt fields and validates mandatory values before Oracle submission;
rules without a registry entry retain the exact-name manual fallback.

Pipelines use Oracle's `pipeline` REST job type or the EPM Automate
`runPipeline` command. The framework retrieves the selected pipeline's live
definition, prompts from its configured runtime variables, and monitors the
REST process ID.

Data Maps use Oracle's `PLAN_TYPE_MAP` REST job type or EPM Automate
`runPlanTypeMap`. The REST engine supports target clearing, member overrides,
and exclusion overrides. A reusable Monthly Forecast workflow runs the
pipeline first, optionally publishes to the reporting cube through a Data Map,
and optionally validates matching source and target form slices. Every
workflow and step is recorded in PostgreSQL.

Planning form reports use Oracle's Export Form Data REST endpoint when the
environment supports it. Older environments can use an administrator-managed
definition from `config/reports.json`; interactive report generation includes
a one-time registration wizard so users do not need to edit JSON manually.
The framework then calls Export Data Slice and creates the same formatted
Excel workbook. Reports are stored locally and never require the unsupported
Management Reporting Library APIs.

The Planning Process Orchestrator combines approved operations into a
catalog-driven, one-click process. It reuses the existing cycle, Pipeline,
Business Rule, Data Map, validation, report, notification, and PostgreSQL history
services. Processes execute sequentially, stop at the first critical failure,
and persist the status and diagnostics of every step.

The enterprise web foundation provides a BISP Solutions-branded connection
gateway and responsive Planning Workspace. Its first increment reads the
live process catalog, PostgreSQL execution history, configured environment, and
generated report artifacts through application use cases shared with other
interfaces.

An optional provider-neutral notification layer sends success and failure
emails. SMTP is implemented for development; a corporate Microsoft Graph
provider can be added later without changing the Oracle workflows.

The EPM Assistant uses LangGraph for provider-neutral, stateful orchestration.
Google Gemini and Groq are supported model providers, selected through
environment configuration without rewriting the application workflow.
Conversations, messages, tool activity, action drafts, input snapshots, and
human approval decisions use the existing PostgreSQL application tables;
LangGraph checkpoints use separate PostgreSQL-managed checkpoint tables.

Agent releases also have a versioned deterministic evaluation gate covering
least-privilege routing, governed preparation, safety boundaries, and
conservative Oracle artifact matching. Run it with
`python -m app.agent.evaluation --fail-on-threshold`; see
[Agent Evaluation and Release Gate](docs/AGENT_EVALUATION.md) for the suite,
threshold policy, CI evidence, and separate live-provider UAT requirements.

The graph exposes a strict, permission-aware capability allow-list. It can
inspect the connected environment, discover Oracle artifacts, review recent
execution history and evidence, and use live Planning Data Review tools when
the connected Oracle version exposes the required metadata APIs. For an
authorized user, it can prepare exact inputs and submit the following work only
after the user approves the reviewed proposal in the platform UI:

- Business Rules, including supplied Calculation Manager runtime prompts;
- Oracle Pipelines and platform-managed multi-operation flows;
- Data Integrations, native Planning Data Import, and Metadata Import;
- Data Maps and saved Cube Refresh jobs; and
- application/cube substitution variables and Planning user variables.

Uploads, existing Oracle Inbox files, Pipeline variables, Data Integration
periods and modes, Data Map controls, and optional post-metadata refreshes are
collected through deterministic forms and revalidated immediately before the
execution is queued. The resulting work uses the same operation manager,
background worker, monitoring, evidence, record statistics, and permission
checks as the rest of the platform.

Conversation text is never treated as approval. The assistant cannot approve
its own proposal, bypass platform or Oracle permissions, generate arbitrary
REST calls, schedule work, delete Oracle objects, or silently retry an Oracle
write. Report generation currently uses a governed handoff to the Reports
workspace rather than direct agent execution. Natural-language Data Review is
read-only and depends on the cube, dimension, and member metadata made
available by the connected Oracle release.

## Requirements

- Python 3.11 or newer
- Network access to an Oracle EPM Cloud environment
- A Service Administrator or appropriately authorized EPM user
- An existing Planning Import Metadata or Import Data job definition
- A deployed Calculation Manager Business Rule when executing rules
- An existing Data Integration Pipeline and permission to run it
- An existing Planning Data Map and permission to run it
- An existing Planning form and permission to read it when generating reports
- Oracle EPM Automate installed locally when using the `epmautomate` engine

The supported release and deployment procedure, branch policy, health probes,
and rollback controls are documented in
[Release and Deployment Runbook](docs/RELEASE_RUNBOOK.md).

## Setup

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and provide the environment-specific values:

```dotenv
EPM_BASE_URL=https://your-epm-instance.example.com
# Backend integration identity used by workers and scheduled automation.
EPM_INTEGRATION_USERNAME=service.account
EPM_INTEGRATION_PASSWORD=service-account-password
# Optional fallback; leave blank for Oracle application discovery.
APPLICATION_NAME=

EPM_DEPLOYMENT_MODE=auto
EPM_REQUEST_TIMEOUT=30
EPM_VERIFY_SSL=true
LOG_LEVEL=INFO
DEFAULT_METADATA_IMPORT_MODE=job_definition
DEFAULT_POLL_INTERVAL=5
DEFAULT_JOB_TIMEOUT=1800
EPM_EXECUTION_RUNTIME=embedded
EXECUTION_WORKER_POLL_INTERVAL=2
EXECUTION_LEASE_SECONDS=120
DEFAULT_METADATA_ENGINE=rest
DEFAULT_DATA_ENGINE=rest
DEFAULT_DATA_INTEGRATION_ENGINE=epmautomate
DEFAULT_BUSINESS_RULE_ENGINE=rest
DEFAULT_PIPELINE_ENGINE=rest
DEFAULT_DATA_MAP_ENGINE=rest
DEFAULT_DATA_INTEGRATION_NAME=Test_DataLoad
DEFAULT_PIPELINE_CODE=PIPE01
DEFAULT_DATA_MAP_NAME=Product_Revenue_to_Reporting
MONTHLY_FORECAST_RUN_DATA_MAP=true
MONTHLY_FORECAST_CLEAR_TARGET=false
DATABASE_URL=postgresql+psycopg://epm_automation:password@localhost:5432/epm_automation
RUNTIME_DATA_DIR=var
PLANNING_CYCLE_CATALOG_FILE=config/planning_cycles.json
PLANNING_PROCESS_CATALOG_FILE=config/planning_processes.json
REPORT_OUTPUT_DIR=reports
REPORT_CATALOG_FILE=config/reports.json
WEB_SESSION_SECRET=replace-with-a-long-random-secret
WEB_SECURE_COOKIES=false
AGENT_PROVIDER=gemini
AGENT_ORCHESTRATOR=langgraph
AGENT_MODEL=gemini-3.5-flash-lite
GEMINI_API_KEY=
GROQ_API_KEY=
AGENT_MAX_TOOL_ROUNDS=4
AGENT_HISTORY_MESSAGES=20
LANGGRAPH_STRICT_MSGPACK=true
VALIDATION_SOURCE_FORM=
VALIDATION_TARGET_FORM=
VALIDATION_TOLERANCE=0
DEFAULT_DATA_INTEGRATION_IMPORT_MODE=Replace
DEFAULT_DATA_INTEGRATION_EXPORT_MODE=Merge
DATA_INTEGRATION_CATALOG_FILE=config/data_integrations.json
PIPELINE_CATALOG_FILE=config/pipelines.json

# Optional Gmail SMTP notifications for development/testing
EMAIL_NOTIFICATIONS_ENABLED=false
EMAIL_PROVIDER=smtp
EMAIL_SMTP_HOST=smtp.gmail.com
EMAIL_SMTP_PORT=587
EMAIL_SMTP_USERNAME=your.test.account@gmail.com
EMAIL_SMTP_PASSWORD=your-16-character-app-password
EMAIL_FROM=your.test.account@gmail.com
EMAIL_TO=your.recipient@example.com
EMAIL_USE_TLS=true
EMAIL_USE_SSL=false
EMAIL_SMTP_TIMEOUT=30

# Required only for the EPM Automate engine
EPM_AUTOMATE_EXECUTABLE=C:\Program Files\Oracle\EPM Automate\bin\epmautomate.bat
EPM_AUTOMATE_PASSWORD_FILE=C:\Secure\EPMPassword.epw
EPM_AUTOMATE_COMMAND_TIMEOUT=1800
```

To use Groq for the EPM Assistant, select it explicitly and use a Groq model
that supports local tool calling:

```dotenv
AGENT_PROVIDER=groq
AGENT_ORCHESTRATOR=langgraph
AGENT_MODEL=openai/gpt-oss-120b
GROQ_API_KEY=your-groq-api-key
```

To switch back to Gemini, set `AGENT_PROVIDER=gemini`, select a Gemini model,
and configure `GEMINI_API_KEY`. Restart the web application and begin a new
assistant conversation after changing providers.

Create an empty PostgreSQL database and apply the versioned schema before the
first application start:

```powershell
python -m pip install -r requirements.txt
alembic upgrade head
alembic current --check-heads
python -m app.agent.checkpoint_setup
```

Alembic automatically reads `DATABASE_URL` from the project-root `.env` file.
An operating-system environment variable with the same name takes precedence,
which allows deployment systems to inject production credentials securely.

The web application and CLI refuse to start when PostgreSQL is unversioned or
behind the code's Alembic head. Repositories never create or alter production
tables at runtime. See [Database Architecture](docs/DATABASE_ARCHITECTURE.md)
for the complete table design and migration policy.

`app.agent.checkpoint_setup` creates or upgrades LangGraph's PostgreSQL
checkpoint schema using LangGraph's idempotent setup routine. Run it after
Alembic during a new deployment and after upgrading LangGraph. These framework-
owned checkpoint tables are intentionally separate from the application tables
managed by Alembic.

If startup reports PostgreSQL password authentication failure, the server is
reachable but the password in `DATABASE_URL` does not match the PostgreSQL
role. Reset the role password as a PostgreSQL administrator, update `.env`,
and rerun `alembic upgrade head`. URL-encode reserved password characters such
as `@`, `:`, `/`, `#`, and `%` when placing them in a connection URL.

For Oracle EPM Cloud, `EPM_BASE_URL` should be the environment root, for
example `https://epm-test-example.epm.us-ashburn-1.ocs.oraclecloud.com`.
Do not add `/epmcloud`, `/HyperionPlanning`, or `/rest/v3`. Basic
Authentication accepts either the Cloud username or
`identitydomain.username`; use the form required by your identity-domain
configuration. `EPM_DEPLOYMENT_MODE=auto` recognizes Oracle Cloud hostnames
and enables Cloud-first capabilities. Set it explicitly to `cloud` when a
reverse proxy or custom hostname hides the Oracle Cloud hostname, or to
`on_premises` for an on-premises Planning server.

At startup, the platform first uses the application selection previously saved
for the configured base URL. When no selection exists, `APPLICATION_NAME` is
used as a compatibility fallback. If that is also blank, the integration
account calls Oracle's supported Get Applications REST API. A single returned
application is selected automatically; multiple applications are shown to a
Service Administrator under **Dashboard > Environment health > Manage**.
Changing the selected application requires one controlled API and worker
restart so in-flight and scheduled work cannot switch environments midway.
Only non-secret application metadata is stored in PostgreSQL.

The connection gateway validates both the Planning REST API and the resolved
application. A registered report uses its approved
`config/reports.json` data-slice definition consistently in Cloud and
on-premises environments. An unregistered Planning form name or ID uses
Oracle's newer Export Form Data API when that endpoint is available on the
connected Cloud environment.
`EPM_INTEGRATION_PASSWORD` is required for Basic Authentication. The legacy
`EPM_USERNAME` and `EPM_PASSWORD` names remain supported during migration.
Never commit `.env`.

### Enterprise web control center

The project has one supported presentation layer: `frontend/`, the React and
TypeScript business-user experience. FastAPI remains the only backend and owns
authentication, authorization, workflow rules, persistence, audit evidence,
and all Oracle EPM communication. The retired server-rendered UI has been
removed so interface behavior is defined in one place.

For development, start the FastAPI backend in one terminal:

```powershell
python web_main.py
```

`EPM_EXECUTION_RUNTIME=embedded` is the development default. Requests are
first committed to the durable database queue and are then executed by an
embedded worker, so local development uses the same queue contract as
production.

For production, set `EPM_EXECUTION_RUNTIME=web` for the API service and run
the durable worker as a separate supervised service:

```powershell
python -m uvicorn web_main:app --host 0.0.0.0 --port 8080
python worker.py
```

The web service validates and commits work only. `worker.py` claims jobs with
PostgreSQL row locking, renews their leases, runs Oracle EPM work, persists
terminal evidence, and polls due schedules. Multiple workers may run safely;
`FOR UPDATE SKIP LOCKED` prevents duplicate claims.

If a worker disappears during an Oracle action, the job becomes
`RECOVERY_REQUIRED` instead of being automatically repeated. Review Oracle's
Job Console before retrying because Oracle may already have accepted it.

Web and worker services must share `DATABASE_URL` and `RUNTIME_DATA_DIR`. Use
controlled shared storage for uploads, logs, and generated reports when they
run on different hosts.

Production supervisors and load balancers can use `GET /health/live` for
process liveness and `GET /health/ready` for API/PostgreSQL readiness. These
unauthenticated probes expose no Oracle URL, application name, credentials, or
database details. Authenticated `GET /api/v1/health` remains the live Oracle
connection check used by the product UI.

Then start the React development server in a second terminal:

```powershell
cd frontend
pnpm install
pnpm dev
```

Open `http://127.0.0.1:5173` for the interface. Vite proxies API, authentication,
downloads, and compatibility entry points to FastAPI at
`http://127.0.0.1:8080`. The connection
gateway verifies the server-managed Oracle EPM credentials before creating a
signed browser session. Oracle credentials are never returned to, or stored
in, the browser.

Set `WEB_FRONTEND_URL=http://127.0.0.1:5173` during local development. This
routes browser entry points into the React workspace. In production, set it to
the public HTTPS origin that serves `frontend/dist`. If it is omitted, FastAPI
serves the built React entry point from `frontend/dist` when that bundle exists.

All React requests use the typed `/api/v1` contract. Temporary unversioned
aliases remain available for older integrations and return `Deprecation` plus
`Link` response headers identifying the successor URL. This compatibility
bridge allows Excel macros and existing clients to migrate without duplicating
backend business logic.

The **Operations** workspace also exposes one environment-scoped Oracle
catalog status. An administrator can select **Sync from Oracle** to use one
authenticated session to discover current Business Rules, Data Maps, native
data and metadata jobs, Cube Refresh jobs, and Planning cubes while verifying
registered Pipelines and their Data Integrations. Selectors hide stale entries,
but the database retains their lifecycle history. Temporary Oracle connection
or permission failures preserve the last known-good state instead of deleting
registrations.

After upgrading an existing PostgreSQL installation, apply every pending
migration before starting the application:

```powershell
alembic upgrade head
alembic current --check-heads
```

Create a production frontend bundle with `pnpm build`. Deployment should serve
`frontend/dist` as static content and route `/api`, `/app`, `/setup`,
`/static`, `/login`, and `/logout` to FastAPI. See
[`frontend/README.md`](frontend/README.md) for the frontend boundary and test
commands.

The web application includes:

- BISP Solutions-branded connection experience
- Responsive Planning Workspace for business users
- Environment and application summary
- Catalog-driven Planning process overview
- Guided Planning-cycle input for Year, period range, Scenario, and Version
- Live, read-only Oracle preflight before execution
- Review of substitution-variable changes and Pipeline runtime variables
- Dynamic Pipeline file requirements discovered from the selected Pipeline
- Session-protected temporary uploads and existing Oracle Inbox file choices
- Explicit approval before any Oracle state is changed
- Background process execution with duplicate-run protection
- Live step status, progress, failure details, execution log, and report
  artifacts
- Standalone Operations & Audit workspace for consultant and administrator
  activities
- Live Substitution Variable workspace with application/cube scope filters,
  safe updates, explicit creation, stale-value protection, and Oracle
  verification
- Targeted live Cube Refresh discovery with impact guidance, explicit
  approval, background monitoring, diagnostics, and durable history
- Live Metadata Import discovery with local upload or existing Inbox
  selection, safe replacement, optional error output, and optional Cube
  Refresh
- Live native Planning Data Import discovery with CSV, TXT, or ZIP upload,
  existing Inbox reuse, safe replacement, and optional error output
- Live Business Rule discovery with optional Calculation Manager runtime
  prompts, governed XML/ZIP RTP synchronization, generated inputs, and
  server-side prompt validation
- Live Data Map discovery with clear-target protection, member overrides, and
  exclusion overrides
- Registered Pipeline selection with live stage, runtime-variable, and
  stage-specific file discovery
- File-based Data Integration execution with exact period ranges,
  import/export modes, local replacement uploads, and existing Inbox
  references
- Governed standalone execution with approval, Oracle job monitoring, email
  notification, logs, and durable history
- Searchable process catalog
- Searchable and filterable execution history
- Generated Excel report library with protected downloads
- Standalone report workspace with registered data-slice selection, optional
  Planning form name/ID export, layout preflight, dynamic POV inputs, background Excel
  generation, and protected artifact downloads
- One-page Planning Workspace combining process selection, optional run
  presets, cycle inputs,
  optional lifecycle controls, preflight, approval, live monitoring, logs,
  history, and generated artifacts
- Automation Designer with live Pipeline inspection, runtime-context policy,
  immutable draft versions, explicit activation, optional run presets, test
  execution, and automatic publication to the Planning Workspace
- Consistent badges, progress, dialogs, toasts, loading states, and empty
  states
- Mobile navigation and keyboard-accessible controls

To run a configured process:

1. Open **Planning Processes** and select **Prepare run**.
2. Enter the Planning-cycle values and choose the optional process controls.
3. Select **Run preflight**. This verifies the current Oracle artifacts without
   updating variables or starting jobs.
4. Review proposed substitution-variable changes, resolved Pipeline values,
   enabled steps, and all discovered file requirements.
5. For each required file, keep the Pipeline's configured Inbox reference,
   upload a replacement, or enter another existing Inbox filename.
6. Approve the reviewed plan and select **Start Planning process**.
7. Follow the live execution page until the process succeeds or fails. The
   page exposes step diagnostics, the process log, and generated reports.

For the normal business-user experience, open **Planning Workspace**:

1. Select an approved Planning process.
2. If a prepared run preset matches the required context, select **Run
   process**. The platform performs live preflight automatically and begins
   monitored execution without asking the user to re-enter saved values.
3. For a different context, enter Year, start/end periods, Scenario, and
   Version.
4. Choose supported optional steps such as Cube Refresh, Data Map, target
   clearing, and final report generation.
5. Run the read-only lifecycle preflight.
6. Resolve Pipeline runtime variables and required file inputs.
7. Review the complete execution sequence and approve it once.
8. Follow every live step, download logs, and retrieve generated Excel
   artifacts without leaving the control panel.

The Planning Workspace uses the same preflight, upload, execution, monitoring,
history, and report services as the dedicated process screens. It does not
introduce a second workflow engine, which keeps future web, Excel, scheduler,
and AI-agent clients on one governed automation core.

The web executor invokes the same governed Planning Process Orchestrator used
by the CLI, so job monitoring, fail-fast behavior, durable PostgreSQL history,
notifications, Data Map controls, validation, and reporting are consistent
across both interfaces. Only one active execution of the same process is
allowed by one web application instance.

### Schedule an approved Planning process

Open **Schedules** in the React workspace to automate an approved Oracle
Pipeline process without creating a second workflow definition. A schedule
stores when the process should start and which approved, unattended context it
should use; Oracle Pipeline continues to own the technical stage sequence.

1. Select **Create schedule** and choose an active Planning process.
2. Choose an unattended-ready run preset when the Pipeline needs saved runtime
   values or an existing Inbox file. Presets that require a new browser upload
   are intentionally unavailable for scheduling.
3. Choose one-time, daily, weekly, or monthly recurrence, the first run, and an
   IANA timezone such as `Asia/Kolkata`.
4. Select **Review schedule**. The server performs live read-only preflight,
   validates the file strategy, and shows the next occurrence in both the
   selected timezone and UTC.
5. Create the schedule enabled, or save it paused. From the schedule card an
   authorized user can run it now, pause/resume it, edit timing, or delete it.
6. Review the latest result and scheduled execution history from the same
   screen; **View execution** opens the corresponding Jobs & Activity record.

Schedules run complete approved Planning processes. Standalone operations such
as an isolated Cube Refresh or Metadata Import are not silently converted into
schedules; configure them as stages in the Oracle Pipeline when they belong to
the recurring lifecycle. Due schedules are claimed by the durable worker, so a
production deployment must keep at least one `worker.py` service running.

Browser uploads are held in session-scoped temporary storage and are removed
after execution. The configured upload limit is 100 MB per file. The Oracle
Inbox remains the system of record after the orchestrator uploads a selected
replacement.

### Design an Oracle Pipeline process

Open **Automation Designer** as the consultant and administrator configuration
workspace for existing Oracle EPM Pipelines. Oracle owns the stage sequence;
the platform adds a business-friendly name, runtime-context policy,
publication governance, optional presets, and testing:

1. Enter a business name and stable uppercase process code.
2. Select a registered Pipeline. If it is not listed, enter its exact Oracle
   Pipeline code and select **Find & register**.
3. Select **Inspect live Pipeline**. The platform retrieves the current Oracle
   stages, runtime variables, and file requirements without changing Oracle.
4. Choose whether the Planning Workspace should prompt for context or begin
   with Oracle Pipeline defaults.
5. Save the definition as an immutable draft version.
6. Select **Verify & activate**. The platform checks Oracle again before
   publishing that exact version.
7. Test from Automation Designer when needed. Normal execution belongs in the
   Planning Workspace.

After activation, optionally create one or more **Run Presets**. A preset is
not required to execute or schedule a process. It is useful only when the same
standard cycle context, Pipeline runtime variables, or file strategy is reused:

- Keep the Pipeline-configured Oracle file.
- Save another Oracle Inbox reference.
- Require a new local upload for every execution.

Presets appear as quick-start cards in Planning Workspace. A preset with no
runtime upload requirement is one-click ready. A preset that needs a fresh file
asks only for that file. Every path repeats live Oracle preflight and uses the
shared background execution manager. Automation Designer retains a clearly
labelled **Test preset** action for consultants. Preset removal is a soft
archive, so historical audit records remain intact.

Process lifecycle editing is intentionally simple. **Edit process** can change
the display name or registered Oracle Pipeline. If no values changed, the
request is rejected and no version is created. The first real change after an
activation creates the next draft version; any additional edits replace that
same open draft until it is activated. The version table can activate a newer
draft or reactivate an older version as a rollback. **Deactivate** removes the
process from runnable catalogs and the Planning Workspace while retaining its
versions, run presets, and execution history.

Designer-managed definitions are stored in the same server-side PostgreSQL
database used for workflow history. Only the active version joins the runtime
catalog; drafts and retired versions are never executable. The definition
contains two governed platform steps—preflight and Oracle Pipeline
execution—while the stage sequence, jobs, rules, integrations, and other
orchestration remain owned by the Pipeline in Oracle EPM. Editing or
activating a process does not modify the Oracle Pipeline. Year, period,
Scenario, and Version are optional for these Pipeline-only processes; the
live Pipeline determines which runtime variables and files are actually
required.

The former Planning Processes catalog is retained as a backward-compatible
route, but it is no longer a primary navigation destination. Process
configuration is centralized in Automation Designer; Planning Workspace is the
primary operating surface for planners and business users; Operations & Audit
is the standalone troubleshooting surface for technical consultants and
administrators.

### Run a standalone operation

Open **Operations & Audit** when a consultant needs to execute or troubleshoot
one activity without running the complete Planning lifecycle.

For a Business Rule:

1. Open **Business Rules**.
2. A Service Administrator can import a Calc Manager XML or LCM ZIP export in
   **Calc Manager RTP registry**. A failed parse preserves the last known-good
   definitions.
3. Select a rule retrieved from the live Oracle Planning job catalog.
4. For a synchronized rule, enter mandatory values and only the optional
   overrides needed for this run. Leaving an optional value empty lets Oracle
   use its configured default.
5. If no synchronized definition exists, use the exact-name manual fallback or
   run with Calculation Manager defaults.
6. Review the execution summary, approve it, and monitor the Oracle job.

For a Metadata Import:

1. Open **Metadata Import** and select a live saved Import Metadata job.
2. Upload a CSV or ZIP file, or enter an existing Oracle Inbox filename.
3. Ensure the filename matches the source configured in the saved Oracle job.
4. Optionally provide an error filename.
5. Optionally select a live Cube Refresh job. It starts only after a
   successful import and is monitored as a separate workflow step.
6. Review and approve the application change.

For a native Planning Data Import:

1. Open **Planning Data Import** and select a live saved Import Data job.
2. Upload a CSV, TXT, or ZIP file, or enter an existing Oracle Inbox filename.
3. Optionally provide an error output filename.
4. Review and approve the data change, then monitor the Oracle job.

The selected saved job remains responsible for the target cube, delimiter,
dimension columns, period columns, numeric layout, and import behavior. The
web workflow therefore supports delimited and multi-column numeric files with
different dimensionality without embedding a fixed file layout in Python.

For Substitution Variables:

1. Open **Substitution Variables** to retrieve every visible application- and
   cube-scoped definition directly from Oracle.
2. Search by name, value, or scope, or filter the catalog to one cube.
3. Select **Edit value** for an existing definition. The current value is
   checked again during execution so a newer administrator change is never
   silently overwritten.
4. Select **Create variable** only when a new definition is required. Choose
   `ALL`, a discovered cube, or enter an exact cube name when the Planning
   version cannot list plan types.
5. Review the scope, name, and value, approve the change, and follow its
   governed execution record.

Update and creation remain deliberately separate. Both operations verify the
saved Oracle value, produce an execution log, write durable history, and
publish the configured success or failure notification.

For a Planning Cube Refresh:

1. Open **Cube Refresh** and select a saved job discovered directly from
   Oracle Planning.
2. Review the metadata-readiness, active-user, and monitoring guidance.
3. Confirm the exact application and job in the approval dialog.
4. Start the refresh and follow Oracle's status on the execution page.

The browser does not create or modify the refresh definition. It executes the
selected saved `CUBE_REFRESH` job through REST, waits for a terminal Oracle
status, records diagnostics, and sends the configured completion notification.
Its catalog endpoint requests only Cube Refresh definitions, so unrelated
Oracle job categories do not delay or block the selector.

Some Oracle Cloud versions return a generic `RefreshCube` item from the public
job-definition endpoint instead of the saved Refresh Database names displayed
in Planning. In that case, select **Enter exact saved job name** and enter the
name exactly as shown in Oracle, including spaces and underscores. Execution
does not reject an exact name merely because the discovery endpoint omitted it;
Oracle remains the final authority that validates the saved job.

For Report Generation:

1. Open **Reports & Artifacts** and choose a registered data-slice report, or
   enter an exact Planning form name or ID.
2. Select **Inspect report layout**. Registered reports resolve from the
   approved catalog without calling the newer form endpoint. Unregistered
   forms retrieve their live axes and POV from Oracle when Export Form Data is
   available.
3. Review or override the displayed page-axis members and confirm the workbook
   title.
4. Review the resolved form, cube, POV, and unique Excel output, then approve
   generation.
5. Follow the background execution page and download the protected `.xlsx`
   workbook as soon as it succeeds, or retrieve it later from **Reports &
   Artifacts**.

If a form cannot be exported directly, choose **Register report** in the web
workspace. The guided dialog collects the report name, workbook title, exact
cube/plan type, optional default POV, and the row and column dimensions with
their members. Member selections use `|` as the separator so commas can remain
part of Oracle member names. The platform validates axis ownership and
duplicate names, saves the definition atomically to `config/reports.json`, and
immediately selects it for preflight. Registration changes only the local
automation catalog; it does not create or modify an Oracle artifact.

Standalone report generation uses the same `FormReportService`, Excel renderer,
notification policy, workflow history, and artifact directory as the complete
Planning process. Output filenames are generated server-side with a timestamp
and execution suffix, so browser users cannot write outside the configured
report directory or replace an existing workbook accidentally.

For a Data Map:

1. Open **Data Maps** and select a live Planning Data Map.
2. Decide whether the configured target region should be cleared.
3. Optionally enter dimension/member overrides and exclusion overrides.
4. Review the elevated-operation warning, approve it, and monitor completion.

For a Pipeline:

1. Open **Pipelines** and choose an administrator-registered Pipeline. If it
   is not listed, expand **Pipeline not listed?**, enter the exact Oracle code,
   and select **Verify & register**.
2. Select **Inspect Pipeline** to retrieve its current Oracle definition.
3. Review every stage and supply required runtime-variable values.
4. Resolve each discovered file requirement using its configured file, a
   local replacement upload, or another Inbox reference.
5. Approve the live definition and monitor the Pipeline job.

Oracle's public Pipeline details API requires an exact Pipeline code and does
not provide a supported list-all operation. `config/pipelines.json` therefore
acts as the seed allowlist. Pipelines verified from either the Pipelines page
or Automation Designer are added to the shared PostgreSQL catalog and remain
available after restart. Registration stores only the code, Oracle display
name, and descriptive metadata; the stages and runtime requirements are
always retrieved live from Oracle.

For a Data Integration:

1. Open **Data Integration** and choose a registered integration profile.
2. Enter the exact start and end period names.
3. Select the Oracle import and export modes.
4. Upload a CSV, TXT, or ZIP file, or preserve an existing Oracle Inbox
   reference. Local uploads are submitted using `#epminbox/`.
5. Review the file, period range, and modes before approval.

The Data Map definition remains in Oracle Planning. The web interface does not
create or redesign source/target mappings; it supplies only supported
run-time controls. Pipeline definitions also remain in Oracle; the local
Pipeline catalog stores only approved codes and display information. Data
Integration profiles remain administrator-managed in Oracle, while the local
catalog controls which names the web application exposes. Native Planning
Import Data jobs are discovered directly from Oracle. These operation types
use REST because EPM Automate is not required for the web workflows.
EPM Automate remains available through the CLI for environments that
explicitly require it.

Set a long random `WEB_SESSION_SECRET` before running the web application in a
shared environment. Set `WEB_SECURE_COOKIES=true` when the web application
itself is served over HTTPS.

### Oracle EPM sign-in and access mapping

For Oracle Cloud environments, the normal sign-in page accepts the user's
Oracle EPM username and password. The platform sends those credentials once
over HTTPS to the configured Oracle EPM environment to validate the login. It
does not log, encrypt, cache, or store the Oracle password. Enable this mode
with:

```dotenv
ORACLE_PASSWORD_LOGIN_ENABLED=true
```

The configured `EPM_INTEGRATION_USERNAME` integration account is then used for a read-only
lookup of that authenticated person's current Access Control profile. A
Platform Administrator must first:

1. open **Access Control** and preview/synchronize Oracle access;
2. map approved Oracle application roles, granular roles, or groups to one of
   the platform's four product roles; and
3. save those mappings.

At the person's first successful Oracle sign-in, the platform creates a
passwordless linked profile just in time. Later sign-ins refresh the profile
and its mapped product role from the current Oracle access. An administrator
may also use the optional combined preview to pre-provision linked profiles.
Existing local platform accounts remain available as recovery accounts.
Linked profiles are visibly identified in Access Control; their role and
password cannot be edited as local account fields because those profiles are
governed by the Oracle-to-platform mappings.

Oracle Access Control remains authoritative for forms, members, data, and EPM
operations. The mapped platform role controls only which automation-platform
features the person can use. This separation avoids incorrectly treating an
Oracle application role as a complete product authorization policy.

The integration account used for synchronization must be allowed to list
users and their roles in Oracle Cloud EPM Access Control. Oracle currently
requires Service Administrator access, or the documented Access Control
View/Manage application-role combination where supported by the environment.
The platform prefers Oracle's enriched List Users and Available Roles REST
resources. If an earlier Cloud monthly release returns users without enriched
access fields, it falls back to the compatible role and group reports rather
than clearing assignments. It retains only identity metadata, entitlement
assignments, mappings, linked profile IDs, and audit events.

#### Optional Oracle SSO

OCI IAM/IDCS federation remains available as a later passwordless upgrade. It
uses OpenID Connect Authorization Code flow with state, nonce, PKCE, discovery
metadata, signed ID-token validation, and a UserInfo subject check. Configure a
custom OCI IAM/IDCS application with:

- Authorization Code grant;
- redirect URI `https://<platform-host>/auth/oracle/callback`;
- scopes `openid profile email`;
- a client secret for a confidential web application, or a public PKCE client.

Then configure:

```dotenv
IDENTITY_PROVIDER=oracle_cloud
ORACLE_IDENTITY_ISSUER_URL=https://<identity-domain-url>
ORACLE_IDENTITY_CLIENT_ID=<registered-client-id>
ORACLE_IDENTITY_CLIENT_SECRET=<registered-client-secret>
ORACLE_IDENTITY_REDIRECT_URI=https://<platform-host>/auth/oracle/callback
WEB_SECURE_COOKIES=true
```

For this project's local Vite/FastAPI development setup, register and use
`http://127.0.0.1:8080/auth/oracle/callback`. The issuer value must be the
identity-domain origin (for example,
`https://<identity-domain>.identity.oraclecloud.com`), not the
`/ui/v1/signin` page URL. The OAuth client ID must come from the registered
OCI IAM/IDCS application. Use `WEB_SECURE_COOKIES=false` only for this local
HTTP setup; production HTTPS deployments must use `true`.

When SSO is configured it appears ahead of the Oracle credential form and uses
the same synchronized entitlements, explicit role mappings, platform
permissions, and audit trail. In the current SSO path, an administrator must
pre-provision the linked profile from the governed preview before its first
SSO login; Oracle credential login supports just-in-time provisioning.

### Prepare the EPM Automate password file

Create the encrypted password file yourself from a private terminal. Do not
place it inside the project:

```powershell
epmautomate encrypt "YOUR_PASSWORD" "YOUR_PRIVATE_KEY" "C:\Secure\EPMPassword.epw"
```

Set `EPM_AUTOMATE_PASSWORD_FILE` to the resulting `.epw` file. The framework
never sends the plaintext REST password to EPM Automate and never logs command
arguments. The repository ignores all `*.epw` files as an additional safeguard.

If `epmautomate` is already available on `PATH`, this is sufficient:

```dotenv
EPM_AUTOMATE_EXECUTABLE=epmautomate
```

Otherwise, use the complete path returned by:

```powershell
Get-Command epmautomate
```

## Interactive menu

Running without arguments opens the interactive menu:

```powershell
python main.py
```

```text
Oracle EPM Planning Automation

1. Check connection
2. Load metadata
3. Load data
4. Run Data Integration / Data Management
5. Run Business Rule
6. Run Data Integration Pipeline
7. Run Data Map
8. Run Monthly Forecast Workflow
9. Configure / Run Planning Cycle
10. View Workflow History
11. Refresh Planning Cube
12. Manage Substitution Variables
13. Generate Planning Form Report
0. Exit
```

For a local load, select metadata or data, choose local upload, and drag the
input file from Windows Explorer into the terminal. The menu asks whether to
execute through REST or EPM Automate. The framework retrieves the matching
saved `IMPORT_METADATA` or `IMPORT_DATA` jobs from Oracle and displays a
numbered job-selection list.

Business Rule execution retrieves definitions of type `RULES` and displays a
numbered selection list. If a user has launch access but cannot list job
definitions, the exact deployed rule name can be entered manually. Runtime
prompts use case-sensitive `NAME=VALUE` pairs; pressing Enter without prompts
uses Calculation Manager defaults.

The menu also supports using a CSV or ZIP file that is already present in the
Oracle Inbox, which skips the upload step.

## Non-interactive commands

Login verification for scripts and schedulers:

```powershell
python main.py login
```

Successful verification prints:

```text
Successfully connected to Oracle Planning.
```

Upload a CSV and execute the saved Planning job:

```powershell
python main.py metadata `
  --engine rest `
  --file "C:\metadata\Account.csv" `
  --job "Import Account Metadata"
```

Execute the same workflow through EPM Automate:

```powershell
python main.py metadata `
  --engine epmautomate `
  --file "C:\metadata\Account.csv" `
  --job "Import Account Metadata"
```

Upload a ZIP, override the saved job's ZIP input, and request an error file:

```powershell
python main.py metadata `
  --engine epmautomate `
  --file "C:\metadata\DailyMetadata.zip" `
  --job "Import Daily Metadata" `
  --error-file-name "DailyMetadataErrors.zip"
```

Execute a job using a file that was already uploaded to the Oracle Inbox:

```powershell
python main.py metadata `
  --inbox-file "DailyMetadata.zip" `
  --job "Import Daily Metadata"
```

### Native Planning data import

Upload CSV data and execute through REST:

```powershell
python main.py data `
  --engine rest `
  --file "C:\data\PlanData.csv" `
  --job "Import Plan Data" `
  --error-file-name "PlanDataErrors.zip"
```

Upload an Essbase-format TXT file through EPM Automate:

```powershell
python main.py data `
  --engine epmautomate `
  --file "C:\data\PlanData.txt" `
  --job "Import Plan Data"
```

Use a ZIP file already in the Oracle Inbox:

```powershell
python main.py data `
  --engine rest `
  --inbox-file "PlanData.zip" `
  --job "Import Plan Data"
```

The REST engine always sends Oracle's documented `importFileName` override.
The EPM Automate engine executes:

```text
epmautomate importData JOB_NAME FILE_NAME errorFile=ERROR_FILE.zip
```

Data load behavior such as overwrite, add, or subtract remains controlled by
the saved Planning Import Data job definition.

### Data Integration / Data Management load

One currently configured integration is `Test_DataLoad`:

- Target cube: `Plan1` (configured in Oracle Data Integration)
- Integration directory: `inbox/`
- Import mode: `Replace`
- Export mode: `Merge`

The framework does not duplicate Oracle's import-format or member-mapping
logic. It uploads the file, supplies the runtime period range, runs the
configured integration, and monitors the result. Oracle Data Integration
remains responsible for interpreting dimensions, delimiters, headers, amount
columns, mappings, file types, and the target application/cube.

Consequently, the same Python command can run integrations configured as:

- Delimited - Numeric
- Multi-Column - Numeric
- Delimited - All Data Type
- Multi-Column - All Data Type

Files may have dimensions in columns 1-3, 1-5, 1-6, or another layout. They
may also contain one or many amount/period columns. The chosen Oracle
integration definition must match the selected file.

Run through EPM Automate:

```powershell
python main.py integration `
  --engine epmautomate `
  --file "C:\data\Test_Sales_DataLoad_V2.csv" `
  --integration-name "Test_DataLoad" `
  --start-period "Jun-19" `
  --end-period "Aug-19"
```

Run through REST:

```powershell
python main.py integration `
  --engine rest `
  --file "C:\data\Test_Sales_DataLoad_V2.csv" `
  --integration-name "Test_DataLoad" `
  --start-period "Jun-19" `
  --end-period "Aug-19"
```

The generated EPM Automate operation is equivalent to:

```text
epmautomate runIntegration Test_DataLoad importMode=Replace exportMode=Merge periodName={Jun-19}{Aug-19} inputFileName=#epminbox/Test_Sales_DataLoad_V2.csv
```

When the framework uploads a local file to the default Applications
Inbox/Outbox repository, it automatically uses Oracle's required reference:

```text
inputFileName=#epminbox/Test_Sales_DataLoad_V2.csv
```

Existing Oracle files retain the location entered by the user:

| File location | Runtime reference |
|---|---|
| Applications Inbox/Outbox Explorer | `#epminbox/FileName.csv` |
| Data Integration inbox | `inbox/FileName.csv` |
| Directory already configured in the integration | `FileName.csv` |

The interactive workflow accepts all three forms. It does not remove the
directory prefix because `inbox` and `#epminbox` identify different Oracle
repositories.

The interactive menu lists the integrations configured in
`config/data_integrations.json`. Select one by number or choose **Enter another
integration name** for an integration that has not been added to the catalog.
The catalog is local because Oracle's supported public Data Integration APIs
do not provide an operation for listing every integration definition.

Add or update menu entries without changing Python code:

```json
{
  "integrations": [
    {
      "name": "Test_DataLoad",
      "description": "Sales data load"
    }
  ]
}
```

`DATA_INTEGRATION_CATALOG_FILE` can point to a different JSON catalog. Relative
paths are resolved from the project root. Integration names must be unique;
descriptions are optional.

The interactive menu also asks for the exact starting and ending period
names. A single-period load uses the same value for both. It
accepts Data Integration names such as `Jun-19`, Planning member notation such
as `Jun#FY19`, multi-period ranges, and cross-year ranges.

The previous `--metadata-file` and `--metadata-job-name` option names remain
available as aliases for backward compatibility.

### Business Rule execution

The Business Rule must already exist in Calculation Manager and be deployed
to the Planning application. The executing user must be a Service
Administrator or a Power User with launch access to the rule.

Run through REST with Calculation Manager default runtime prompts:

```powershell
python main.py rule `
  --engine rest `
  --rule "Calculate Vehicle Revenue"
```

Supply one or more runtime prompts by repeating `--rtp`:

```powershell
python main.py rule `
  --engine rest `
  --rule "Calculate Vehicle Revenue" `
  --rtp "Scenario=Plan" `
  --rtp "Version=Working" `
  --rtp "Entity=North America"
```

Run the same rule through EPM Automate:

```powershell
python main.py rule `
  --engine epmautomate `
  --rule "Calculate Vehicle Revenue" `
  --rtp "Scenario=Plan" `
  --rtp "Entity=North America"
```

Runtime prompt names must exactly match Calculation Manager, including case.
Values containing spaces are supported; quote the complete `NAME=VALUE`
argument in PowerShell. REST execution returns a job ID and uses the reusable
job monitor. EPM Automate execution uses the encrypted password file and
always attempts logout.

Business Rule execution is currently an independent command. Automatically
running a rule only after a particular data load succeeds will be implemented
later as an explicit workflow/orchestration feature.

### Data Integration Pipeline execution

The pipeline must already be created in Oracle Data Integration. The
framework uses the immutable pipeline **code**, not its display name. The
included catalog contains:

```json
{
  "pipelines": [
    {
      "code": "PIPE01",
      "name": "PL_ProductRevenueForecast",
      "description": "Product revenue forecast pipeline"
    }
  ]
}
```

Interactive execution retrieves the live pipeline definition and prompts in
Oracle's configured variable order. For `PIPE01`, Start Period and End Period
are required. Press Enter to accept the Oracle defaults for Import Mode,
Export Mode, Send Mail, and Attach Logs. Leave Send To blank while native
pipeline email is disabled.

Before asking for runtime variables, the framework inspects every stage, job,
and job parameter returned by Oracle. It discovers:

- Oracle variables whose validation type is `FILE`
- Text variables referenced by a file-consuming job parameter
- Fixed `fileName`, `inputFileName`, `importFileName`, and
  `importZipFileName` parameters on supported Integration and import jobs

This supports zero, one, or many external files without pipeline-code-specific
Python logic. Business Rules and other file-free stages do not cause an upload
prompt. Planning import jobs use Applications Inbox filenames directly. Data
Integration jobs use Oracle's `#epminbox/` prefix when consuming a local file
uploaded by this framework.

Data Integration has two distinct file locations:

| Integration file reference | Oracle location |
|---|---|
| `#epminbox/FileName.csv` | Applications Inbox/Outbox Explorer |
| `FileName.csv` with Integration Directory `inbox` | Data Integration home `inbox/` |

The framework uploads through the Applications Inbox REST API. A plain fixed
Integration filename can therefore read an older file from Data Integration
home even after an Applications Inbox upload succeeds. Pre-flight validation
rejects this unsafe combination instead of running with stale data.

For a Pipeline Integration job that accepts framework uploads:

1. Leave the underlying file-based integration's **Directory** option blank.
2. Create a Pipeline variable such as `DATA_FILE` with validation type
   **FILE**.
3. Set the Integration job's **File Name** parameter to `$DATA_FILE`.

For a selected local data file, the framework uploads the file and binds:

```text
DATA_FILE=#epminbox/<actual-file-name>
```

Metadata and other Planning import jobs retain their normal Applications
Inbox filenames without the `#epminbox/` prefix.

For each detected file, interactive execution offers:

```text
1. Upload a local file
2. Use a file already in the Oracle Inbox
```

All selections are validated and displayed in the final confirmation before
any upload occurs. Local uploads retain the existing safe replace behavior:
only the exact target filename is replaced.

Run non-interactively through REST:

```powershell
python main.py pipeline `
  --engine rest `
  --pipeline PIPE01 `
  --pipeline-upload `
    "DATA_FILE=D:\Loads\LatestSales.csv" `
  --pipeline-inbox `
    "METADATA_FILE=Product_Metadata_Load.csv" `
  --variable "STARTPERIOD=Jan-26" `
  --variable "ENDPERIOD=Mar-26" `
  --variable "IMPORTMODE=Replace" `
  --variable "EXPORTMODE=Merge" `
  --variable "SEND_MAIL=No" `
  --variable "ATTACH_LOGS=N"
```

`--pipeline-upload` uses `INPUT=LOCAL_FILE`. For Data Integration uploads,
`INPUT` is the Pipeline FILE variable name. Planning import jobs may also use a
configured fixed filename. `--pipeline-inbox` uses `INPUT=ORACLE_FILE`. Repeat
either option for pipelines with multiple external inputs.

Fixed Oracle file parameters default to their already-configured Inbox files
in non-interactive mode. Fixed Planning import files can be uploaded and
replaced. A fixed plain Data Integration filename cannot be replaced through
the Applications Inbox uploader; use a Pipeline FILE variable or keep using a
file already staged in Data Integration home.

Use `--param` as an alias for `--variable`. Custom variables configured by an
administrator are supported without code changes:

```powershell
python main.py pipeline --pipeline PIPE01 `
  --param "STARTPERIOD=Jan-26" `
  --param "ENDPERIOD=Jan-26" `
  --param "Scenario=Forecast"
```

To use EPM Automate, change the engine to `epmautomate`. Interactive
EPM Automate execution still uses REST to retrieve the variable definitions;
it can also perform detected uploads through the shared REST file service
before launching EPM Automate. Non-interactive EPM Automate execution can use
the encrypted `.epw` credentials alone and therefore assumes required files
have already been staged; use REST when non-interactive pre-flight uploads are
needed.

`config/pipelines.json` is a convenience catalog because Oracle's documented
Get Pipeline Details endpoint retrieves one exact code rather than listing all
pipelines. Manual code entry remains available. Oracle native pipeline email
can be enabled later with `SEND_MAIL`, `SEND_TO`, and `ATTACH_LOGS`; keeping
`SEND_MAIL=No` avoids duplicate messages while framework email is enabled.

`--engine` accepts `rest` or `epmautomate`. If it is omitted, metadata uses
`DEFAULT_METADATA_ENGINE` and data uses `DEFAULT_DATA_ENGINE`; both default to
`rest`. Pipelines use `DEFAULT_PIPELINE_ENGINE`, which also defaults to
`rest`.

### Data Map execution

The Data Map must already be defined in Planning. Interactive option 7 lists
saved `PLAN_TYPE_MAP` job definitions, asks whether the target region should
be cleared, displays a confirmation, and monitors the resulting job.

Run the current Product Revenue map without clearing the target:

```powershell
python main.py data-map `
  --engine rest `
  --data-map "Product_Revenue_to_Reporting" `
  --no-clear-target
```

Override the configured slice for one run:

```powershell
python main.py data-map `
  --engine rest `
  --data-map "Product_Revenue_to_Reporting" `
  --map-override "Period=Jan,Feb,Mar" `
  --map-override "Year=FY26" `
  --map-exclude "Entity=No Entity" `
  --no-clear-target
```

Member and exclusion overrides are REST-only. EPM Automate supports the saved
map definition and `clearData`:

```powershell
python main.py data-map `
  --engine epmautomate `
  --data-map "Product_Revenue_to_Reporting" `
  --no-clear-target
```

`--no-clear-target` is the safe default for the current workflow. Whether it
is appropriate in production depends on the Data Map slice and the required
replace semantics.

### Monthly Forecast workflow

Interactive option 8 runs the current planning process in this order:

```mermaid
flowchart LR
    A["Run PIPE01"] --> B{"Publish reporting data?"}
    B -->|Yes| C["Run Data Map"]
    B -->|No| E["Finish; reporting cube unchanged"]
    C --> D{"Validation forms configured?"}
    D -->|Yes| F["Compare source and target cells"]
    D -->|No| G["Record validation as skipped"]
    F --> H["Finish successfully"]
```

The engine is fail-fast: the Data Map never starts when the pipeline fails,
and validation never starts when the Data Map fails. A skipped Data Map is
reported explicitly. The complete run and every step are stored in
PostgreSQL under a unique execution ID.

Run non-interactively with the configured defaults:

```powershell
python main.py workflow `
  --pipeline PIPE01 `
  --data-map "Product_Revenue_to_Reporting" `
  --variable "STARTPERIOD=Jan-26" `
  --variable "ENDPERIOD=Mar-26" `
  --variable "IMPORTMODE=Replace" `
  --variable "EXPORTMODE=Merge" `
  --variable "SEND_MAIL=No" `
  --variable "ATTACH_LOGS=N"
```

All existing Pipeline file options remain available to the workflow. Add
`--skip-data-map` to run only the pipeline.

Automatic validation is enabled only when both validation forms are
configured:

```dotenv
VALIDATION_SOURCE_FORM=VF_ProductRevenue_Plan1
VALIDATION_TARGET_FORM=VF_ProductRevenue_VisASO
VALIDATION_TOLERANCE=0.01
```

Create the two forms in Planning with the same row dimensions, column
dimensions, member order, and POV. The framework exports both JSON grids,
compares every numeric cell, reports the first mismatches, and fails the
workflow when the absolute difference exceeds the tolerance. Smart View and
forms remain useful for investigation, but they are not the automated control.

### Planning Cycle Control

Interactive option 9 discovers current Oracle substitution variables,
including their `ALL` or cube-specific scope. On the first run, map any number
of discovered variables to these business roles:

- Year
- Start Period
- End Period
- Scenario
- Version

Mappings are saved in `config/planning_cycles.json`. Variables that were not
returned by Oracle cannot be created accidentally. Before execution, the
framework displays every current and proposed value and requires confirmation.
Changed variables are updated and verified through REST as the first audited
workflow step.

Planning member values remain separate from Data Integration period syntax.
For example:

```text
Cycle Year: FY26
Cycle Start Period: Jan
Cycle End Period: Mar

CurMonth substitution variable: Jan
Pipeline STARTPERIOD: Jan-26
Pipeline ENDPERIOD: Mar-26
```

Run non-interactively after the initial mapping is configured:

```powershell
python main.py cycle `
  --cycle MONTHLY_FORECAST `
  --year FY26 `
  --start-period Jan `
  --end-period Mar `
  --scenario Forecast `
  --version Working `
  --pipeline-upload "DATA_FILE=D:\Loads\Forecast.csv" `
  --pipeline-inbox "METADATA_FILE=Product_Metadata_Load.csv"
```

Planning Cycle Control intentionally uses REST. Existing Pipeline file
discovery, uploads, Data Map publishing, validation, notifications, and PostgreSQL
history are reused.

### Workflow history

Interactive option 10 lists recent executions and can display every stored
step. Equivalent non-interactive commands are:

```powershell
python main.py history --history-limit 20
python main.py history --execution-id EXECUTION_ID
```

History is stored in PostgreSQL. Technical request details
remain in rotating text logs. Every HTTP request receives a safe correlation
identifier in the `X-Request-ID` response header. The same identifier is
included in centralized application logs and frontend error references so an
operator can trace one browser failure without searching by timestamp alone.

Jobs & Activity records both the initiating platform user and the effective
Oracle integration username. Passwords and user session credentials are never
written to workflow history or request logs.

### Standalone substitution-variable maintenance

Interactive option 12 is independent of Planning Cycle configuration. It can:

- list every discovered variable with its exact scope and current value;
- update one existing variable selected by number; or
- explicitly create a new application- or cube-scoped variable.

Updates display the old and new value. Creation displays the scope, name, and
initial value with an additional warning. Both operations default to No at
confirmation and retrieve the variable again afterward to verify Oracle saved
the requested value.

Run read-only list mode:

```powershell
python main.py variables
```

Update an existing application-level variable:

```powershell
python main.py variables --set-subvar "ALL.CurYr=FY26"
```

Create a new application-level variable:

```powershell
python main.py variables --create-subvar "ALL.ForecastEnd=Mar"
```

Use the exact cube name for a cube-scoped definition:

```powershell
python main.py variables --create-subvar "Plan1.ForecastEnd=Mar"
```

Creation is deliberately separate from update. `--set-subvar` refuses an
unknown variable, while `--create-subvar` refuses a scoped name that already
exists. Variable names are entered without the leading `&`.

### Optional Cube Refresh

Cube Refresh is REST-only and executes an existing saved `CUBE_REFRESH` job.
It is not part of Monthly Forecast. Run it independently:

```powershell
python main.py refresh-cube --refresh-job "RefreshCube"
```

### Planning form Excel reports

Interactive option 13 asks for an exact Planning form name/ID or configured
report name. Registered reports are listed and can be selected by number. A
matching `config/reports.json` entry always uses its cube, axes, member
selections, and default POV with Export Data Slice. Otherwise, when Export
Form Data is available, the framework discovers the live form axes and POV.
The user can override catalog POV members at runtime without changing Python
code.

An artifact created under Oracle's **Reports** area is a different resource
from a Planning data form and cannot be passed as a form name. Task Manager
reports require Oracle's separate report-generation API and are outside this
form/data-slice workspace.

If a new form cannot be discovered through the environment's REST API, the
interactive flow offers a one-time registration wizard. It asks for:

1. Report title and cube/plan type
2. Optional default POV dimension/member pairs
3. Column dimensions and their member selections
4. Row dimensions and their member selections

The completed definition is validated and written atomically to
`config/reports.json`. After that, users only select the registered name and
choose runtime POV values. Use `|` between member selections in the wizard;
this allows member names themselves to contain commas.

The generated workbook contains:

- `Report`: report title, application, form, generation time, POV, and the
  complete Planning grid with numeric Excel cells
- `Report Metadata`: form axes, row/column/cell counts, generation time, and
  the resolved POV

Output files default to the configured `REPORT_OUTPUT_DIR` and include a
timestamp, so prior reports are not replaced. Existing files can be replaced
only after explicit interactive confirmation or with `--overwrite-report`.

Catalog definitions are the compatibility option for Planning environments
that expose REST v3 but do not provide the newer form-export resource. Row and
column selections are registered once, while POV dimensions such as Scenario,
Version, Entity, and Year remain interactive. Non-interactive commands and the
Planning Process Orchestrator deliberately do not launch setup prompts, so an
on-premises report must be registered once with interactive option 13 before
it is used in unattended automation.

Run non-interactively:

```powershell
python main.py report `
  --report-form "Product Revenue Report" `
  --report-title "Product Revenue Forecast" `
  --page-member "Scenario=Forecast" `
  --page-member "Version=Working" `
  --filter-member "Electronic_Items" `
  --report-output "reports\Product_Revenue_FY26.xlsx"
```

`--page-member` uses `DIMENSION=MEMBER` and may be repeated. Dimensions must
exist on the form page axis or configured report POV. If only some dimensions
are overridden, the live form or catalog supplies the remaining members.
`--filter-member` applies only to the newer Export Form Data backend; catalog
reports define their row and column member filters in `config/reports.json`.

### End-to-End Planning Process Orchestrator

Interactive option 14 runs an approved process from
`config/planning_processes.json`. The initial
`MONTHLY_FORECAST_PROCESS` contains:

1. Process preflight
2. Substitution-variable updates when values changed
3. Optional Cube Refresh
4. Pipeline execution
5. Optional standalone Business Rule steps
6. Optional Data Map publishing
7. Source-to-target validation when matching forms are configured
8. Excel report generation

Disabled or unavailable optional steps are recorded as `SKIPPED`. Any enabled
step failure stops the process, marks all later steps as skipped, records the
error in PostgreSQL, and triggers the existing failure notification.

Run non-interactively:

```powershell
python main.py process `
  --process MONTHLY_FORECAST_PROCESS `
  --year FY22 `
  --start-period Jan `
  --end-period Mar `
  --scenario Actual `
  --version Working
```

Use `--skip-data-map`, `--skip-report`, or `--run-refresh-cube` to control
optional steps for one execution. Use `--dry-run` to authenticate and validate
the cycle, variables, Pipeline, Data Map, and configured report without
updating Oracle or starting any job:

```powershell
python main.py process `
  --process MONTHLY_FORECAST_PROCESS `
  --year FY22 `
  --start-period Jan `
  --end-period Mar `
  --scenario Actual `
  --version Working `
  --dry-run
```

The CLI, React/FastAPI application, scheduler, Excel integration, and EPM
Assistant reuse the same typed application services, execution queue, and
monitoring infrastructure instead of implementing separate Planning logic.

After an interactive metadata load succeeds, the menu asks whether a refresh
should run. The default answer is No. If no saved refresh job exists, nothing
is executed and the framework explains what must be configured in Planning.

For unattended metadata processing:

```powershell
python main.py metadata `
  --engine rest `
  --file "D:\Metadata\Account.csv" `
  --job "Import Account Metadata" `
  --refresh-after-metadata `
  --refresh-job "RefreshCube"
```

For CSV files, the import filename in the saved Planning job must match the
uploaded filename. The EPM Automate engine runs `importMetadata JOB_NAME`
without incorrectly passing a CSV filename. For ZIP files, it passes the ZIP
filename as Oracle's documented optional override.

If a local upload has the same filename as an existing Oracle Inbox file, the
framework deletes that exact existing file through the Oracle Interop API and
retries the upload once. Unrelated upload errors never trigger deletion.
The EPM Automate engine provides equivalent behavior through `deleteFile` and
`uploadFile`.

Oracle's Planning REST API does not expose an `importMode` parameter for an
Import Metadata job. Member merge/clear behavior is configured in the saved
Planning job definition. The framework therefore supports the explicit
`job_definition` mode and does not send an undocumented REST field.

## Email notifications

Email notifications are disabled by default. For development and testing, use
a dedicated Gmail test account with two-step verification and a Google App
Password:

```dotenv
EMAIL_NOTIFICATIONS_ENABLED=true
EMAIL_PROVIDER=smtp
EMAIL_SMTP_HOST=smtp.gmail.com
EMAIL_SMTP_PORT=587
EMAIL_SMTP_USERNAME=epm.automation.test@gmail.com
EMAIL_SMTP_PASSWORD=abcdefghijklmnop
EMAIL_FROM=epm.automation.test@gmail.com
EMAIL_TO=your.address@example.com
EMAIL_USE_TLS=true
EMAIL_USE_SSL=false
EMAIL_SMTP_TIMEOUT=30
```

`EMAIL_SMTP_PASSWORD` must contain the App Password, not the normal Google
account password. Do not commit `.env`.

Multiple recipients can be separated with commas or semicolons:

```dotenv
EMAIL_TO=first@example.com,second@example.com
```

Terminal notifications are generated for:

- Connection checks
- Metadata loads
- Native Planning data loads
- Data Integration/Data Management loads
- Business Rule executions
- Data Integration Pipeline executions
- Planning Data Map executions
- Monthly Forecast workflow executions
- Planning form report generation

Messages include the status, environment, application, execution engine,
job/rule/integration name, file, period range, completion timestamp, duration,
and failure message when applicable. User-cancelled operations do not generate
a success email.

Email delivery is deliberately isolated from Oracle task status. If Gmail or
the network is unavailable, the notification error is logged, but a successful
Oracle job remains successful and the original Oracle failure remains the
reported failure.

The notification provider interface allows a future corporate SMTP relay or
Microsoft Graph adapter to replace Gmail without modifying metadata, data,
integration, or monitoring services.

## Test

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

All tests use mocked HTTP sessions and process runners. They never call a real
Oracle environment or installed EPM Automate utility.

## Beginner-friendly blog series

The implementation is explained progressively in
[From Scripts to a Framework: Oracle EPM Planning Automation with Python](docs/blog/README.md):

1. Secure login and end-to-end metadata loading
2. EPM Automate and native Planning data loading
3. Flexible Data Integration / Data Management loading
4. Success and failure email notifications
5. Business Rule execution with runtime prompts
6. Dynamic Data Integration Pipelines with safe file handling
7. Data Maps, validation, and the Monthly Forecast workflow
8. Planning Cycle Control, scoped substitution variables, history, and safe
   Cube Refresh
9. Planning form reporting and Excel output

Each article includes prerequisites, architecture diagrams, execution flows,
code examples, troubleshooting guidance, tests, and current limitations.

## Project structure

```text
.
├── app/
│   ├── clients/
│   │   └── epm_client.py
│   ├── automation/
│   │   ├── epm_automate_client.py
│   │   └── epm_automate_runner.py
│   ├── config/
│   │   ├── email_settings.py
│   │   └── settings.py
│   ├── models/
│   │   ├── business_rule.py
│   │   ├── command.py
│   │   ├── data_integration.py
│   │   ├── data_integration_catalog.py
│   │   ├── data_job.py
│   │   ├── epm_automate.py
│   │   ├── file_transfer.py
│   │   ├── job.py
│   │   ├── metadata_job.py
│   │   └── notification.py
│   ├── monitoring/
│   │   └── job_monitor.py
│   ├── services/
│   │   ├── business_rule_service.py
│   │   ├── data_integration_catalog_service.py
│   │   ├── data_integration_service.py
│   │   ├── data_service.py
│   │   ├── epm_automate_data_integration_service.py
│   │   ├── epm_automate_data_service.py
│   │   ├── epm_automate_business_rule_service.py
│   │   ├── epm_automate_metadata_service.py
│   │   ├── file_service.py
│   │   ├── job_service.py
│   │   ├── metadata_service.py
│   │   └── notification_service.py
│   └── utils/
│       ├── exceptions.py
│       └── logger.py
├── config/
│   └── data_integrations.json
├── docs/
│   └── blog/
├── tests/
├── .env.example
├── main.py
├── requirements.txt
└── requirements-dev.txt
```

## Design

- `EPMClient` owns Basic Authentication, the reusable HTTP session, JSON
  requests, binary streaming, response handling, and transport exceptions.
- `EPMAutomateRunner` safely executes argument lists with no command shell,
  captures output and exit codes, applies timeouts, and never logs arguments.
- `EPMAutomateClient` owns encrypted-file login, authenticated command
  execution, shared file replacement, and logout.
- `EPMAutomateMetadataService` owns metadata-specific validation and
  `importMetadata` command construction.
- `BusinessRuleService` submits documented REST `RULES` jobs and validates
  optional runtime prompts.
- `EPMAutomateBusinessRuleService` executes `runBusinessRule` with the same
  rule and runtime-prompt contract.
- `DataService` submits documented REST `IMPORT_DATA` jobs with
  `importFileName`.
- `EPMAutomateDataService` executes native CSV, TXT, and ZIP data imports
  through `importData`.
- `DataIntegrationService` submits and monitors REST `INTEGRATION` jobs.
- `EPMAutomateDataIntegrationService` executes `runIntegration` with validated
  modes, periods, and input filenames.
- `FileService` validates and streams reusable CSV/ZIP uploads through the
  Oracle Interop API.
- `MetadataService` creates the documented `IMPORT_METADATA` payload and
  returns a typed submission containing the job ID.
- `JobService` normalizes Planning job responses and retrieves parent and child
  diagnostics. It also retrieves saved job definitions for interactive
  selection.
- `JobMonitor` owns polling, timeout enforcement, terminal-state handling, and
  failure diagnostic collection.
- `NotificationService` isolates delivery policy so email failures cannot
  change Oracle task results.
- `SMTPNotificationProvider` sends development notifications through Gmail;
  future corporate providers implement the same provider interface.
- `DataMapService` submits documented `PLAN_TYPE_MAP` jobs with safe clear and
  member-selection overrides.
- `DataValidationService` exports matching Planning forms and compares their
  numeric grids.
- `WorkflowEngine` provides ordered, fail-fast execution while
  `SQLWorkflowRepository` keeps durable run and step history.
- Models isolate Oracle payload shape from the rest of the application.
- `main.py` remains the composition root. Its menu and non-interactive commands
  call the same application services.

Additional Oracle operations, scheduled workflows, and agent capabilities can
reuse either `EPMClient` or `EPMAutomateRunner` without duplicating transport,
process, or error-handling logic.

## Excel Pipeline Runner

The first governed Excel interface is available under
`outputs/excel_pipeline_runner`. It invokes an existing Oracle Pipeline through
the FastAPI backend; it does not connect to Oracle or store Oracle credentials
inside the workbook.

The integration provides:

- live Pipeline preflight through a scoped bearer-token endpoint;
- exact Pipeline variable and existing-Inbox-file mappings;
- explicit confirmation before submission;
- execution polling and a small local workbook history;
- the same PostgreSQL execution audit used by web-triggered operations; and
- revocable, expiring API tokens whose secrets are never stored in plaintext.

Apply migration `0003_external_api_tokens`, create the token with
`python -m app.cli.api_tokens`, and follow the complete beginner instructions
in `outputs/excel_pipeline_runner/README.md`. The workbook contains no
automatic `Workbook_Open` execution and this version does not upload local
files from Excel.

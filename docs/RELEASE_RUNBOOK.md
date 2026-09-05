# Release and Deployment Runbook

This runbook is the release baseline for the BISP Solutions Oracle EPM
Automation Platform. It covers the application code, React bundle, PostgreSQL
schema, LangGraph checkpoint schema, API service, and durable worker.

## Supported release runtime

- Python 3.11 through 3.13
- Node.js 22.13 or a supported newer LTS release
- pnpm 11
- PostgreSQL 16 or a compatible supported PostgreSQL release
- HTTPS access from the backend to the customer Oracle EPM environment

EPM Automate is optional. A deployment needs it only when an enabled operation
explicitly selects the `epmautomate` engine. REST-based operations do not
require an EPM Automate installation.

## Repository and branch policy

`main` is the protected, releasable branch. Use short-lived branches such as
`codex/feature-name` or `feature/feature-name`, open a pull request, and merge
only after the Release checks workflow passes. Use immutable Git tags such as
`v0.1.0` for deployed releases.

Do not maintain different source code in `dev` and `prod` branches. Development,
test, and production are deployment environments configured with different
secret values. Long-lived environment branches tend to drift and make fixes
difficult to promote safely.

## Pull-request release gate

The GitHub Actions workflow runs:

1. The complete backend test suite on Python 3.11 and 3.13.
2. Every Alembic migration against a clean PostgreSQL 16 database.
3. LangGraph checkpoint-schema initialization.
4. The complete frontend test suite and strict production build.

Never place Oracle credentials, database passwords, AI keys, SMTP passwords,
or encrypted EPM Automate password files in GitHub Actions YAML or committed
configuration files.

## Pre-deployment checklist

1. Confirm that the intended commit is on `main` and its CI checks passed.
2. Create a PostgreSQL backup using the organization's approved backup tool.
3. Preserve the current application `.env` or secret-manager version.
4. Confirm that API and worker instances use the same `DATABASE_URL`,
   `RUNTIME_DATA_DIR`, Oracle environment, and application selection.
5. Confirm that `WEB_SESSION_SECRET` is long, random, and unchanged during a
   rolling deployment unless all browser sessions should be invalidated.
6. Set `WEB_SECURE_COOKIES=true` whenever the browser reaches the platform over
   HTTPS.
7. Build from a clean checkout; do not deploy local `var/`, `logs/`, test
   caches, `.env`, or `frontend/node_modules/`.

## Build and verify

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest

Set-Location frontend
pnpm install --frozen-lockfile
pnpm verify
Set-Location ..
```

The CI PostgreSQL migration job is authoritative for clean-schema migration.
Before promoting an existing environment, also check that environment directly:

```powershell
python -m alembic current --check-heads
```

## Deployment order

Use this order so scheduled work and durable executions cannot run with mixed
code and database versions:

1. Stop schedule/execution workers from claiming new work.
2. Allow running Oracle operations to finish or record them for controlled
   recovery.
3. Stop the old API and worker services.
4. Deploy the tagged application code and install production dependencies.
5. Apply the application schema and initialize LangGraph checkpoints:

   ```powershell
   python -m alembic upgrade head
   python -m alembic current --check-heads
   python -m app.agent.checkpoint_setup
   ```

6. Build the React application:

   ```powershell
   Set-Location frontend
   pnpm install --frozen-lockfile
   pnpm build
   Set-Location ..
   ```

7. Start the API with `EPM_EXECUTION_RUNTIME=web`:

   ```powershell
   python -m uvicorn web_main:app --host 0.0.0.0 --port 8080
   ```

8. Start one or more separately supervised durable workers:

   ```powershell
   python worker.py
   ```

9. Configure the load balancer or service supervisor to use:

   - `GET /health/live` for process liveness
   - `GET /health/ready` for API and PostgreSQL readiness

10. Sign in as an administrator and verify Dashboard Environment Health,
    Oracle catalog access, execution queue processing, and one low-risk
    read-only operation.

The readiness endpoint intentionally does not call Oracle. A temporary Oracle
outage must not cause the API to restart repeatedly or prevent administrators
from opening the platform to diagnose the environment.

## Rollback

Prefer a forward fix. If code rollback is required:

1. Stop API and worker services.
2. Confirm whether the previous code understands the current database schema.
3. Restore the pre-deployment PostgreSQL backup when a database rollback is
   required. Do not run an Alembic downgrade against production without an
   approved, tested data-preservation plan.
4. Deploy the previous immutable Git tag and its matching frontend bundle.
5. Start the API and worker, then repeat readiness and functional checks.

An Oracle job already submitted before a rollback must be reconciled against
Oracle Job Console and platform execution evidence. Never automatically repeat
an uncertain write operation.

## Release evidence

For every release record:

- Git tag and commit SHA
- CI workflow URL and result
- PostgreSQL backup reference
- Alembic revision before and after deployment
- Deployment start/end time and operator
- API and worker health-check result
- Known issues and rollback decision, if any

When investigating an API or browser failure, capture the `X-Request-ID`
response value or the **Reference** shown in the UI. Search the API logs for
`request=<value>` to correlate the request, response status, duration, and any
Oracle/client exception without exposing credentials.

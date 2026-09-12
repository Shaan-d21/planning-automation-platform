# Hetzner and Coolify staging deployment

This deployment is intentionally scoped to the `stage` branch. It builds the
React application into the Python image and runs PostgreSQL, migrations, the
FastAPI web service, and the durable execution worker through one Coolify
Docker Compose resource.

Oracle EPM Automate is not installed in this release. Every default execution
engine in `docker-compose.coolify.yml` is therefore set to `rest`.

## One-time Coolify setup

1. Create an x86-64 Hetzner server with the Coolify image and complete the
   Coolify administrator setup.
2. Point `automation.bispsolutions.com` to the server's public IP address.
3. In Coolify, create an `EPM Automation` project and a `Stage` environment.
4. Add a private GitHub repository through a GitHub App or deploy key.
5. Select the `stage` branch and the Docker Compose build pack.
6. Set the Compose file to `/docker-compose.coolify.yml`.
7. Add the values from `deploy/coolify.env.example` in Coolify's environment
   variable screen. Do not upload or commit a production `.env` file.
   `APPLICATION_NAME` must contain the exact Planning application name for
   this stage release so Oracle credential sign-in is enabled immediately.
8. Assign `https://automation.bispsolutions.com:8080` to the `web` service.
   The public browser URL remains the normal HTTPS URL without `:8080`.
9. Keep Coolify's repository auto-deploy disabled. GitHub Actions triggers the
   deployment only after every release check succeeds.

## GitHub staging deployment secret

Copy the authenticated Deploy Webhook URL from the Coolify application and
create this GitHub Actions repository secret:

```text
COOLIFY_STAGING_DEPLOY_WEBHOOK
```

When the secret is absent, stage CI still verifies the complete application
and container image but deliberately skips deployment.

After adding the secret, either push another reviewed stage change or manually
run the `Release checks` workflow against the `stage` branch.

## First deployment

1. Confirm the GitHub `Release checks` workflow passes on `stage`.
2. Open the Coolify deployment and verify that `postgres` becomes healthy and
   `migrate` exits successfully.
3. Verify that `web` and `worker` remain running.
4. Open `/health/live`, followed by `/health/ready`.
5. On a new database, create the initial local Platform Administrator when
   the guarded bootstrap screen appears.
6. Sign in, configure the required Oracle role mappings, synchronize the
   Oracle catalog, and run one low-risk REST operation.
7. Create a short test schedule and confirm that the worker claims it once.

## Persistent data and backups

The Compose resource owns three named volumes: `postgres_data`,
`runtime_data`, and `report_data`. Configure daily PostgreSQL backups in
Coolify and copy them to storage outside the Hetzner server. A local backup on
the same VPS is not sufficient disaster recovery.

The API and worker intentionally share the runtime and report volumes. Do not
scale the web service beyond one replica without first moving those files to
shared object storage.

## Local Docker test

The local launcher reuses the existing project-root `.env` without copying or
printing its secrets. It replaces only the database connection with an
isolated PostgreSQL container and publishes the application on localhost.

Start Docker Desktop, then run from the repository root:

```powershell
.\scripts\docker-local.ps1 start
```

Open `http://127.0.0.1:8080` after the `web` service reports healthy. Useful
follow-up commands are:

```powershell
.\scripts\docker-local.ps1 status
.\scripts\docker-local.ps1 logs
.\scripts\docker-local.ps1 stop
```

`stop` preserves the PostgreSQL, runtime, and report volumes. Do not add
Docker's `--volumes` option unless the local test data is intentionally being
discarded.

## Updating stage

Push reviewed changes to `stage`. GitHub Actions runs backend tests, database
migration checks, agent evaluation, frontend tests, the production frontend
build, Compose validation, and a complete Docker image build. A successful run
then calls the Coolify deploy webhook.

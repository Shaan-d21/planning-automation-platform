# Direct Docker deployment

This deployment runs the `stage` branch directly through Docker Compose,
without a provider-specific deployment service. It starts four required containers:

- `postgres`: the durable application database;
- `migrate`: a one-time database and LangGraph checkpoint initializer;
- `web`: the React build served by FastAPI;
- `worker`: queued operation and schedule execution.

An optional `cloudflared` profile publishes the web service through a remotely
managed Cloudflare Tunnel. Oracle EPM Automate is not installed; the Compose
configuration selects REST for every default Oracle operation engine.

## Host choices

For Windows 10 or 11, install Docker Desktop with Linux containers and WSL 2.
For Windows Server, run Ubuntu Server 24.04 in Hyper-V or another supported
hypervisor, then install Docker Engine in that VM. Docker Desktop is not
supported on Windows Server.

Recommended capacity is 4 CPU cores, 8 GB RAM, and 80 GB of SSD storage.

## First deployment

1. Clone the repository and check out `stage`.
2. Copy `deploy/server.env.example` to `.env.docker` in the repository root.
3. Replace every placeholder in `.env.docker`. Use a long URL-safe value for
   `POSTGRES_PASSWORD`; Compose constructs the internal database URL from it.
4. Keep `APP_BIND_ADDRESS=127.0.0.1` when Cloudflare Tunnel is used. This stops
   users from bypassing the tunnel by connecting to port 8080 directly.
5. Validate and start the required services:

   ```text
   docker compose --env-file .env.docker config
   docker compose --env-file .env.docker up -d --build
   docker compose --env-file .env.docker ps
   ```

6. Open `http://127.0.0.1:8080/health/ready` on the server. The `migrate`
   container should finish with exit code 0; `postgres`, `web`, and `worker`
   should remain running.
7. On a fresh database, complete the guarded Platform Administrator bootstrap,
   configure Oracle role mappings, sync the Oracle catalog, and run one
   low-risk operation.

The Windows development helper remains available:

```powershell
.\scripts\docker-local.ps1 start
.\scripts\docker-local.ps1 status
.\scripts\docker-local.ps1 logs
.\scripts\docker-local.ps1 stop
```

The local helper uses `docker-compose.local.yml` to permit HTTP login during
localhost-only testing. The normal server configuration retains secure cookies
for the public HTTPS address.

## Cloudflare Tunnel

Create a remotely managed tunnel in the Cloudflare dashboard and copy its
connector token into `.env.docker` as `CLOUDFLARE_TUNNEL_TOKEN`. Add a published
application route for the required hostname and use this service URL:

```text
http://web:8080
```

Because `cloudflared` runs inside the Compose network, `localhost` must not be
used as the route's service URL. Start the complete stack with the tunnel:

```text
docker compose --env-file .env.docker --profile tunnel up -d --build
docker compose --env-file .env.docker --profile tunnel ps
docker compose --env-file .env.docker --profile tunnel logs -f cloudflared web worker
```

No inbound router port forwarding is required. The host must permit outbound
HTTPS and Cloudflare Tunnel traffic, and it must be able to reach the configured
Oracle EPM environment.

## Updates

Deploy a tested `stage` update from the server checkout:

```text
git fetch origin
git switch stage
git pull --ff-only origin stage
docker compose --env-file .env.docker --profile tunnel up -d --build
```

GitHub Actions validates tests, migrations, the frontend build, Compose syntax,
and the Docker image. Direct server deployment is intentionally manual; there
is no provider webhook.

## Persistence and backup

The Compose project owns three named volumes: `postgres_data`, `runtime_data`,
and `report_data`. A normal `docker compose --env-file .env.docker down`
preserves them. Never use the `--volumes` option unless all application data is
intentionally being deleted.

`POSTGRES_PASSWORD` initializes the database user only when `postgres_data` is
created. Changing that value later does not rotate the password already stored
inside PostgreSQL. Rotate the database role password first, or keep the original
value; otherwise the `migrate` container will report password authentication
failure while the existing volume is retained.

Back up PostgreSQL and any required reports to storage outside the physical
server every day. A Docker volume on the same disk is persistence, not disaster
recovery. Also configure the VM, Docker service, and Cloudflare connector to
start automatically after a host reboot.

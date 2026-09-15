[CmdletBinding()]
param(
    [ValidateSet("start", "stop", "status", "logs")]
    [string]$Action = "start",
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$environmentFile = Join-Path $projectRoot ".env"
$productionCompose = Join-Path $projectRoot "docker-compose.yml"
$localCompose = Join-Path $projectRoot "docker-compose.local.yml"

if (-not (Test-Path -LiteralPath $environmentFile)) {
    throw "Create '$environmentFile' from .env.example before starting Docker."
}

foreach ($rawLine in Get-Content -LiteralPath $environmentFile) {
    $line = $rawLine.Trim()
    if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
        continue
    }

    $parts = $line.Split("=", 2)
    $name = $parts[0].Trim()
    $value = $parts[1].Trim()
    if (
        $value.Length -ge 2 -and
        (($value.StartsWith('"') -and $value.EndsWith('"')) -or
         ($value.StartsWith("'") -and $value.EndsWith("'")))
    ) {
        $value = $value.Substring(1, $value.Length - 2)
    }

    if (-not [Environment]::GetEnvironmentVariable($name, "Process")) {
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

$requiredNames = @(
    "EPM_BASE_URL",
    "EPM_INTEGRATION_USERNAME",
    "EPM_INTEGRATION_PASSWORD"
)
$missingNames = @(
    $requiredNames | Where-Object {
        -not [Environment]::GetEnvironmentVariable($_, "Process")
    }
)
if ($missingNames.Count -gt 0) {
    throw "Set these required values in .env: $($missingNames -join ', ')."
}

$localDatabasePassword = [Environment]::GetEnvironmentVariable(
    "LOCAL_DOCKER_POSTGRES_PASSWORD",
    "Process"
)
if (-not $localDatabasePassword) {
    $localDatabasePassword = "local-docker-only-epm-automation"
}
if ($localDatabasePassword -notmatch "^[A-Za-z0-9_-]+$") {
    throw "LOCAL_DOCKER_POSTGRES_PASSWORD may contain only letters, numbers, '_' and '-'."
}

[Environment]::SetEnvironmentVariable(
    "POSTGRES_PASSWORD",
    $localDatabasePassword,
    "Process"
)
[Environment]::SetEnvironmentVariable(
    "DATABASE_URL",
    "postgresql+psycopg://epm_automation:${localDatabasePassword}@postgres:5432/epm_automation",
    "Process"
)

if (-not [Environment]::GetEnvironmentVariable("WEB_SESSION_SECRET", "Process")) {
    $temporarySessionSecret = (
        [Guid]::NewGuid().ToString("N") +
        [Guid]::NewGuid().ToString("N")
    )
    [Environment]::SetEnvironmentVariable(
        "WEB_SESSION_SECRET",
        $temporarySessionSecret,
        "Process"
    )
}

$null = Get-Command docker -ErrorAction Stop
& docker info --format "{{.ServerVersion}}" *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Engine is not running. Start Docker Desktop and run this command again."
}

$composeArguments = @(
    "compose",
    "-f", $productionCompose,
    "-f", $localCompose
)

switch ($Action) {
    "start" {
        & docker @composeArguments config --quiet
        if ($LASTEXITCODE -ne 0) {
            throw "Docker Compose configuration validation failed."
        }

        $upArguments = @("up", "--detach", "--remove-orphans")
        if (-not $NoBuild) {
            $upArguments += "--build"
        }
        & docker @composeArguments @upArguments
        if ($LASTEXITCODE -ne 0) {
            throw "The local Docker environment could not be started."
        }
        & docker @composeArguments ps
        Write-Output "Open http://127.0.0.1:8080 after the web service is healthy."
    }
    "stop" {
        & docker @composeArguments down
        if ($LASTEXITCODE -ne 0) {
            throw "The local Docker environment could not be stopped."
        }
    }
    "status" {
        & docker @composeArguments ps
    }
    "logs" {
        & docker @composeArguments logs --follow web worker migrate postgres
    }
}

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

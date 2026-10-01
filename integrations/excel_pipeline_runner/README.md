# Excel Pipeline Runner — Beginner Setup

This workbook invokes an existing Oracle EPM Pipeline through the BISP EPM
Automation backend. Excel never connects to Oracle directly and never stores
an Oracle username or password. The backend performs live preflight, applies
platform authorization, starts the Pipeline, monitors it, and writes the run
to the shared execution history.

The first version deliberately uses files that already exist in the Oracle
Inbox. Local-file upload from Excel is outside this test case.

## Files

- `Oracle_EPM_Pipeline_Runner.xlsx` — the formatted runner workbook.
- `BispEpmPipeline.bas` — the VBA module to import into the workbook.

## 1. Prepare the backend once

Open PowerShell in the project directory and apply the latest database
migration:

```powershell
alembic upgrade head
alembic current --check-heads
```

Start the platform:

```powershell
python web_main.py
```

Keep this PowerShell window open. For the local test, the backend address is
`http://127.0.0.1:8080`.

## 2. Create a revocable Excel API token

Use the same **platform username** that you use to sign in to the BISP web
application. It must have permission to execute operations and view history.

```powershell
python -m app.cli.api_tokens create --username YOUR_PLATFORM_USERNAME --name "Excel Pipeline Runner" --expires-days 30
```

The command prints the secret exactly once. Copy it immediately. The database
stores only its one-way hash.

Save the secret in your Windows user environment, not in Excel:

```powershell
[Environment]::SetEnvironmentVariable(
    "BISP_EPM_EXCEL_TOKEN",
    "PASTE_THE_NEW_TOKEN_HERE",
    "User"
)
```

Fully close every Excel window and reopen Excel so that it sees the new
environment variable. Never paste this token into a cell, VBA source, email,
or chat message.

## 3. Create the macro-enabled workbook

1. Open `Oracle_EPM_Pipeline_Runner.xlsx` in desktop Microsoft Excel.
2. Select **File > Save As**.
3. Choose **Excel Macro-Enabled Workbook (`*.xlsm`)**.
4. Save it as `Oracle_EPM_Pipeline_Runner.xlsm` in a trusted local folder.
5. If the **Developer** tab is hidden, open **File > Options > Customize
   Ribbon**, select **Developer**, and choose **OK**.
6. Press **Alt+F11** to open the Visual Basic Editor.
7. In the editor, select **File > Import File**.
8. Choose `BispEpmPipeline.bas` from this folder.
9. Confirm that `BispEpmPipeline` appears under **Modules** in the Project
   Explorer.
10. Press **Ctrl+S**, then close the Visual Basic Editor.

Do not select Excel's global **Enable all macros** setting. Enable this signed
or explicitly trusted workbook only. If Windows marks the downloaded file as
blocked, right-click the file, select **Properties**, choose **Unblock**, and
apply the change before opening it.

## 4. Enter the run configuration

On the **Pipeline Runner** worksheet:

1. Keep cell `B7` as `http://127.0.0.1:8080` for a local backend. Use the
   organization's HTTPS backend URL when deployed remotely.
2. Enter the exact Oracle Pipeline code in `B9`, for example `PIPE01`.
3. Keep polling and timeout values in `B10` and `B11` unless the Pipeline is
   known to require a longer timeout.
4. Under **Runtime variables**, enter the exact variable names returned by the
   Pipeline's live preflight and their values. Names are case-insensitive in
   the platform, but using Oracle's spelling is clearest.
5. Delete sample variable rows that the selected Pipeline does not define.
   Unknown variables are rejected intentionally rather than silently ignored.
6. Under **Oracle Inbox file mappings**, enter a Pipeline file-input key and
   the filename that already exists in Oracle Inbox. Leave this section blank
   when the Pipeline owns a fixed/default filename.

Typical variables include `YEAR`, `STARTPERIOD`, `ENDPERIOD`, `IMPORTMODE`, and
`EXPORTMODE`, but every Pipeline can expose a different set. The live Oracle
definition is authoritative.

For Data Integration file references, Oracle commonly expects
`#epminbox/filename.csv`. A native Planning import stage may expect only
`filename.csv`. Use the exact format configured for that Pipeline stage.

## 5. Test access without running the Pipeline

1. Press **Alt+F8**.
2. Select `CheckPipelineConnection`.
3. Choose **Run**.

This checks the backend token and performs a live read-only Pipeline preflight.
The worksheet status becomes `READY` when the check succeeds. No Pipeline is
started.

## 6. Run and monitor the Pipeline

1. Press **Alt+F8**.
2. Select `RunPipelineFromExcel`.
3. Choose **Run**.
4. Read the confirmation summary and choose **Yes** only when the Pipeline and
   context are correct.

Excel performs preflight again, submits the governed execution, and polls the
backend until it succeeds, fails, or the local wait timeout is reached. The
**Live execution result** section shows the execution ID and status. The local
history table records the test, while the authoritative history remains in
PostgreSQL and is visible in the platform's Execution History.

Closing Excel does not cancel an Oracle job that has already been submitted.
If Excel reaches its wait timeout, search for the displayed execution ID in the
web application.

## 7. Revoke or replace a token

List safe token metadata:

```powershell
python -m app.cli.api_tokens list --username YOUR_PLATFORM_USERNAME
```

Revoke a token permanently:

```powershell
python -m app.cli.api_tokens revoke --token-id TOKEN_ID
```

Remove the local Windows value when it is no longer needed:

```powershell
[Environment]::SetEnvironmentVariable(
    "BISP_EPM_EXCEL_TOKEN",
    $null,
    "User"
)
```

Issue a new token if the old one expires or might have been exposed.

## Troubleshooting

- **Environment variable is not configured** — set
  `BISP_EPM_EXCEL_TOKEN`, close all Excel processes, and reopen Excel.
- **HTTP 401** — the token is invalid, expired, revoked, or belongs to a
  deactivated user.
- **HTTP 403** — the token lacks a required scope or its user lacks the
  current platform permission.
- **HTTP 404** — verify the Pipeline code and confirm it is discoverable in
  the connected Oracle environment.
- **Unknown Pipeline variable** — remove the row or replace the name with the
  exact variable exposed by live preflight.
- **Missing required file** — map the required input to an existing Oracle
  Inbox file, using the reference format required by that stage.
- **Cannot connect to server** — confirm `python web_main.py` is still running
  and the URL in `B7` is reachable from the Excel computer.

## Security design

- No Oracle credential is present in the workbook.
- The external API token is scoped, expiring, revocable, and stored as a hash.
- The workbook uses only the allow-listed Pipeline preflight, run, and
  execution-status endpoints.
- A run requires an explicit confirmation and is attributed to the token's
  platform user with trigger source `EXCEL`.
- Local uploads are not accepted by this endpoint; only existing Inbox
  references are allowed in this version.
- There is intentionally no `Workbook_Open` auto-run procedure.

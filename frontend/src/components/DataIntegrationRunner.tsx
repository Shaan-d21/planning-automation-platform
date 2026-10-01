import { useEffect, useState, type FormEvent } from "react";

import { api } from "../api/client";
import type { DataIntegrationDefinition, DataIntegrationRunInput, DataReviewCube, OperationSummary, OracleArtifactRegistration } from "../api/types";
import { ExecutionStep, ResultStep } from "./OperationRunner";
import { Icon } from "./Icon";
import { FeedbackBanner } from "./Feedback";
import { OracleFilePicker } from "./OracleFilePicker";
import { useOperationMonitor } from "./useOperationMonitor";

type RunnerStep = "SETUP" | "REVIEW" | "RUNNING" | "RESULT";
type FileSource = "configured" | "upload" | "inbox";
type PeriodInputMode = "planning" | "exact";

interface ReviewedIntegration {
  payload: Omit<DataIntegrationRunInput, "upload_token" | "inbox_file">;
  source: FileSource;
  file: File | null;
  inboxFile: string | null;
}

interface DataIntegrationRunnerProps {
  operation: OperationSummary;
  csrfToken: string;
  canManageCatalog: boolean;
  planningTaskId?: number | null;
  onBack: () => void;
}

const IMPORT_MODES = ["Replace", "Append", "Map and Validate", "No Import"];
const EXPORT_MODES = ["Merge", "Replace", "Accumulate", "Subtract", "No Export", "Check"];
export function DataIntegrationRunner({ operation, csrfToken, canManageCatalog, planningTaskId = null, onBack }: DataIntegrationRunnerProps) {
  const [step, setStep] = useState<RunnerStep>("SETUP");
  const [integrations, setIntegrations] = useState<DataIntegrationDefinition[]>([]);
  const [artifacts, setArtifacts] = useState<OracleArtifactRegistration[]>([]);
  const [integrationName, setIntegrationName] = useState("");
  const [sessionPendingIntegration, setSessionPendingIntegration] = useState<DataIntegrationDefinition | null>(null);
  const [registerName, setRegisterName] = useState("");
  const [periodInputMode, setPeriodInputMode] = useState<PeriodInputMode>("planning");
  const [planningCubes, setPlanningCubes] = useState<DataReviewCube[]>([]);
  const [targetCube, setTargetCube] = useState("");
  const [liveYears, setLiveYears] = useState<string[]>([]);
  const [livePeriods, setLivePeriods] = useState<string[]>([]);
  const [loadingPlanningContext, setLoadingPlanningContext] = useState(true);
  const [planningContextError, setPlanningContextError] = useState<string | null>(null);
  const [planningYear, setPlanningYear] = useState("");
  const [startMonth, setStartMonth] = useState("");
  const [endMonth, setEndMonth] = useState("");
  const [startPeriod, setStartPeriod] = useState("");
  const [endPeriod, setEndPeriod] = useState("");
  const [importMode, setImportMode] = useState("Replace");
  const [exportMode, setExportMode] = useState("Merge");
  const [fileSource, setFileSource] = useState<FileSource>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [uploadTarget, setUploadTarget] = useState("");
  const [inboxFile, setInboxFile] = useState("");
  const [reviewed, setReviewed] = useState<ReviewedIntegration | null>(null);
  const [approved, setApproved] = useState(false);
  const [executionId, setExecutionId] = useState<string | null>(null);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [registering, setRegistering] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [catalogNotice, setCatalogNotice] = useState<string | null>(null);
  const [oracleAvailable, setOracleAvailable] = useState<boolean | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { execution, monitorError } = useOperationMonitor(executionId);

  useEffect(() => {
    let active = true;
    api.dataIntegrationCatalog()
      .then((catalog) => {
        if (!active) return;
        setIntegrations(catalog.integrations);
        setArtifacts(catalog.artifacts ?? []);
        if (canManageCatalog) void synchronizeCatalog();
      })
      .catch((reason: unknown) => active && setError(errorMessage(reason)))
      .finally(() => active && setLoadingCatalog(false));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    api.dataReviewCubes()
      .then((response) => {
        if (!active) return;
        setPlanningCubes(response.cubes);
        if (response.cubes.length === 1) setTargetCube(response.cubes[0].name);
      })
      .catch((reason: unknown) => active && setPlanningContextError(errorMessage(reason)))
      .finally(() => active && setLoadingPlanningContext(false));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    setLiveYears([]);
    setLivePeriods([]);
    setPlanningYear("");
    setStartMonth("");
    setEndMonth("");
    if (!targetCube) return () => { active = false; };
    setLoadingPlanningContext(true);
    setPlanningContextError(null);
    api.dataReviewDimensions(targetCube)
      .then(async (response) => {
        const yearDimension = findDimension(response.dimensions.map((item) => item.name), ["Years", "Year"]);
        const periodDimension = findDimension(response.dimensions.map((item) => item.name), ["Period", "Periods"]);
        if (!yearDimension || !periodDimension) throw new Error(`Oracle did not expose Year and Period dimensions for ${targetCube}. Use exact Data Integration period names.`);
        const [years, periods] = await Promise.all([
          api.dataReviewMembers(targetCube, yearDimension, "", 0, 100),
          api.dataReviewMembers(targetCube, periodDimension, "", 0, 100)
        ]);
        if (!active) return;
        setLiveYears(uniqueNames(years.members.map((item) => item.name)));
        setLivePeriods(uniqueNames(periods.members.map((item) => item.name)));
      })
      .catch((reason: unknown) => active && setPlanningContextError(errorMessage(reason)))
      .finally(() => active && setLoadingPlanningContext(false));
    return () => { active = false; };
  }, [targetCube]);

  const verifiedNames = new Set(
    artifacts.filter((item) => item.is_verified && item.is_active).map((item) => item.oracle_identifier.toLowerCase())
  );
  const selectableIntegrations = artifacts.length
    ? integrations.filter((item) => verifiedNames.has(item.name.toLowerCase()))
    : integrations;

  async function reloadCatalog() {
    const catalog = await api.dataIntegrationCatalog();
    setIntegrations(catalog.integrations);
    setArtifacts(catalog.artifacts ?? []);
    const selectable = new Set(
      (catalog.artifacts?.length
        ? catalog.artifacts.filter((item) => item.is_verified && item.is_active).map((item) => item.oracle_identifier)
        : catalog.integrations.map((item) => item.name)
      ).map((item) => item.toLowerCase())
    );
    if (
      integrationName
      && !selectable.has(integrationName.toLowerCase())
      && sessionPendingIntegration?.name.toLowerCase() !== integrationName.toLowerCase()
    ) {
      setIntegrationName("");
    }
  }

  async function registerIntegration() {
    const name = registerName.trim();
    if (!name) {
      setError("Enter the exact Data Integration name shown in Oracle.");
      return;
    }
    setRegistering(true);
    setError(null);
    setCatalogNotice(null);
    try {
      const result = await api.registerDataIntegration(name, csrfToken);
      setSessionPendingIntegration({
        name: result.integration.oracle_identifier,
        description: "Pending this governed verification run",
      });
      await reloadCatalog();
      setIntegrationName(result.integration.oracle_identifier);
      setRegisterName("");
      setCatalogNotice(result.message);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setRegistering(false);
    }
  }

  async function synchronizeCatalog() {
    setSyncing(true);
    setError(null);
    setCatalogNotice(null);
    try {
      const result = await api.synchronizeOracleCatalog(csrfToken);
      setOracleAvailable(result.sync.oracle_available);
      await reloadCatalog();
      setCatalogNotice(result.sync.message);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setSyncing(false);
    }
  }

  useEffect(() => {
    if (execution?.terminal) setStep("RESULT");
  }, [execution]);

  function review(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const generatedPeriods = periodInputMode === "exact"
      ? { start: startPeriod.trim(), end: endPeriod.trim() }
      : buildPlanningPeriodRange(planningYear, startMonth, endMonth);
    const normalizedStart = generatedPeriods.start;
    const normalizedEnd = generatedPeriods.end;
    const normalizedInbox = inboxFile.trim();
    if (!integrationName) {
      setError("Select a Data Integration before continuing.");
      return;
    }
    if (!normalizedStart || !normalizedEnd) {
      setError(periodInputMode === "exact"
        ? "Enter both exact Oracle period names."
        : "Select a Planning cube, year, start period, and end period.");
      return;
    }
    if (fileSource === "upload" && !file) {
      setError("Select a local .csv, .txt, or .zip data file.");
      return;
    }
    if (file && ![".csv", ".txt", ".zip", ".dat"].some((extension) => file.name.toLowerCase().endsWith(extension))) {
      setError("Data Integration uploads support .csv, .txt, .zip, or .dat files.");
      return;
    }
    if (fileSource === "inbox" && !normalizedInbox) {
      setError("Enter an existing Oracle Inbox file reference.");
      return;
    }
    setReviewed({
      payload: {
        integration_name: integrationName,
        start_period: normalizedStart,
        end_period: normalizedEnd,
        import_mode: importMode,
        export_mode: exportMode,
        ...(fileSource === "upload" && uploadTarget.trim()
          ? { upload_target: uploadTarget.trim() }
          : {})
      },
      source: fileSource,
      file: fileSource === "upload" ? file : null,
      inboxFile: fileSource === "inbox" ? normalizedInbox : null
    });
    setApproved(false);
    setStep("REVIEW");
  }

  async function start() {
    if (!reviewed || !approved) return;
    setStarting(true);
    setError(null);
    try {
      const uploadToken = reviewed.file
        ? (await api.uploadOperationFile(reviewed.file, csrfToken)).upload.token
        : null;
      const accepted = await api.startDataIntegration(
        {
          ...reviewed.payload,
          upload_token: uploadToken,
          inbox_file: reviewed.inboxFile,
          ...(reviewed.source === "configured" ? { use_configured_file: true } : {}),
          ...(planningTaskId ? { planning_task_id: planningTaskId } : {})
        },
        csrfToken
      );
      setExecutionId(accepted.execution_id);
      setStep("RUNNING");
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setStarting(false);
    }
  }

  function runAgain() {
    void reloadCatalog();
    setReviewed(null);
    setApproved(false);
    setExecutionId(null);
    setError(null);
    setStep("SETUP");
  }

  return <section className="operation-runner">
    <header className="runner-header">
      <button className="button button--quiet" onClick={onBack}><Icon name="arrow" /> Back to Operations</button>
      <div><span className="eyebrow">{operation.category}</span><h1>{operation.display_name}</h1><p>{operation.description}</p></div>
      <span className="risk-badge">{operation.risk_level}</span>
    </header>

    <RunnerSteps current={step} />
    {(error || monitorError) && <FeedbackBanner tone="error" title="Action required" message={error || monitorError} />}

    {step === "SETUP" && <div className="runner-layout"><form className="panel runner-form" onSubmit={review}>
      <div className="panel-heading"><span className="eyebrow">Step 1</span><h2>Prepare the data load</h2><p>Choose an approved integration, define the period range and provide the exact source file.</p></div>
      <IntegrationCatalogHealth artifacts={artifacts} canManage={canManageCatalog} syncing={syncing} notice={catalogNotice} onSync={() => void synchronizeCatalog()} />
      <div className="integration-input-grid">
        <label className="runner-field integration-field--wide"><span>Data Integration *</span><select value={integrationName} disabled={loadingCatalog || oracleAvailable === false || (!selectableIntegrations.length && !sessionPendingIntegration)} onChange={(event) => setIntegrationName(event.target.value)}><option value="">{loadingCatalog ? "Loading verified integrations..." : "Select a verified Data Integration"}</option>{selectableIntegrations.length > 0 && <optgroup label="Verified in Oracle">{selectableIntegrations.map((item) => <option value={item.name} key={item.name}>{item.description ? `${item.name} - ${item.description}` : item.name}</option>)}</optgroup>}{sessionPendingIntegration && !verifiedNames.has(sessionPendingIntegration.name.toLowerCase()) && <optgroup label="New exact-name verification"><option value={sessionPendingIntegration.name}>{sessionPendingIntegration.name} — verify with this run</option></optgroup>}</select><small>{loadingCatalog ? "Reading the approved environment catalog." : oracleAvailable === false ? "Oracle is unavailable; execution is temporarily disabled." : `${selectableIntegrations.length} Oracle-verified integration${selectableIntegrations.length === 1 ? "" : "s"} available. Unavailable registrations are hidden.`}</small></label>
        <label className="runner-field"><span>Period source *</span><select aria-label="Period naming" value={periodInputMode} onChange={(event) => setPeriodInputMode(event.target.value as PeriodInputMode)}><option value="planning">Live Planning members</option><option value="exact">Advanced: exact Data Integration names</option></select><small>Live members avoid invented years and periods; Oracle still validates that period mappings exist.</small></label>
        {periodInputMode !== "exact" && <>
          <label className="runner-field"><span>Target Planning cube *</span><select aria-label="Target Planning cube" value={targetCube} disabled={loadingPlanningContext && !planningCubes.length} onChange={(event) => setTargetCube(event.target.value)}><option value="">Select cube</option>{planningCubes.map((cube) => <option value={cube.name} key={cube.name}>{cube.name}</option>)}</select><small>Used only to retrieve valid Planning members; it does not alter the Oracle integration.</small></label>
          <label className="runner-field"><span>Planning year *</span><select aria-label="Planning year" value={planningYear} disabled={!liveYears.length} onChange={(event) => setPlanningYear(event.target.value)}><option value="">{loadingPlanningContext && targetCube ? "Loading years..." : "Select year"}</option>{liveYears.map((year) => <option key={year}>{year}</option>)}</select><small>Retrieved live from the selected cube.</small></label>
          <label className="runner-field"><span>Start period *</span><select aria-label="Start period" value={startMonth} disabled={!livePeriods.length} onChange={(event) => setStartMonth(event.target.value)}><option value="">{loadingPlanningContext && targetCube ? "Loading periods..." : "Select period"}</option>{livePeriods.map((period) => <option key={period}>{period}</option>)}</select></label>
          <label className="runner-field"><span>End period *</span><select aria-label="End period" value={endMonth} disabled={!livePeriods.length} onChange={(event) => setEndMonth(event.target.value)}><option value="">{loadingPlanningContext && targetCube ? "Loading periods..." : "Select period"}</option>{livePeriods.map((period) => <option key={period}>{period}</option>)}</select><small>May match the start period.</small></label>
          {planningContextError && <div className="runner-warning integration-field--wide"><Icon name="alert" /><div><strong>Live Planning periods are unavailable</strong><p>{planningContextError} Choose “Advanced: exact Data Integration names” to continue.</p></div></div>}
        </>}
        {periodInputMode === "exact" && <>
          <label className="runner-field"><span>Start period *</span><input value={startPeriod} onChange={(event) => setStartPeriod(event.target.value)} placeholder="Jan-27" /><small>Exact Data Integration period mapping name.</small></label>
          <label className="runner-field"><span>End period *</span><input value={endPeriod} onChange={(event) => setEndPeriod(event.target.value)} placeholder="Mar-27" /><small>Do not include curly braces.</small></label>
        </>}
        <label className="runner-field"><span>Import mode *</span><select value={importMode} onChange={(event) => setImportMode(event.target.value)}>{IMPORT_MODES.map((mode) => <option key={mode}>{mode}</option>)}</select></label>
        <label className="runner-field"><span>Export mode *</span><select value={exportMode} onChange={(event) => setExportMode(event.target.value)}>{EXPORT_MODES.map((mode) => <option key={mode}>{mode}</option>)}</select></label>
        <div className="configured-file integration-field--wide"><Icon name="settings" /><span><strong>Scenario remains controlled by Oracle</strong><small>The selected integration's Category mapping determines the target Scenario. This screen does not send a conflicting Scenario override.</small></span></div>
      </div>

      {canManageCatalog && <details className="pipeline-register">
        <summary>Data Integration not listed?</summary>
        <div className="pipeline-register__body">
          <label className="runner-field"><span>Exact Oracle Data Integration name</span><input value={registerName} onChange={(event) => setRegisterName(event.target.value)} placeholder="Revenue_Load" maxLength={250} /><small>Oracle has no safe read-only lookup for a standalone Integration. Registration stays pending until its first governed run is accepted.</small></label>
          <button type="button" className="button button--quiet" disabled={registering} onClick={() => void registerIntegration()}>{registering ? "Registering..." : "Register exact name"}</button>
        </div>
      </details>}

      <section className="integration-file-section"><header><h3>Source file</h3><p>Use the integration's configured file, upload a file for this run, or choose a compatible file already in Oracle.</p></header>
        <div className="file-source-choice" role="radiogroup" aria-label="File source">
          <label className={fileSource === "configured" ? "is-selected" : ""}><input type="radio" name="file-source" checked={fileSource === "configured"} onChange={() => { setFileSource("configured"); setFile(null); setInboxFile(""); }} /><Icon name="settings" /><span><strong>Use configured file</strong><small>Run with the filename already saved in the selected integration.</small></span></label>
          <label className={fileSource === "upload" ? "is-selected" : ""}><input type="radio" name="file-source" checked={fileSource === "upload"} onChange={() => { setFileSource("upload"); setInboxFile(""); }} /><Icon name="data" /><span><strong>Upload local file</strong><small>Use a one-run override or replace an exact Oracle target.</small></span></label>
          <label className={fileSource === "inbox" ? "is-selected" : ""}><input type="radio" name="file-source" checked={fileSource === "inbox"} onChange={() => { setFileSource("inbox"); setFile(null); }} /><Icon name="automation" /><span><strong>Choose from Oracle Inbox</strong><small>Select a compatible live file without typing its reference.</small></span></label>
        </div>
        {fileSource === "upload" && <><label className={`integration-upload${file ? " has-file" : ""}`}><input type="file" accept=".csv,.txt,.zip,.dat" aria-label="Local data file" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><Icon name={file ? "check" : "data"} /><span><strong>{file ? file.name : "Choose a data file"}</strong><small>{file ? formatFileSize(file.size) : "CSV, TXT, ZIP, or DAT"}</small></span><em>{file ? "Change file" : "Browse"}</em></label><label className="runner-field integration-field--wide"><span>Oracle upload target <small>(optional)</small></span><input aria-label="Oracle upload target" value={uploadTarget} onChange={(event) => setUploadTarget(event.target.value)} placeholder={file ? `#epminbox/${file.name}` : "#epminbox/Forecast.csv or inbox/folder/Forecast.csv"} /><small>Leave blank for a one-run Applications Inbox override. Enter the exact configured filename or Data Integration Inbox path to replace that Oracle file without changing the saved integration definition.</small></label></>}
        {fileSource === "inbox" && <OracleFilePicker purpose="data-integration" value={inboxFile} onChange={setInboxFile} label="Existing Oracle integration file" />}
        {fileSource === "configured" && <div className="configured-file"><Icon name="check" /><span><strong>Saved Oracle integration configuration</strong><small>No runtime filename override will be supplied.</small></span></div>}
      </section>
      <footer className="runner-actions"><button type="button" className="button button--quiet" onClick={onBack}>Cancel</button><button className="button button--primary" disabled={loadingCatalog || oracleAvailable === false || (!selectableIntegrations.length && !sessionPendingIntegration)}>Review execution <Icon name="arrow" /></button></footer>
    </form><IntegrationGuidance /></div>}

    {step === "REVIEW" && reviewed && <section className="panel runner-review"><div className="panel-heading"><span className="eyebrow">Step 2</span><h2>Review the Data Integration</h2><p>Nothing has been uploaded or submitted yet. Confirm the file, period range, and Oracle modes.</p></div>
      <div className="review-target"><span className="service-card__icon"><Icon name="data" /></span><div><small>Data Integration</small><strong>{reviewed.payload.integration_name}</strong></div></div>
      <dl className="review-summary integration-review-summary"><div><dt>Period range</dt><dd>{reviewed.payload.start_period} to {reviewed.payload.end_period}</dd></div><div><dt>Import mode</dt><dd>{reviewed.payload.import_mode}</dd></div><div><dt>Export mode</dt><dd>{reviewed.payload.export_mode}</dd></div><div><dt>Source file</dt><dd>{reviewed.file?.name || reviewed.inboxFile || "Configured in Oracle integration"}</dd></div>{reviewed.file && <div><dt>Oracle target</dt><dd>{reviewed.payload.upload_target || `#epminbox/${reviewed.file.name}`}</dd></div>}</dl>
      {reviewed.file && <div className="runner-warning integration-replace-notice"><Icon name="alert" /><div><strong>{reviewed.payload.upload_target ? "Exact Oracle target will be replaced" : "One-run file override"}</strong><p>{reviewed.payload.upload_target ? `The upload replaces ${reviewed.payload.upload_target} if it exists, then this run uses that reference.` : `The file is uploaded as #epminbox/${reviewed.file.name} and used only for this run. The saved Oracle integration filename is not changed.`}</p></div></div>}
      <label className="runner-approval"><input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} /><span><strong>I reviewed the integration, periods, modes, and file</strong><small>The approved data load will be submitted to the connected Oracle environment.</small></span></label>
      <footer className="runner-actions"><button className="button button--quiet" disabled={starting} onClick={() => { setError(null); setStep("SETUP"); }}>Back to inputs</button><button className="button button--primary" disabled={!approved || starting} onClick={() => void start()}>{starting ? <><span className="spinner" /> Uploading and starting...</> : <>Start Data Integration <Icon name="arrow" /></>}</button></footer>
    </section>}

    {step === "RUNNING" && <ExecutionStep execution={execution} operation={operation} />}
    {step === "RESULT" && execution && <ResultStep execution={execution} onAgain={runAgain} onBack={onBack} />}
  </section>;
}

function IntegrationGuidance() {
  return <aside className="panel runner-guidance"><span className="eyebrow">File guidance</span><h2>Use the correct Oracle location</h2><ol><li><span>1</span><div><strong>Local upload</strong><p>The file is staged securely and uploaded to the reviewed Oracle target. Blank targets use Applications Inbox.</p></div></li><li><span>2</span><div><strong>Existing file</strong><p>The selected Oracle reference is preserved exactly for execution.</p></div></li><li><span>3</span><div><strong>Oracle configuration</strong><p>Import format, category and period mappings, target cube, and multi-column behavior remain owned by the selected integration.</p></div></li></ol></aside>;
}

function IntegrationCatalogHealth({ artifacts, canManage, syncing, notice, onSync }: {
  artifacts: OracleArtifactRegistration[];
  canManage: boolean;
  syncing: boolean;
  notice: string | null;
  onSync: () => void;
}) {
  const verified = artifacts.filter((item) => item.is_verified).length;
  const pending = artifacts.filter((item) => item.is_active && item.status === "PENDING").length;
  const unavailable = artifacts.filter((item) => item.status === "MISSING" || item.status === "INACTIVE");
  return <section className="catalog-health" aria-label="Data Integration catalog status">
    <div className="catalog-health__summary">
      <span className="definition-badge"><Icon name="check" /> {verified} verified</span>
      {pending > 0 && <span className="catalog-health__pending">{pending} pending first run</span>}
      {unavailable.length > 0 && <span className="catalog-health__attention"><Icon name="alert" /> {unavailable.length} unavailable</span>}
      {canManage && <button type="button" className="button button--quiet" disabled={syncing} onClick={onSync}>{syncing ? <><span className="spinner" /> Syncing Oracle...</> : <><Icon name="refresh" /> Sync Pipelines &amp; Integrations</>}</button>}
    </div>
    {notice && <p className="catalog-health__notice">{notice}</p>}
    {canManage && unavailable.length > 0 && <details><summary>Review unavailable registrations</summary><ul>{unavailable.map((item) => <li key={item.artifact_id}><span><strong>{item.display_name}</strong><small>{item.last_error || "Not available in the connected application"}</small></span><em className={`catalog-status catalog-status--${item.status.toLowerCase()}`}>{item.status.toLowerCase()}</em></li>)}</ul></details>}
  </section>;
}

function RunnerSteps({ current }: { current: RunnerStep }) {
  const steps: [RunnerStep, string, string][] = [["SETUP", "Prepare", "Periods and file"], ["REVIEW", "Review", "Confirm scope"], ["RUNNING", "Run", "Monitor Oracle"], ["RESULT", "Result", "Review outcome"]];
  const currentIndex = steps.findIndex(([value]) => value === current);
  return <ol className="runner-steps" aria-label="Operation progress">{steps.map(([value, label, description], index) => <li className={index === currentIndex ? "is-current" : index < currentIndex ? "is-complete" : ""} key={value}><span>{index < currentIndex ? <Icon name="check" /> : index + 1}</span><div><strong>{label}</strong><small>{description}</small></div></li>)}</ol>;
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function buildPlanningPeriodRange(
  year: string,
  startMonth: string,
  endMonth: string
) {
  const normalizedYear = year.trim();
  if (!normalizedYear || !startMonth.trim() || !endMonth.trim()) {
    return { start: "", end: "" };
  }
  return {
    start: `${startMonth.trim()}#${normalizedYear}`,
    end: `${endMonth.trim()}#${normalizedYear}`,
  };
}

function findDimension(dimensions: string[], preferredNames: string[]) {
  return preferredNames.map((name) => dimensions.find((dimension) => dimension.toLowerCase() === name.toLowerCase())).find(Boolean) || "";
}

function uniqueNames(values: string[]) {
  return [...new Map(values.filter(Boolean).map((value) => [value.toLowerCase(), value])).values()];
}

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : "The Data Integration could not be completed.";
}

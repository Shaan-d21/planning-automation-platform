import { useEffect, useMemo, useState, type FormEvent } from "react";

import { api } from "../api/client";
import type {
  OperationSummary,
  OracleArtifactRegistration,
  PipelineDefinition,
  PipelineFilePreview,
  PipelineOperationPreview,
  PipelineRunInput,
  PipelineVariablePreview
} from "../api/types";
import { ExecutionStep, ResultStep } from "./OperationRunner";
import { Icon } from "./Icon";
import { FeedbackBanner } from "./Feedback";
import { useOperationMonitor } from "./useOperationMonitor";

type RunnerStep = "SETUP" | "REVIEW" | "RUNNING" | "RESULT";
type FileSource = "configured" | "upload" | "inbox" | "none";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const PLANNING_YEARS = Array.from({ length: 21 }, (_, index) => `FY${String(20 + index).padStart(2, "0")}`);
const PIPELINE_PERIODS = PLANNING_YEARS.flatMap((year) => MONTHS.map((month) => `${month}-${year.slice(2)}`));

interface FileChoice {
  source: FileSource;
  file: File | null;
  inboxReference: string;
}

interface ReviewedPipeline {
  preview: PipelineOperationPreview;
  variables: Record<string, string>;
  files: Record<string, FileChoice>;
}

interface PipelineRunnerProps {
  operation: OperationSummary;
  csrfToken: string;
  canManageCatalog: boolean;
  planningTaskId?: number | null;
  onBack: () => void;
}

export function PipelineRunner({ operation, csrfToken, canManageCatalog, planningTaskId = null, onBack }: PipelineRunnerProps) {
  const [step, setStep] = useState<RunnerStep>("SETUP");
  const [pipelines, setPipelines] = useState<PipelineDefinition[]>([]);
  const [artifacts, setArtifacts] = useState<OracleArtifactRegistration[]>([]);
  const [pipelineCode, setPipelineCode] = useState("");
  const [registerCode, setRegisterCode] = useState("");
  const [preview, setPreview] = useState<PipelineOperationPreview | null>(null);
  const [variables, setVariables] = useState<Record<string, string>>({});
  const [fileChoices, setFileChoices] = useState<Record<string, FileChoice>>({});
  const [reviewed, setReviewed] = useState<ReviewedPipeline | null>(null);
  const [approved, setApproved] = useState(false);
  const [executionId, setExecutionId] = useState<string | null>(null);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [inspecting, setInspecting] = useState(false);
  const [registering, setRegistering] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [catalogNotice, setCatalogNotice] = useState<string | null>(null);
  const [oracleAvailable, setOracleAvailable] = useState<boolean | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { execution, monitorError } = useOperationMonitor(executionId);

  const verifiedCodes = new Set(
    artifacts.filter((item) => item.is_verified && item.is_active).map((item) => item.oracle_identifier.toLowerCase())
  );
  const selectablePipelines = artifacts.length
    ? pipelines.filter((item) => verifiedCodes.has(item.code.toLowerCase()))
    : pipelines;

  useEffect(() => {
    let active = true;
    api.pipelineCatalog()
      .then((catalog) => {
        if (active) {
          setPipelines(catalog.pipelines);
          setArtifacts(catalog.artifacts ?? []);
          if (canManageCatalog) void synchronizeCatalog();
        }
      })
      .catch((reason: unknown) => active && setError(errorMessage(reason)))
      .finally(() => active && setLoadingCatalog(false));
    return () => { active = false; };
  }, []);

  async function reloadCatalog() {
    const catalog = await api.pipelineCatalog();
    setPipelines(catalog.pipelines);
    setArtifacts(catalog.artifacts ?? []);
    const selectable = new Set(
      (catalog.artifacts?.length
        ? catalog.artifacts.filter((item) => item.is_verified && item.is_active).map((item) => item.oracle_identifier)
        : catalog.pipelines.map((item) => item.code)
      ).map((item) => item.toLowerCase())
    );
    if (pipelineCode && !selectable.has(pipelineCode.toLowerCase())) {
      setPipelineCode("");
      setPreview(null);
      setVariables({});
      setFileChoices({});
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

  const stageJobCount = useMemo(
    () => preview?.stages.reduce((total, stage) => total + stage.job_count, 0) ?? 0,
    [preview]
  );

  function applyPreview(next: PipelineOperationPreview) {
    setPipelineCode(next.code);
    setPreview(next);
    setVariables(Object.fromEntries(next.variables.map((variable) => [variable.name, variable.default_value ?? ""])));
    setFileChoices(Object.fromEntries(next.file_requirements.map((requirement) => [
      requirement.key,
      {
        source: requirement.configured_reference ? "configured" : requirement.required ? "upload" : "none",
        file: null,
        inboxReference: ""
      }
    ])));
    setReviewed(null);
    setApproved(false);
  }

  async function inspect() {
    if (!pipelineCode) {
      setError("Select a registered Pipeline first.");
      return;
    }
    setInspecting(true);
    setError(null);
    try {
      applyPreview((await api.pipelinePreflight(pipelineCode)).preview);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setInspecting(false);
    }
  }

  async function register() {
    const code = registerCode.trim();
    if (!code) {
      setError("Enter the exact Oracle Pipeline code.");
      return;
    }
    setRegistering(true);
    setError(null);
    try {
      const result = await api.registerPipeline(code, csrfToken);
      setPipelines((current) => mergePipeline(current, result.pipeline));
      await reloadCatalog();
      setRegisterCode("");
      applyPreview(result.preview);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setRegistering(false);
    }
  }

  function updatePipeline(code: string) {
    setPipelineCode(code);
    setPreview(null);
    setVariables({});
    setFileChoices({});
    setError(null);
  }

  function updateFileChoice(key: string, update: Partial<FileChoice>) {
    setFileChoices((current) => ({
      ...current,
      [key]: { ...current[key], ...update }
    }));
  }

  function review(event: FormEvent) {
    event.preventDefault();
    if (!preview) {
      setError("Inspect the selected Pipeline before reviewing the run.");
      return;
    }
    try {
      validatePipelineInputs(preview, variables, fileChoices);
      setReviewed({
        preview,
        variables: Object.fromEntries(Object.entries(variables).map(([name, value]) => [name, value.trim()]).filter(([, value]) => value)),
        files: Object.fromEntries(Object.entries(fileChoices).map(([key, value]) => [key, { ...value }]))
      });
      setApproved(false);
      setError(null);
      setStep("REVIEW");
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }

  async function start() {
    if (!reviewed || !approved) return;
    setStarting(true);
    setError(null);
    try {
      const payload: PipelineRunInput = {
        pipeline_code: reviewed.preview.code,
        variables: reviewed.variables,
        uploads: {},
        inbox_files: {},
        ...(planningTaskId ? { planning_task_id: planningTaskId } : {})
      };
      const uploads = Object.entries(reviewed.files).filter(([, choice]) => choice.source === "upload" && choice.file);
      await Promise.all(uploads.map(async ([key, choice]) => {
        payload.uploads[key] = (await api.uploadOperationFile(choice.file!, csrfToken)).upload.token;
      }));
      Object.entries(reviewed.files).forEach(([key, choice]) => {
        if (choice.source === "inbox") payload.inbox_files[key] = choice.inboxReference.trim();
      });
      const accepted = await api.startPipeline(payload, csrfToken);
      setExecutionId(accepted.execution_id);
      setStep("RUNNING");
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setStarting(false);
    }
  }

  function runAgain() {
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

    {step === "SETUP" && <div className="runner-layout"><form className="panel runner-form pipeline-form" onSubmit={review}>
      <div className="panel-heading"><span className="eyebrow">Step 1</span><h2>Select and inspect a Pipeline</h2><p>Choose an approved Pipeline. The platform then reads its current stages, variables, and file inputs directly from Oracle.</p></div>
      <CatalogHealth
        artifacts={artifacts}
        canManage={canManageCatalog}
        syncing={syncing}
        notice={catalogNotice}
        onSync={() => void synchronizeCatalog()}
      />
      <div className="pipeline-select-row">
        <label className="runner-field"><span>Oracle Pipeline *</span><select aria-label="Oracle Pipeline" value={pipelineCode} disabled={loadingCatalog || oracleAvailable === false || !selectablePipelines.length} onChange={(event) => updatePipeline(event.target.value)}><option value="">{loadingCatalog ? "Loading verified Pipelines..." : "Select a verified Pipeline"}</option>{selectablePipelines.map((item) => <option key={item.code} value={item.code}>{item.name} ({item.code})</option>)}</select><small>{loadingCatalog ? "Reading approved registrations." : oracleAvailable === false ? "Oracle is unavailable; execution is temporarily disabled." : `${selectablePipelines.length} Oracle-verified Pipeline${selectablePipelines.length === 1 ? "" : "s"} available. Missing registrations are hidden.`}</small></label>
        <button type="button" className="button button--secondary" disabled={!pipelineCode || inspecting || oracleAvailable === false} onClick={() => void inspect()}>{inspecting ? <><span className="spinner" /> Inspecting Oracle...</> : <>Inspect live definition <Icon name="search" /></>}</button>
      </div>

      {canManageCatalog && <details className="pipeline-register">
        <summary>Pipeline not listed?</summary>
        <div className="pipeline-register__body">
          <label className="runner-field"><span>Exact Oracle Pipeline code</span><input value={registerCode} onChange={(event) => setRegisterCode(event.target.value)} placeholder="PIPE01" maxLength={50} /><small>The code is verified against Oracle before it is registered.</small></label>
          <button type="button" className="button button--quiet" disabled={registering} onClick={() => void register()}>{registering ? "Verifying..." : "Verify and register"}</button>
        </div>
      </details>}

      {preview && <>
        <section className="pipeline-definition">
          <header><div><span className="eyebrow">Live Oracle definition</span><h3>{preview.display_name}</h3></div><span className="definition-badge"><Icon name="check" /> Verified</span></header>
          <div className="pipeline-stage-summary"><strong>{preview.stages.length} stage{preview.stages.length === 1 ? "" : "s"}</strong><span>{stageJobCount} configured job{stageJobCount === 1 ? "" : "s"}</span></div>
          {preview.stages.length ? <ol className="pipeline-stages">{preview.stages.map((stage, index) => <li key={`${stage.name}-${index}`}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{stage.display_name}</strong><small>{stage.job_count} job{stage.job_count === 1 ? "" : "s"}{stage.runs_in_parallel ? " · Runs in parallel" : ""}</small></div></li>)}</ol> : <div className="runner-pair-empty"><Icon name="automation" /><span><strong>No stages returned</strong><small>Oracle returned a valid Pipeline with no visible stage summary.</small></span></div>}
        </section>

        <section className="pipeline-input-section"><header><h3>Runtime values</h3><p>Defaults come from Oracle. Required values must be present before review.</p></header>
          {preview.variables.length ? <div className="pipeline-variable-grid">{preview.variables.map((variable) => <PipelineVariableInput variable={variable} value={variables[variable.name] ?? ""} onChange={(value) => setVariables((current) => ({ ...current, [variable.name]: value }))} key={variable.name} />)}</div> : <div className="runner-pair-empty"><Icon name="check" /><span><strong>No runtime values required</strong><small>This Pipeline uses its saved Oracle configuration.</small></span></div>}
        </section>

        {preview.file_requirements.length > 0 && <section className="pipeline-input-section"><header><h3>File inputs</h3><p>Keep the configured Oracle file, upload a replacement, or use another Inbox reference.</p></header><div className="pipeline-file-list">{preview.file_requirements.map((requirement) => <PipelineFileInput requirement={requirement} choice={fileChoices[requirement.key]} onChange={(update) => updateFileChoice(requirement.key, update)} key={requirement.key} />)}</div></section>}
      </>}

      <footer className="runner-actions"><button type="button" className="button button--quiet" onClick={onBack}>Cancel</button><button className="button button--primary" disabled={!preview || oracleAvailable === false}>Review Pipeline run <Icon name="arrow" /></button></footer>
    </form><PipelineGuidance /></div>}

    {step === "REVIEW" && reviewed && <section className="panel runner-review pipeline-review">
      <div className="panel-heading"><span className="eyebrow">Step 2</span><h2>Review the complete Pipeline run</h2><p>Nothing has been uploaded or submitted. Confirm the live stage sequence and every runtime input.</p></div>
      <div className="review-target"><span className="service-card__icon"><Icon name="automation" /></span><div><small>Oracle Pipeline</small><strong>{reviewed.preview.display_name} ({reviewed.preview.code})</strong></div></div>
      <dl className="review-summary"><div><dt>Stages</dt><dd>{reviewed.preview.stages.length}</dd></div><div><dt>Jobs</dt><dd>{reviewed.preview.stages.reduce((total, stage) => total + stage.job_count, 0)}</dd></div><div><dt>File inputs</dt><dd>{reviewed.preview.file_requirements.length}</dd></div></dl>
      {reviewed.preview.stages.length > 0 && <ReviewStages preview={reviewed.preview} />}
      {Object.keys(reviewed.variables).length > 0 && <ReviewValues title="Runtime values" values={reviewed.variables} />}
      {reviewed.preview.file_requirements.length > 0 && <ReviewFiles preview={reviewed.preview} files={reviewed.files} />}
      <div className="runner-warning"><Icon name="alert" /><div><strong>Pipeline stages execute as configured in Oracle</strong><p>The platform supplies only the reviewed runtime values and files. Stage jobs, mappings, calculations, and notifications remain controlled by the Oracle Pipeline definition.</p></div></div>
      <label className="runner-approval"><input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} /><span><strong>I reviewed the live stages and all Pipeline inputs</strong><small>The Pipeline will run against the connected Oracle Planning environment.</small></span></label>
      <footer className="runner-actions"><button className="button button--quiet" disabled={starting} onClick={() => { setError(null); setStep("SETUP"); }}>Back to inputs</button><button className="button button--primary" disabled={!approved || starting} onClick={() => void start()}>{starting ? <><span className="spinner" /> Uploading and starting...</> : <>Start Oracle Pipeline <Icon name="arrow" /></>}</button></footer>
    </section>}

    {step === "RUNNING" && <ExecutionStep execution={execution} operation={operation} />}
    {step === "RESULT" && execution && <ResultStep execution={execution} onAgain={runAgain} onBack={onBack} />}
  </section>;
}

function PipelineVariableInput({ variable, value, onChange }: {
  variable: PipelineVariablePreview;
  value: string;
  onChange: (value: string) => void;
}) {
  const normalizedName = variable.name.replaceAll("_", "").replaceAll(" ", "").toUpperCase();
  let options: string[] | null = null;
  let emptyLabel = "Select a value";
  let guidance = variable.name;
  if (normalizedName === "STARTPERIOD" || normalizedName === "ENDPERIOD") {
    options = ["GLOBAL_POV", ...PIPELINE_PERIODS];
    emptyLabel = normalizedName === "STARTPERIOD" ? "Select start period" : "Select end period";
    guidance = `${variable.name} · Data Integration mapped periods`;
  } else if (normalizedName === "YEAR" || normalizedName.endsWith("YEAR")) {
    options = PLANNING_YEARS;
    emptyLabel = "Select Planning year";
    guidance = `${variable.name} · Planning year member`;
  } else if (normalizedName.endsWith("MONTH")) {
    options = MONTHS;
    emptyLabel = "Select month";
    guidance = `${variable.name} · Planning month member`;
  }

  const availableOptions = options && value && !options.includes(value)
    ? [value, ...options]
    : options;
  return <label className="runner-field">
    <span>{variable.display_name}{variable.required ? " *" : ""}</span>
    {availableOptions
      ? <select aria-label={variable.display_name} value={value} disabled={!variable.editable} onChange={(event) => onChange(event.target.value)}><option value="">{emptyLabel}</option>{availableOptions.map((option) => <option value={option} key={option}>{option}{option === value && !options?.includes(option) ? " (Oracle default)" : ""}</option>)}</select>
      : <input aria-label={variable.display_name} value={value} disabled={!variable.editable} onChange={(event) => onChange(event.target.value)} />}
    <small>{guidance}{variable.default_value ? " · Oracle default prefilled" : ""}</small>
  </label>;
}

function PipelineFileInput({ requirement, choice, onChange }: { requirement: PipelineFilePreview; choice: FileChoice; onChange: (update: Partial<FileChoice>) => void }) {
  if (!choice) return null;
  const options: { value: FileSource; label: string }[] = [];
  if (requirement.configured_reference) options.push({ value: "configured", label: `Use configured file · ${requirement.configured_reference}` });
  options.push({ value: "upload", label: "Upload a local replacement" }, { value: "inbox", label: "Use another Oracle Inbox reference" });
  if (!requirement.required) options.push({ value: "none", label: "Do not provide this optional file" });
  return <article className="pipeline-file-card">
    <header><div><strong>{requirement.display_name}</strong><small>{requirement.consumers.join(" · ") || "Pipeline file variable"}</small></div><span className={requirement.required ? "is-required" : ""}>{requirement.required ? "Required" : "Optional"}</span></header>
    <label className="runner-field"><span>File source</span><select aria-label={`${requirement.display_name} source`} value={choice.source} onChange={(event) => onChange({ source: event.target.value as FileSource, file: null, inboxReference: "" })}>{options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
    {choice.source === "upload" && <label className={`integration-upload${choice.file ? " has-file" : ""}`}><input type="file" aria-label={`${requirement.display_name} local file`} accept={requirement.allowed_extensions.join(",")} onChange={(event) => onChange({ file: event.target.files?.[0] ?? null })} /><Icon name={choice.file ? "check" : "data"} /><span><strong>{choice.file?.name || "Choose a local file"}</strong><small>{choice.file ? formatFileSize(choice.file.size) : requirement.allowed_extensions.length ? requirement.allowed_extensions.join(", ") : "Oracle-compatible file"}</small></span><em>{choice.file ? "Change" : "Browse"}</em></label>}
    {choice.source === "inbox" && <label className="runner-field"><span>Oracle Inbox reference *</span><input aria-label={`${requirement.display_name} Inbox reference`} value={choice.inboxReference} onChange={(event) => onChange({ inboxReference: event.target.value })} placeholder="#epminbox/filename.csv" /><small>Enter the exact reference expected by the Pipeline job.</small></label>}
    {choice.source === "configured" && <div className="configured-file"><Icon name="check" /><span><strong>Oracle configured file</strong><small>{requirement.configured_reference}</small></span></div>}
  </article>;
}

function PipelineGuidance() {
  return <aside className="panel runner-guidance"><span className="eyebrow">How it works</span><h2>Oracle owns the process</h2><ol><li><span>1</span><div><strong>Inspect live</strong><p>Stages, variables, and file requirements are read from the selected Pipeline.</p></div></li><li><span>2</span><div><strong>Supply only inputs</strong><p>You provide values or replacement files without recreating Oracle configuration.</p></div></li><li><span>3</span><div><strong>Monitor one run</strong><p>The complete Pipeline is tracked as one governed execution in Jobs &amp; Activity.</p></div></li></ol></aside>;
}

function CatalogHealth({ artifacts, canManage, syncing, notice, onSync }: {
  artifacts: OracleArtifactRegistration[];
  canManage: boolean;
  syncing: boolean;
  notice: string | null;
  onSync: () => void;
}) {
  const verified = artifacts.filter((item) => item.is_verified).length;
  const attention = artifacts.filter((item) => !item.is_verified);
  return <section className="catalog-health" aria-label="Pipeline catalog status">
    <div className="catalog-health__summary">
      <span className="definition-badge"><Icon name="check" /> {verified} verified</span>
      {attention.length > 0 && <span className="catalog-health__attention"><Icon name="alert" /> {attention.length} need attention</span>}
      {canManage && <button type="button" className="button button--quiet" disabled={syncing} onClick={onSync}>{syncing ? <><span className="spinner" /> Syncing Oracle...</> : <><Icon name="refresh" /> Sync Pipelines &amp; Integrations</>}</button>}
    </div>
    {notice && <p className="catalog-health__notice">{notice}</p>}
    {canManage && attention.length > 0 && <details><summary>Review unavailable registrations</summary><ul>{attention.map((item) => <li key={item.artifact_id}><span><strong>{item.display_name}</strong><small>{item.status === "PENDING" ? "Awaiting live verification" : item.last_error || "Not available in the connected application"}</small></span><em className={`catalog-status catalog-status--${item.status.toLowerCase()}`}>{item.status.toLowerCase()}</em></li>)}</ul></details>}
  </section>;
}

function ReviewStages({ preview }: { preview: PipelineOperationPreview }) {
  return <section className="review-pairs pipeline-review-stages"><h3>Stage sequence</h3><ol>{preview.stages.map((stage, index) => <li key={`${stage.name}-${index}`}><span>{index + 1}</span><div><strong>{stage.display_name}</strong><small>{stage.job_count} job{stage.job_count === 1 ? "" : "s"}{stage.runs_in_parallel ? " · Parallel" : ""}</small></div></li>)}</ol></section>;
}

function ReviewValues({ title, values }: { title: string; values: Record<string, string> }) {
  return <section className="review-pairs"><h3>{title}</h3><dl>{Object.entries(values).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl></section>;
}

function ReviewFiles({ preview, files }: { preview: PipelineOperationPreview; files: Record<string, FileChoice> }) {
  const values = Object.fromEntries(preview.file_requirements.map((requirement) => {
    const choice = files[requirement.key];
    const value = choice.source === "configured" ? requirement.configured_reference ?? "Configured in Oracle" : choice.source === "upload" ? choice.file?.name ?? "Local upload" : choice.source === "inbox" ? choice.inboxReference : "Not provided";
    return [requirement.display_name, value];
  }));
  return <ReviewValues title="File inputs" values={values} />;
}

function validatePipelineInputs(preview: PipelineOperationPreview, variables: Record<string, string>, files: Record<string, FileChoice>) {
  for (const variable of preview.variables) {
    if (variable.required && !(variables[variable.name] ?? "").trim()) throw new Error(`Enter ${variable.display_name}.`);
  }
  for (const requirement of preview.file_requirements) {
    const choice = files[requirement.key];
    if (!choice) throw new Error(`Choose a file source for ${requirement.display_name}.`);
    if (choice.source === "configured" && !requirement.configured_reference) throw new Error(`${requirement.display_name} has no configured Oracle file.`);
    if (choice.source === "upload") {
      if (!choice.file) throw new Error(`Choose a local file for ${requirement.display_name}.`);
      if (requirement.allowed_extensions.length && !requirement.allowed_extensions.some((extension) => choice.file!.name.toLowerCase().endsWith(extension.toLowerCase()))) throw new Error(`${requirement.display_name} supports: ${requirement.allowed_extensions.join(", ")}.`);
    }
    if (choice.source === "inbox" && !choice.inboxReference.trim()) throw new Error(`Enter an Oracle Inbox reference for ${requirement.display_name}.`);
    if (choice.source === "none" && requirement.required) throw new Error(`${requirement.display_name} is required.`);
  }
}

function RunnerSteps({ current }: { current: RunnerStep }) {
  const steps: [RunnerStep, string, string][] = [["SETUP", "Prepare", "Inspect live definition"], ["REVIEW", "Review", "Confirm stages and inputs"], ["RUNNING", "Run", "Monitor Oracle"], ["RESULT", "Result", "Review outcome"]];
  const currentIndex = steps.findIndex(([value]) => value === current);
  return <ol className="runner-steps" aria-label="Operation progress">{steps.map(([value, label, description], index) => <li className={index === currentIndex ? "is-current" : index < currentIndex ? "is-complete" : ""} key={value}><span>{index < currentIndex ? <Icon name="check" /> : index + 1}</span><div><strong>{label}</strong><small>{description}</small></div></li>)}</ol>;
}

function mergePipeline(current: PipelineDefinition[], added: PipelineDefinition) {
  return [...current.filter((item) => item.code.toLowerCase() !== added.code.toLowerCase()), added].sort((left, right) => left.name.localeCompare(right.name));
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : "The Pipeline operation could not be completed.";
}

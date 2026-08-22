import { useEffect, useMemo, useState, type FormEvent } from "react";

import { api } from "../api/client";
import type {
  OperationSummary,
  ReportAxisDimensionInput,
  ReportCatalogItem,
  ReportPreflight,
  ReportRegistrationInput,
  ReportRunInput
} from "../api/types";
import { ExecutionStep, ResultStep } from "./OperationRunner";
import { Icon } from "./Icon";
import { FeedbackBanner } from "./Feedback";
import { useOperationMonitor } from "./useOperationMonitor";

type RunnerStep = "SETUP" | "REVIEW" | "RUNNING" | "RESULT";
type ReportSource = "REGISTERED" | "FORM";

interface ReviewedReport {
  preflight: ReportPreflight;
  payload: ReportRunInput;
}

interface ReportGenerationRunnerProps {
  operation?: OperationSummary;
  csrfToken: string;
  onBack: () => void;
}

export const REPORT_OPERATION: OperationSummary = {
  code: "report-generation",
  display_name: "Report Generation",
  description: "Generate a downloadable Excel workbook from a registered report or live Planning form.",
  category: "Reporting",
  risk_level: "Read only",
  route: "#reports"
};

export function ReportGenerationRunner({
  operation = REPORT_OPERATION,
  csrfToken,
  onBack
}: ReportGenerationRunnerProps) {
  const [step, setStep] = useState<RunnerStep>("SETUP");
  const [source, setSource] = useState<ReportSource>("REGISTERED");
  const [reports, setReports] = useState<ReportCatalogItem[]>([]);
  const [reportName, setReportName] = useState("");
  const [preflight, setPreflight] = useState<ReportPreflight | null>(null);
  const [title, setTitle] = useState("");
  const [pov, setPov] = useState<Record<string, string>>({});
  const [reviewed, setReviewed] = useState<ReviewedReport | null>(null);
  const [registrationOpen, setRegistrationOpen] = useState(false);
  const [approved, setApproved] = useState(false);
  const [executionId, setExecutionId] = useState<string | null>(null);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [inspecting, setInspecting] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { execution, monitorError } = useOperationMonitor(executionId);

  const registeredCubes = useMemo(
    () => [...new Set(reports.map((report) => report.cube).filter(Boolean))].sort(),
    [reports]
  );

  useEffect(() => {
    void loadCatalog();
  }, []);

  useEffect(() => {
    if (execution?.terminal) setStep("RESULT");
  }, [execution]);

  async function loadCatalog() {
    setLoadingCatalog(true);
    setError(null);
    try {
      const response = await api.reportCatalog();
      setReports(response.reports);
      if (response.reports.length) {
        setSource("REGISTERED");
        setReportName((current) => current || response.reports[0].name);
      } else {
        setSource("FORM");
      }
    } catch (reason) {
      setSource("FORM");
      setError(errorMessage(reason));
    } finally {
      setLoadingCatalog(false);
    }
  }

  function changeSource(next: ReportSource) {
    setSource(next);
    setReportName(next === "REGISTERED" ? reports[0]?.name ?? "" : "");
    setPreflight(null);
    setTitle("");
    setPov({});
    setReviewed(null);
    setError(null);
  }

  async function inspectReport(explicitName?: string) {
    const normalizedName = (explicitName ?? reportName).trim();
    if (!normalizedName) {
      setError(source === "REGISTERED" ? "Select a registered report." : "Enter the exact Planning form name or ID.");
      return;
    }
    setInspecting(true);
    setError(null);
    setPreflight(null);
    try {
      const response = await api.reportPreflight(normalizedName, csrfToken);
      setReportName(normalizedName);
      setPreflight(response.preflight);
      setTitle(response.preflight.title);
      setPov(Object.fromEntries(response.preflight.current_pov));
    } catch (reason) {
      const isDirectForm = source === "FORM" || !reports.some((report) => report.name === normalizedName);
      if (isDirectForm) setRegistrationOpen(true);
      setError(`${errorMessage(reason)}${isDirectForm ? " If this environment cannot inspect the form through REST, register its data layout below." : ""}`);
    } finally {
      setInspecting(false);
    }
  }

  function review(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (!preflight) {
      setError("Inspect the selected report or Planning form before continuing.");
      return;
    }
    const normalizedTitle = title.trim();
    if (!normalizedTitle) {
      setError("Enter the workbook title.");
      return;
    }
    const overrides: Record<string, string> = {};
    for (const dimension of preflight.page_dimensions) {
      const member = (pov[dimension] ?? "").trim();
      if (!member) {
        setError(`Select or enter a POV member for ${dimension}.`);
        return;
      }
      overrides[dimension] = member;
    }
    setReviewed({
      preflight,
      payload: {
        form_name: preflight.form_name,
        title: normalizedTitle,
        page_member_overrides: overrides
      }
    });
    setApproved(false);
    setStep("REVIEW");
  }

  async function start() {
    if (!reviewed || !approved) return;
    setStarting(true);
    setError(null);
    try {
      const accepted = await api.startReport(reviewed.payload, csrfToken);
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

  async function registered(report: ReportCatalogItem) {
    setReports((current) => mergeReport(current, report));
    setSource("REGISTERED");
    setRegistrationOpen(false);
    setReportName(report.name);
    await inspectReport(report.name);
  }

  return <section className="operation-runner report-runner">
    <header className="runner-header">
      <button className="button button--quiet" onClick={onBack}><Icon name="arrow" /> Back</button>
      <div><span className="eyebrow">{operation.category}</span><h1>{operation.display_name}</h1><p>{operation.description}</p></div>
      <span className="risk-badge risk-badge--read-only">Read only</span>
    </header>

    <RunnerSteps current={step} />
    {(error || monitorError) && <FeedbackBanner tone="error" title="Action required" message={error || monitorError} />}

    {step === "SETUP" && <>
      <div className="runner-layout"><form className="panel runner-form" onSubmit={review}>
        <div className="panel-heading report-heading"><div><span className="eyebrow">Step 1</span><h2>Choose the reporting source</h2><p>Use a governed registered definition or inspect a Planning form directly when the environment supports it.</p></div><button type="button" className="button button--quiet" onClick={() => setRegistrationOpen((current) => !current)}>{registrationOpen ? "Close registration" : "Register report"}</button></div>
        <div className="variable-mode-tabs report-source-tabs" role="tablist" aria-label="Report source">
          <button type="button" role="tab" aria-selected={source === "REGISTERED"} className={source === "REGISTERED" ? "is-active" : ""} onClick={() => changeSource("REGISTERED")}><Icon name="reports" /><span><strong>Registered report</strong><small>Use a reusable layout reviewed by an administrator.</small></span></button>
          <button type="button" role="tab" aria-selected={source === "FORM"} className={source === "FORM" ? "is-active" : ""} onClick={() => changeSource("FORM")}><Icon name="data" /><span><strong>Planning form</strong><small>Inspect an exact live form name or ID through REST.</small></span></button>
        </div>

        <div className="report-source-row">
          {source === "REGISTERED" ? <label className="runner-field"><span>Registered report *</span><select aria-label="Registered report" value={reportName} disabled={loadingCatalog || !reports.length} onChange={(event) => { setReportName(event.target.value); setPreflight(null); }}><option value="">{loadingCatalog ? "Loading report library..." : "Select a registered report"}</option>{reports.map((report) => <option value={report.name} key={report.name}>{report.title} · {report.cube}</option>)}</select><small>{reports.length ? `${reports.length} reusable report${reports.length === 1 ? "" : "s"} available.` : "No reports are registered yet. Use Register report or inspect a live form."}</small></label> : <label className="runner-field"><span>Exact Planning form name or ID *</span><input aria-label="Planning form name or ID" value={reportName} onChange={(event) => { setReportName(event.target.value); setPreflight(null); }} placeholder="Product_Revenue_Forecast_Reporting" maxLength={250} /><small>Cloud environments normally support direct form inspection. Registration remains available as a compatible fallback.</small></label>}
          <button type="button" className="button button--primary" disabled={inspecting || loadingCatalog || !reportName.trim()} onClick={() => void inspectReport()}>{inspecting ? <><span className="spinner" /> Inspecting...</> : <>Inspect layout <Icon name="arrow" /></>}</button>
        </div>

        {preflight ? <ReportLayout preflight={preflight} title={title} pov={pov} onTitle={setTitle} onPov={(dimension, member) => setPov((current) => ({ ...current, [dimension]: member }))} /> : <div className="report-layout-empty"><Icon name="reports" /><div><strong>Inspect a source to prepare the workbook</strong><p>The platform will show its row, column, and POV dimensions before anything is generated.</p></div></div>}
        <footer className="runner-actions"><button type="button" className="button button--quiet" onClick={onBack}>Cancel</button><button className="button button--primary" disabled={!preflight}>Review Report <Icon name="arrow" /></button></footer>
      </form><ReportGuidance /></div>

      {registrationOpen && <ReportRegistrationPanel csrfToken={csrfToken} knownCubes={registeredCubes} suggestedName={source === "FORM" ? reportName : ""} onCancel={() => setRegistrationOpen(false)} onRegistered={(report) => void registered(report)} />}
    </>}

    {step === "REVIEW" && reviewed && <section className="panel runner-review">
      <div className="panel-heading"><span className="eyebrow">Step 2</span><h2>Review the Excel report</h2><p>No Oracle data has been exported. Confirm the source, layout, title, and POV before generation.</p></div>
      <div className="review-target"><span className="service-card__icon"><Icon name="reports" /></span><div><small>{reviewed.preflight.registered ? "Registered report" : "Live Planning form"}</small><strong>{reviewed.preflight.form_name}</strong></div></div>
      <dl className="review-summary"><div><dt>Workbook title</dt><dd>{reviewed.payload.title}</dd></div><div><dt>Cube</dt><dd>{reviewed.preflight.cube || "Resolved by Planning form"}</dd></div><div><dt>Rows</dt><dd>{reviewed.preflight.row_dimensions.join(", ")}</dd></div><div><dt>Columns</dt><dd>{reviewed.preflight.column_dimensions.join(", ")}</dd></div></dl>
      <section className="review-pairs"><h3>Point of view</h3><dl>{Object.entries(reviewed.payload.page_member_overrides).map(([dimension, member]) => <div key={dimension}><dt>{dimension}</dt><dd>{member}</dd></div>)}</dl></section>
      <label className="runner-approval"><input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} /><span><strong>I reviewed the source, layout, and POV</strong><small>This is read-only. The platform will export Oracle data into a new Excel workbook without changing Planning.</small></span></label>
      <footer className="runner-actions"><button className="button button--quiet" disabled={starting} onClick={() => { setError(null); setStep("SETUP"); }}>Back to inputs</button><button className="button button--primary" disabled={!approved || starting} onClick={() => void start()}>{starting ? <><span className="spinner" /> Starting report...</> : <>Generate Excel Report <Icon name="arrow" /></>}</button></footer>
    </section>}

    {step === "RUNNING" && <ExecutionStep execution={execution} operation={operation} />}
    {step === "RESULT" && execution && <ResultStep execution={execution} onAgain={runAgain} onBack={onBack} />}
  </section>;
}

function ReportLayout({ preflight, title, pov, onTitle, onPov }: { preflight: ReportPreflight; title: string; pov: Record<string, string>; onTitle: (value: string) => void; onPov: (dimension: string, member: string) => void }) {
  const allowed = Object.fromEntries(preflight.allowed_page_members);
  return <section className="report-layout-card"><header><div><span className={`status-badge ${preflight.registered ? "status-success" : "status-running"}`}>{preflight.registered ? "Registered layout" : "Live form"}</span><h3>{preflight.title}</h3><p>{preflight.cube ? `${preflight.cube} cube` : "Planning resolves the form cube"}</p></div><Icon name="reports" /></header>
    <label className="runner-field"><span>Workbook title *</span><input aria-label="Workbook title" value={title} onChange={(event) => onTitle(event.target.value)} maxLength={250} /></label>
    <div className="report-axis-summary"><div><small>Rows</small><strong>{preflight.row_dimensions.join(" · ") || "Not returned"}</strong></div><div><small>Columns</small><strong>{preflight.column_dimensions.join(" · ") || "Not returned"}</strong></div></div>
    {preflight.page_dimensions.length ? <div className="report-pov-grid">{preflight.page_dimensions.map((dimension, index) => <label className="runner-field" key={dimension}><span>{dimension} *</span><input list={`report-pov-${index}`} aria-label={`Report POV ${dimension}`} value={pov[dimension] ?? ""} onChange={(event) => onPov(dimension, event.target.value)} placeholder={`Select or enter ${dimension}`} /><datalist id={`report-pov-${index}`}>{(allowed[dimension] ?? []).map((member) => <option value={member} key={member} />)}</datalist><small>{allowed[dimension]?.length ? `${allowed[dimension].length} live member choices.` : "Enter the exact valid member name."}</small></label>)}</div> : <div className="native-import-note"><Icon name="check" /><div><strong>No page dimensions require input</strong><p>The selected definition owns the complete data intersection.</p></div></div>}
  </section>;
}

interface AxisDraft {
  id: number;
  dimension: string;
  members: string;
}

let nextAxisId = 1;

function ReportRegistrationPanel({ csrfToken, knownCubes, suggestedName, onCancel, onRegistered }: { csrfToken: string; knownCubes: string[]; suggestedName: string; onCancel: () => void; onRegistered: (report: ReportCatalogItem) => void }) {
  const [name, setName] = useState(suggestedName);
  const [title, setTitle] = useState(suggestedName);
  const [cube, setCube] = useState(knownCubes[0] ?? "");
  const [pov, setPov] = useState<AxisDraft[]>([]);
  const [rows, setRows] = useState<AxisDraft[]>([axisDraft()]);
  const [columns, setColumns] = useState<AxisDraft[]>([axisDraft()]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const payload = registrationPayload(name, title, cube, pov, rows, columns);
      setSaving(true);
      const response = await api.registerReport(payload, csrfToken);
      onRegistered(response.report);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setSaving(false);
    }
  }

  function update(group: "pov" | "rows" | "columns", id: number, key: "dimension" | "members", value: string) {
    const setter = group === "pov" ? setPov : group === "rows" ? setRows : setColumns;
    setter((current) => current.map((item) => item.id === id ? { ...item, [key]: value } : item));
  }

  function remove(group: "pov" | "rows" | "columns", id: number) {
    const setter = group === "pov" ? setPov : group === "rows" ? setRows : setColumns;
    setter((current) => current.filter((item) => item.id !== id));
  }

  return <section className="panel report-registration"><header><div><span className="eyebrow">Reusable fallback</span><h2>Register a report definition</h2><p>Define the cube and data intersection once when direct Planning form inspection is unavailable or a governed reusable layout is preferred.</p></div><button type="button" className="button button--quiet" onClick={onCancel}>Close</button></header>
    {error && <FeedbackBanner tone="error" title="Review the definition" message={error} />}
    <form onSubmit={save}><div className="report-registration-basics"><label className="runner-field"><span>Registered name *</span><input aria-label="Registered report name" value={name} onChange={(event) => setName(event.target.value)} placeholder="Revenue Forecast" /></label><label className="runner-field"><span>Workbook title *</span><input aria-label="Registered workbook title" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Revenue Forecast Report" /></label><label className="runner-field"><span>Cube / plan type *</span><input list="registered-report-cubes" aria-label="Registered report cube" value={cube} onChange={(event) => setCube(event.target.value)} placeholder="Plan1" /><datalist id="registered-report-cubes">{knownCubes.map((item) => <option value={item} key={item} />)}</datalist><small>Enter the exact Oracle Planning cube name.</small></label></div>
      <RegistrationAxis title="Default point of view" description="Optional fixed page members. One member per dimension." group="pov" entries={pov} onAdd={() => setPov((current) => [...current, axisDraft()])} onUpdate={update} onRemove={remove} />
      <div className="report-registration-axes"><RegistrationAxis title="Column axis" description="Periods and other dimensions displayed across the workbook." group="columns" entries={columns} required onAdd={() => setColumns((current) => [...current, axisDraft()])} onUpdate={update} onRemove={remove} /><RegistrationAxis title="Row axis" description="Accounts, products, entities, and other dimensions down the workbook." group="rows" entries={rows} required onAdd={() => setRows((current) => [...current, axisDraft()])} onUpdate={update} onRemove={remove} /></div>
      <footer className="runner-actions"><button type="button" className="button button--quiet" onClick={onCancel}>Cancel</button><button className="button button--primary" disabled={saving}>{saving ? <><span className="spinner" /> Registering...</> : <>Save Registered Report <Icon name="arrow" /></>}</button></footer>
    </form>
  </section>;
}

function RegistrationAxis({ title, description, group, entries, required = false, onAdd, onUpdate, onRemove }: { title: string; description: string; group: "pov" | "rows" | "columns"; entries: AxisDraft[]; required?: boolean; onAdd: () => void; onUpdate: (group: "pov" | "rows" | "columns", id: number, key: "dimension" | "members", value: string) => void; onRemove: (group: "pov" | "rows" | "columns", id: number) => void }) {
  return <section className="report-registration-axis"><header><div><h3>{title}{required ? " *" : ""}</h3><p>{description}</p></div><button type="button" className="button button--quiet" onClick={onAdd}>Add dimension</button></header>{entries.length ? <div className="report-axis-entries">{entries.map((entry) => <div className="report-axis-entry" key={entry.id}><label className="runner-field"><span>Dimension</span><input aria-label={`${title} dimension`} value={entry.dimension} onChange={(event) => onUpdate(group, entry.id, "dimension", event.target.value)} placeholder={group === "columns" ? "Period" : group === "rows" ? "Account" : "Scenario"} /></label><label className="runner-field"><span>{group === "pov" ? "Member" : "Members"}</span><input aria-label={`${title} members`} value={entry.members} onChange={(event) => onUpdate(group, entry.id, "members", event.target.value)} placeholder={group === "pov" ? "Forecast" : "Jan | Feb | Mar"} /><small>{group === "pov" ? "One exact member." : "Separate multiple exact members with |."}</small></label><button type="button" className="icon-button report-axis-remove" aria-label={`Remove ${title} dimension`} onClick={() => onRemove(group, entry.id)}><Icon name="close" /></button></div>)}</div> : <div className="runner-pair-empty"><Icon name="reports" /><span><strong>No dimensions added</strong><small>{required ? "Add at least one dimension." : "This report will not use fixed POV members."}</small></span></div>}</section>;
}

function registrationPayload(name: string, title: string, cube: string, pov: AxisDraft[], rows: AxisDraft[], columns: AxisDraft[]): ReportRegistrationInput {
  const normalizedName = name.trim();
  const normalizedTitle = title.trim();
  const normalizedCube = cube.trim();
  if (!normalizedName || !normalizedTitle || !normalizedCube) throw new Error("Registered name, workbook title, and cube are required.");
  const normalizedPov = axisInputs(pov, true);
  const normalizedRows = axisInputs(rows, false);
  const normalizedColumns = axisInputs(columns, false);
  if (!normalizedRows.length || !normalizedColumns.length) throw new Error("Add at least one row dimension and one column dimension.");
  const dimensions = [...normalizedPov, ...normalizedRows, ...normalizedColumns].map((item) => item.dimension.toLowerCase());
  if (new Set(dimensions).size !== dimensions.length) throw new Error("Each dimension can appear only once across POV, rows, and columns.");
  return { name: normalizedName, title: normalizedTitle, cube: normalizedCube, pov: Object.fromEntries(normalizedPov.map((item) => [item.dimension, item.members[0]])), rows: normalizedRows, columns: normalizedColumns };
}

function axisInputs(entries: AxisDraft[], singleMember: boolean): ReportAxisDimensionInput[] {
  return entries.map((entry) => ({ dimension: entry.dimension.trim(), members: entry.members.split("|").map((item) => item.trim()).filter(Boolean) })).filter((entry) => entry.dimension || entry.members.length).map((entry) => {
    if (!entry.dimension || !entry.members.length) throw new Error("Every report axis entry requires a dimension and member selection.");
    if (singleMember && entry.members.length !== 1) throw new Error(`POV dimension ${entry.dimension} requires exactly one member.`);
    if (new Set(entry.members.map((item) => item.toLowerCase())).size !== entry.members.length) throw new Error(`Dimension ${entry.dimension} contains a duplicate member.`);
    return entry;
  });
}

function axisDraft(): AxisDraft {
  return { id: nextAxisId++, dimension: "", members: "" };
}

function mergeReport(reports: ReportCatalogItem[], report: ReportCatalogItem) {
  return [...reports.filter((item) => item.name.toLowerCase() !== report.name.toLowerCase()), report].sort((left, right) => left.title.localeCompare(right.title));
}

function ReportGuidance() {
  return <aside className="panel runner-guidance"><span className="eyebrow">How it works</span><h2>Generate without changing Planning</h2><ol><li><span>1</span><div><strong>Inspect the layout</strong><p>Confirm the live form or reusable registered definition.</p></div></li><li><span>2</span><div><strong>Choose the POV</strong><p>Enter the exact page members required for this output.</p></div></li><li><span>3</span><div><strong>Download Excel</strong><p>The completed workbook is retained with the monitored execution.</p></div></li></ol></aside>;
}

function RunnerSteps({ current }: { current: RunnerStep }) {
  const steps: [RunnerStep, string, string][] = [["SETUP", "Prepare", "Source, layout, and POV"], ["REVIEW", "Review", "Confirm read-only export"], ["RUNNING", "Generate", "Read Oracle data"], ["RESULT", "Download", "Open Excel output"]];
  const currentIndex = steps.findIndex(([value]) => value === current);
  return <ol className="runner-steps" aria-label="Operation progress">{steps.map(([value, label, description], index) => <li className={index === currentIndex ? "is-current" : index < currentIndex ? "is-complete" : ""} key={value}><span>{index < currentIndex ? <Icon name="check" /> : index + 1}</span><div><strong>{label}</strong><small>{description}</small></div></li>)}</ol>;
}

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : "The report could not be prepared.";
}

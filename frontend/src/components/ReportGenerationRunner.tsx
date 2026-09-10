import { useEffect, useState, type FormEvent } from "react";

import { api } from "../api/client";
import type {
  OperationSummary,
  ReportCatalogItem,
  ReportPreflight,
  ReportRunInput
} from "../api/types";
import { ExecutionStep, ResultStep } from "./OperationRunner";
import { Icon } from "./Icon";
import { FeedbackBanner } from "./Feedback";
import { useOperationMonitor } from "./useOperationMonitor";

type RunnerStep = "SETUP" | "REVIEW" | "RUNNING" | "RESULT";

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
  display_name: "Data Explorer",
  description: "Generate a downloadable Excel workbook from an approved saved data view.",
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
  const [reports, setReports] = useState<ReportCatalogItem[]>([]);
  const [reportName, setReportName] = useState("");
  const [preflight, setPreflight] = useState<ReportPreflight | null>(null);
  const [title, setTitle] = useState("");
  const [pov, setPov] = useState<Record<string, string>>({});
  const [reviewed, setReviewed] = useState<ReviewedReport | null>(null);
  const [approved, setApproved] = useState(false);
  const [executionId, setExecutionId] = useState<string | null>(null);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [inspecting, setInspecting] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { execution, monitorError } = useOperationMonitor(executionId);

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
        setReportName((current) => current || response.reports[0].name);
      }
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setLoadingCatalog(false);
    }
  }

  async function inspectReport(explicitName?: string) {
    const normalizedName = (explicitName ?? reportName).trim();
    if (!normalizedName) {
      setError("Select a saved data view.");
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
      setError(errorMessage(reason));
    } finally {
      setInspecting(false);
    }
  }

  function review(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (!preflight) {
      setError("Inspect the selected saved view before continuing.");
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

  return <section className="operation-runner report-runner">
    <header className="runner-header">
      <button className="button button--quiet" onClick={onBack}><Icon name="arrow" /> Back</button>
      <div><span className="eyebrow">Approved read-only outputs</span><h1>Data Explorer</h1><p>Generate an Excel workbook from a saved Planning data view available to your role.</p></div>
      <span className="risk-badge risk-badge--read-only">Read only</span>
    </header>

    <RunnerSteps current={step} />
    {(error || monitorError) && <FeedbackBanner tone="error" title="Action required" message={error || monitorError} />}

    {step === "SETUP" && <>
      <div className="runner-layout"><form className="panel runner-form" onSubmit={review}>
        <div className="panel-heading report-heading"><div><span className="eyebrow">Step 1</span><h2>Choose a saved data view</h2><p>Your role can export approved reusable layouts. Live ad-hoc data exploration is not assigned to this account.</p></div></div>

        <div className="report-source-row">
          <label className="runner-field"><span>Saved view *</span><select aria-label="Saved data view export" value={reportName} disabled={loadingCatalog || !reports.length} onChange={(event) => { setReportName(event.target.value); setPreflight(null); }}><option value="">{loadingCatalog ? "Loading saved views..." : "Select a saved view"}</option>{reports.map((report) => <option value={report.name} key={report.name}>{report.title} · {report.cube}</option>)}</select><small>{reports.length ? `${reports.length} reusable view${reports.length === 1 ? "" : "s"} available.` : "No saved views are available for this account."}</small></label>
          <button type="button" className="button button--primary" disabled={inspecting || loadingCatalog || !reportName.trim()} onClick={() => void inspectReport()}>{inspecting ? <><span className="spinner" /> Inspecting...</> : <>Inspect layout <Icon name="arrow" /></>}</button>
        </div>

        {preflight ? <ReportLayout preflight={preflight} title={title} pov={pov} onTitle={setTitle} onPov={(dimension, member) => setPov((current) => ({ ...current, [dimension]: member }))} /> : <div className="report-layout-empty"><Icon name="reports" /><div><strong>Inspect a source to prepare the workbook</strong><p>The platform will show its row, column, and POV dimensions before anything is generated.</p></div></div>}
        <footer className="runner-actions"><button type="button" className="button button--quiet" onClick={onBack}>Cancel</button><button className="button button--primary" disabled={!preflight}>Review output <Icon name="arrow" /></button></footer>
      </form><ReportGuidance /></div>
    </>}

    {step === "REVIEW" && reviewed && <section className="panel runner-review">
      <div className="panel-heading"><span className="eyebrow">Step 2</span><h2>Review the Excel output</h2><p>No Oracle data has been exported. Confirm the source, layout, title, and POV before generation.</p></div>
      <div className="review-target"><span className="service-card__icon"><Icon name="reports" /></span><div><small>Saved data view</small><strong>{reviewed.preflight.form_name}</strong></div></div>
      <dl className="review-summary"><div><dt>Workbook title</dt><dd>{reviewed.payload.title}</dd></div><div><dt>Cube</dt><dd>{reviewed.preflight.cube}</dd></div><div><dt>Rows</dt><dd>{reviewed.preflight.row_dimensions.join(", ")}</dd></div><div><dt>Columns</dt><dd>{reviewed.preflight.column_dimensions.join(", ")}</dd></div></dl>
      <section className="review-pairs"><h3>Point of view</h3><dl>{Object.entries(reviewed.payload.page_member_overrides).map(([dimension, member]) => <div key={dimension}><dt>{dimension}</dt><dd>{member}</dd></div>)}</dl></section>
      <label className="runner-approval"><input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} /><span><strong>I reviewed the source, layout, and POV</strong><small>This is read-only. The platform will export Oracle data into a new Excel workbook without changing Planning.</small></span></label>
      <footer className="runner-actions"><button className="button button--quiet" disabled={starting} onClick={() => { setError(null); setStep("SETUP"); }}>Back to inputs</button><button className="button button--primary" disabled={!approved || starting} onClick={() => void start()}>{starting ? <><span className="spinner" /> Creating Excel...</> : <>Generate Excel <Icon name="arrow" /></>}</button></footer>
    </section>}

    {step === "RUNNING" && <ExecutionStep execution={execution} operation={operation} />}
    {step === "RESULT" && execution && <ResultStep execution={execution} onAgain={runAgain} onBack={onBack} />}
  </section>;
}

function ReportLayout({ preflight, title, pov, onTitle, onPov }: { preflight: ReportPreflight; title: string; pov: Record<string, string>; onTitle: (value: string) => void; onPov: (dimension: string, member: string) => void }) {
  const allowed = Object.fromEntries(preflight.allowed_page_members);
  return <section className="report-layout-card"><header><div><span className="status-badge status-success">Saved layout</span><h3>{preflight.title}</h3><p>{preflight.cube} cube</p></div><Icon name="reports" /></header>
    <label className="runner-field"><span>Workbook title *</span><input aria-label="Workbook title" value={title} onChange={(event) => onTitle(event.target.value)} maxLength={250} /></label>
    <div className="report-axis-summary"><div><small>Rows</small><strong>{preflight.row_dimensions.join(" · ") || "Not returned"}</strong></div><div><small>Columns</small><strong>{preflight.column_dimensions.join(" · ") || "Not returned"}</strong></div></div>
    {preflight.page_dimensions.length ? <div className="report-pov-grid">{preflight.page_dimensions.map((dimension, index) => <label className="runner-field" key={dimension}><span>{dimension} *</span><input list={`report-pov-${index}`} aria-label={`Report POV ${dimension}`} value={pov[dimension] ?? ""} onChange={(event) => onPov(dimension, event.target.value)} placeholder={`Select or enter ${dimension}`} /><datalist id={`report-pov-${index}`}>{(allowed[dimension] ?? []).map((member) => <option value={member} key={member} />)}</datalist><small>{allowed[dimension]?.length ? `${allowed[dimension].length} live member choices.` : "Enter the exact valid member name."}</small></label>)}</div> : <div className="native-import-note"><Icon name="check" /><div><strong>No page dimensions require input</strong><p>The selected definition owns the complete data intersection.</p></div></div>}
  </section>;
}

function ReportGuidance() {
  return <aside className="panel runner-guidance"><span className="eyebrow">How it works</span><h2>Generate without changing Planning</h2><ol><li><span>1</span><div><strong>Inspect the layout</strong><p>Confirm the approved reusable data view.</p></div></li><li><span>2</span><div><strong>Choose the POV</strong><p>Enter the exact page members required for this output.</p></div></li><li><span>3</span><div><strong>Download Excel</strong><p>The completed workbook is retained with the monitored execution.</p></div></li></ol></aside>;
}

function RunnerSteps({ current }: { current: RunnerStep }) {
  const steps: [RunnerStep, string, string][] = [["SETUP", "Prepare", "Source, layout, and POV"], ["REVIEW", "Review", "Confirm read-only export"], ["RUNNING", "Generate", "Read Oracle data"], ["RESULT", "Download", "Open Excel output"]];
  const currentIndex = steps.findIndex(([value]) => value === current);
  return <ol className="runner-steps" aria-label="Operation progress">{steps.map(([value, label, description], index) => <li className={index === currentIndex ? "is-current" : index < currentIndex ? "is-complete" : ""} key={value}><span>{index < currentIndex ? <Icon name="check" /> : index + 1}</span><div><strong>{label}</strong><small>{description}</small></div></li>)}</ol>;
}

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : "The report could not be prepared.";
}

import { useMemo, useState } from "react";

import { api } from "../api/client";
import type {
  DataComparisonCell,
  DataQualityIssue,
  DataQualityResult,
  DataReviewComparisonInput,
  DataReviewComparisonResponse,
  DataReviewCube,
  DataReviewSliceInput,
  DataReviewValidationInput,
  PlanningValidationEvidence
} from "../api/types";
import { Icon } from "./Icon";

interface DataValidationWorkbenchProps {
  slice: DataReviewSliceInput;
  cubes: DataReviewCube[];
  csrfToken: string;
  planningTaskId: number | null;
  configuration: {
    validation_type?: "QUALITY" | "COMPARISON";
    rules?: Partial<DataReviewValidationInput["rules"]>;
    target_cube?: string;
    tolerance?: number;
  } | null;
  initialEvidence: PlanningValidationEvidence | null;
}

type ValidationMode = "quality" | "comparison";

export function DataValidationWorkbench({ slice, cubes, csrfToken, planningTaskId, configuration, initialEvidence }: DataValidationWorkbenchProps) {
  const configuredRules = configuration?.rules;
  const [mode, setMode] = useState<ValidationMode>(configuration?.validation_type === "COMPARISON" ? "comparison" : "quality");
  const [checkMissing, setCheckMissing] = useState(configuredRules?.check_missing ?? true);
  const [checkZero, setCheckZero] = useState(configuredRules?.check_zero ?? false);
  const [minimum, setMinimum] = useState(configuredRules?.minimum == null ? "" : String(configuredRules.minimum));
  const [maximum, setMaximum] = useState(configuredRules?.maximum == null ? "" : String(configuredRules.maximum));
  const [targetCube, setTargetCube] = useState(configuration?.target_cube ?? "");
  const [tolerance, setTolerance] = useState(String(configuration?.tolerance ?? 0));
  const [quality, setQuality] = useState<DataQualityResult | null>(null);
  const [comparison, setComparison] = useState<DataReviewComparisonResponse["comparison"] | null>(null);
  const [selectedIssue, setSelectedIssue] = useState<DataQualityIssue | null>(null);
  const [selectedDifference, setSelectedDifference] = useState<DataComparisonCell | null>(null);
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<PlanningValidationEvidence | null>(initialEvidence);
  const [governanceBusy, setGovernanceBusy] = useState(false);
  const [taskCompleted, setTaskCompleted] = useState(false);

  const comparisonInput = useMemo<DataReviewComparisonInput | null>(() => {
    if (!targetCube.trim()) return null;
    return {
      source: slice,
      target: { ...slice, cube: targetCube.trim() },
      tolerance: numericValue(tolerance, "Tolerance", 0),
      max_mismatches: 500,
      include_cells: false,
      planning_task_id: planningTaskId
    };
  }, [planningTaskId, slice, targetCube, tolerance]);

  function validationInput(): DataReviewValidationInput {
    const normalizedMinimum = optionalNumber(minimum, "Minimum");
    const normalizedMaximum = optionalNumber(maximum, "Maximum");
    if (normalizedMinimum != null && normalizedMaximum != null && normalizedMinimum > normalizedMaximum) {
      throw new Error("Minimum cannot be greater than maximum.");
    }
    return {
      slice,
      rules: {
        check_missing: checkMissing,
        check_zero: checkZero,
        minimum: normalizedMinimum,
        maximum: normalizedMaximum,
        max_issues: 500
      },
      planning_task_id: planningTaskId
    };
  }

  async function runQuality() {
    setError(null);
    setSelectedIssue(null);
    try {
      setBusy(true);
      const response = await api.validateDataReview(validationInput(), csrfToken);
      setQuality(response.validation.result);
      setEvidence(response.task_validation ?? null);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  async function runComparison() {
    setError(null);
    setSelectedDifference(null);
    try {
      if (!comparisonInput) throw new Error("Select the target reporting cube.");
      setBusy(true);
      const response = await api.compareDataReview(comparisonInput, csrfToken);
      setComparison(response.comparison);
      setEvidence(response.task_validation ?? null);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  async function acknowledgeWarnings() {
    if (!evidence) return;
    setGovernanceBusy(true);
    setError(null);
    try {
      const response = await api.acknowledgePlanningValidation(evidence.validation_id, csrfToken);
      setEvidence(response.validation);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setGovernanceBusy(false);
    }
  }

  async function completeTask() {
    if (!planningTaskId) return;
    setGovernanceBusy(true);
    setError(null);
    try {
      await api.updateTask(planningTaskId, "COMPLETED", csrfToken);
      setTaskCompleted(true);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setGovernanceBusy(false);
    }
  }

  async function exportEvidence() {
    setError(null);
    try {
      setExporting(true);
      if (mode === "quality") {
        const blob = await api.exportDataReviewValidation(validationInput(), csrfToken);
        downloadBlob(blob, `${safeFilename(slice.cube)}-data-quality.xlsx`);
      } else {
        if (!comparisonInput) throw new Error("Select the target reporting cube.");
        const blob = await api.exportDataReviewComparison(comparisonInput, csrfToken);
        downloadBlob(blob, `${safeFilename(slice.cube)}-to-${safeFilename(targetCube)}-validation.xlsx`);
      }
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setExporting(false);
    }
  }

  return <section className="panel validation-workbench">
    <header className="validation-workbench__header">
      <div><span className="eyebrow">Business validation</span><h2>Validate this Planning grid</h2><p>Check data quality or reconcile the same intersection with a target cube. Validation is read-only.</p></div>
      <span className="read-only-badge"><Icon name="check" /> No data changes</span>
    </header>

    {planningTaskId && <TaskValidationGovernance evidence={evidence} completed={taskCompleted} busy={governanceBusy} onAcknowledge={() => void acknowledgeWarnings()} onComplete={() => void completeTask()} />}

    <div className="validation-tabs" role="tablist" aria-label="Validation method">
      <button type="button" role="tab" aria-selected={mode === "quality"} className={mode === "quality" ? "is-active" : ""} onClick={() => { setMode("quality"); setError(null); }}><Icon name="check" /><span><strong>Quality checks</strong><small>Missing, zero, and thresholds</small></span></button>
      <button type="button" role="tab" aria-selected={mode === "comparison"} className={mode === "comparison" ? "is-active" : ""} onClick={() => { setMode("comparison"); setError(null); }}><Icon name="data" /><span><strong>Compare target cube</strong><small>Source-to-target reconciliation</small></span></button>
    </div>

    {mode === "quality" ? <QualityControls checkMissing={checkMissing} checkZero={checkZero} minimum={minimum} maximum={maximum} onCheckMissing={setCheckMissing} onCheckZero={setCheckZero} onMinimum={setMinimum} onMaximum={setMaximum} /> : <ComparisonControls sourceCube={slice.cube} targetCube={targetCube} tolerance={tolerance} cubes={cubes} onTargetCube={setTargetCube} onTolerance={setTolerance} />}

    {error && <div className="validation-error" role="alert"><Icon name="alert" /><span><strong>Validation could not be completed</strong><small>{error}</small></span></div>}

    <div className="validation-actions">
      <span><Icon name="check" /> Uses the live intersection displayed above</span>
      {(mode === "quality" ? quality : comparison) && <button type="button" className="button button--quiet" disabled={exporting} onClick={() => void exportEvidence()}>{exporting ? <><span className="spinner" /> Creating Excel...</> : <><Icon name="reports" /> Export evidence</>}</button>}
      <button type="button" className="button button--primary" disabled={busy || (mode === "comparison" && !targetCube)} onClick={() => void (mode === "quality" ? runQuality() : runComparison())}>{busy ? <><span className="spinner" /> Validating...</> : <>{mode === "quality" ? "Run quality checks" : "Compare live data"}<Icon name="arrow" /></>}</button>
    </div>

    {mode === "quality" && quality && <QualityResult result={quality} selected={selectedIssue} onSelect={setSelectedIssue} />}
    {mode === "comparison" && comparison && <ComparisonResult comparison={comparison} sourcePov={slice.pov} targetPov={comparisonInput?.target.pov ?? {}} selected={selectedDifference} onSelect={setSelectedDifference} />}
  </section>;
}

function TaskValidationGovernance({ evidence, completed, busy, onAcknowledge, onComplete }: { evidence: PlanningValidationEvidence | null; completed: boolean; busy: boolean; onAcknowledge: () => void; onComplete: () => void }) {
  if (completed) return <div className="task-validation-governance task-validation-governance--pass"><Icon name="check" /><div><strong>Validation responsibility completed</strong><small>My Work and cycle progress have been updated.</small></div><a className="button button--quiet" href="#tasks">Return to My Work</a></div>;
  if (!evidence) return <div className="task-validation-governance"><Icon name="tasks" /><div><strong>Assigned validation</strong><small>Run the configured check. The task can be completed only after a pass or an acknowledged warning.</small></div></div>;
  const acknowledged = Boolean(evidence.warning_acknowledged_at);
  return <div className={`task-validation-governance task-validation-governance--${evidence.status.toLowerCase()}`}><Icon name={evidence.status === "PASS" ? "check" : "alert"} /><div><strong>{evidence.status === "PASS" ? "Ready to complete" : evidence.status === "WARNING" ? acknowledged ? "Warnings acknowledged" : "Review and acknowledge warnings" : "Resolve exceptions before completing"}</strong><small>{evidence.checked_cells.toLocaleString()} cells checked · {evidence.exception_count.toLocaleString()} exceptions · {new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(evidence.performed_at))}</small></div>{evidence.status === "WARNING" && !acknowledged ? <button type="button" className="button button--secondary" disabled={busy} onClick={onAcknowledge}>{busy ? <span className="spinner" /> : <Icon name="check" />} Acknowledge warnings</button> : evidence.completion_allowed ? <button type="button" className="button button--primary" disabled={busy} onClick={onComplete}>{busy ? <span className="spinner" /> : <Icon name="check" />} Complete validation task</button> : null}</div>;
}

function QualityControls({ checkMissing, checkZero, minimum, maximum, onCheckMissing, onCheckZero, onMinimum, onMaximum }: { checkMissing: boolean; checkZero: boolean; minimum: string; maximum: string; onCheckMissing: (value: boolean) => void; onCheckZero: (value: boolean) => void; onMinimum: (value: string) => void; onMaximum: (value: string) => void }) {
  return <div className="quality-controls">
    <label className={`validation-rule${checkMissing ? " is-enabled" : ""}`}><input type="checkbox" checked={checkMissing} onChange={(event) => onCheckMissing(event.target.checked)} /><span><strong>Missing values</strong><small>Fail intersections with no Planning data.</small></span></label>
    <label className={`validation-rule${checkZero ? " is-enabled" : ""}`}><input type="checkbox" checked={checkZero} onChange={(event) => onCheckZero(event.target.checked)} /><span><strong>Zero values</strong><small>Flag zeros as warnings for review.</small></span></label>
    <label className="validation-number"><span>Minimum allowed</span><input type="number" step="any" value={minimum} onChange={(event) => onMinimum(event.target.value)} placeholder="No minimum" /><small>Values below this fail.</small></label>
    <label className="validation-number"><span>Maximum allowed</span><input type="number" step="any" value={maximum} onChange={(event) => onMaximum(event.target.value)} placeholder="No maximum" /><small>Values above this fail.</small></label>
  </div>;
}

function ComparisonControls({ sourceCube, targetCube, tolerance, cubes, onTargetCube, onTolerance }: { sourceCube: string; targetCube: string; tolerance: string; cubes: DataReviewCube[]; onTargetCube: (value: string) => void; onTolerance: (value: string) => void }) {
  const targets = cubes.filter((cube) => cube.name.toLowerCase() !== sourceCube.toLowerCase());
  return <div className="comparison-controls">
    <div className="comparison-cube"><small>Source cube</small><strong>{sourceCube}</strong><span>Current displayed intersection</span></div>
    <span className="comparison-arrow"><Icon name="arrow" /></span>
    <label className="validation-number"><span>Target cube *</span><input list="validation-target-cubes" value={targetCube} onChange={(event) => onTargetCube(event.target.value)} placeholder="Select reporting cube" /><datalist id="validation-target-cubes">{targets.map((cube) => <option value={cube.name} key={cube.name} />)}</datalist><small>The same POV, rows, and columns are queried.</small></label>
    <label className="validation-number"><span>Allowed numeric difference</span><input type="number" min="0" step="any" value={tolerance} onChange={(event) => onTolerance(event.target.value)} /><small>Differences within tolerance match.</small></label>
  </div>;
}

function QualityResult({ result, selected, onSelect }: { result: DataQualityResult; selected: DataQualityIssue | null; onSelect: (issue: DataQualityIssue) => void }) {
  return <div className="validation-result">
    <ValidationResultHeader status={result.status} title={result.status === "PASS" ? "All configured checks passed" : result.status === "WARNING" ? "Review the warnings" : "Data-quality exceptions found"} description={`${result.checked_cells.toLocaleString()} cells checked · ${result.issue_count.toLocaleString()} exceptions`} />
    <div className="validation-metrics"><Metric label="Checked" value={result.checked_cells} /><Metric label="Passed" value={result.passed_cells} tone="success" /><Metric label="Missing" value={result.missing_count} tone={result.missing_count ? "danger" : undefined} /><Metric label="Zero" value={result.zero_count} tone={result.zero_count ? "warning" : undefined} /><Metric label="Out of range" value={result.below_minimum_count + result.above_maximum_count} tone={result.below_minimum_count + result.above_maximum_count ? "danger" : undefined} /></div>
    {result.issues.length ? <div className="validation-evidence"><div className="validation-table-wrap"><table className="validation-table"><thead><tr><th>Status</th><th>Check</th><th>Row intersection</th><th>Column intersection</th><th>Value</th></tr></thead><tbody>{result.issues.map((issue, index) => <tr className={selected === issue ? "is-selected" : ""} tabIndex={0} onClick={() => onSelect(issue)} onKeyDown={(event) => { if (event.key === "Enter") onSelect(issue); }} key={`${issue.row_headers.join("|")}-${issue.column_headers.join("|")}-${index}`}><td><span className={`validation-status validation-status--${issue.severity.toLowerCase()}`}>{issue.severity === "ERROR" ? "Fail" : "Warning"}</span></td><td>{qualityLabel(issue.code)}</td><td>{intersection(issue.row_headers)}</td><td>{intersection(issue.column_headers)}</td><td>{displayValue(issue.raw_value)}</td></tr>)}</tbody></table></div>{selected && <QualityInspector issue={selected} />}</div> : <ValidationEmpty title="No exceptions found" description="Every selected cell passed the configured quality checks." />}
    {result.truncated && <p className="validation-truncated"><Icon name="alert" /> Only the first {result.issues.length.toLocaleString()} exceptions are displayed and exported.</p>}
  </div>;
}

function ComparisonResult({ comparison, sourcePov, targetPov, selected, onSelect }: { comparison: DataReviewComparisonResponse["comparison"]; sourcePov: Record<string, string>; targetPov: Record<string, string>; selected: DataComparisonCell | null; onSelect: (cell: DataComparisonCell) => void }) {
  const result = comparison.result;
  const cells: DataComparisonCell[] = result.cells.length
    ? result.cells
    : result.mismatches.map((cell) => ({ ...cell, matches: false }));
  const differences = cells.filter((cell) => !cell.matches);
  const status = result.compared_cells === result.matched_cells ? "PASS" : "FAIL";
  return <div className="validation-result">
    <ValidationResultHeader status={status} title={status === "PASS" ? "Source and target match" : "Source-to-target differences found"} description={`${comparison.source_cube} compared with ${comparison.target_cube} at tolerance ${result.tolerance}`} />
    <div className="validation-metrics validation-metrics--comparison"><Metric label="Compared" value={result.compared_cells} /><Metric label="Matched" value={result.matched_cells} tone="success" /><Metric label="Different" value={result.compared_cells - result.matched_cells} tone={status === "FAIL" ? "danger" : undefined} /></div>
    {differences.length ? <div className="validation-evidence"><div className="validation-table-wrap"><table className="validation-table"><thead><tr><th>Row intersection</th><th>Column intersection</th><th>Source</th><th>Target</th><th>Difference</th></tr></thead><tbody>{differences.map((cell, index) => <tr className={selected === cell ? "is-selected" : ""} tabIndex={0} onClick={() => onSelect(cell)} onKeyDown={(event) => { if (event.key === "Enter") onSelect(cell); }} key={`${cell.row_headers.join("|")}-${cell.column_headers.join("|")}-${index}`}><td>{intersection(cell.row_headers)}</td><td>{intersection(cell.column_headers)}</td><td>{displayValue(cell.source_value)}</td><td>{displayValue(cell.target_value)}</td><td>{displayValue(cell.difference)}</td></tr>)}</tbody></table></div>{selected && <ComparisonInspector cell={selected} sourceCube={comparison.source_cube} targetCube={comparison.target_cube} sourcePov={sourcePov} targetPov={targetPov} />}</div> : <ValidationEmpty title="No differences found" description="Every source cell matches its target within the configured tolerance." />}
  </div>;
}

function ValidationResultHeader({ status, title, description }: { status: string; title: string; description: string }) {
  return <header className="validation-result__header"><div><span className={`validation-verdict validation-verdict--${status.toLowerCase()}`}><Icon name={status === "PASS" ? "check" : "alert"} /> {status}</span><h3>{title}</h3><p>{description}</p></div></header>;
}

function Metric({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return <span className={tone ? `is-${tone}` : ""}><small>{label}</small><strong>{value.toLocaleString()}</strong></span>;
}

function QualityInspector({ issue }: { issue: DataQualityIssue }) {
  return <aside className="validation-inspector"><header><span className={`validation-status validation-status--${issue.severity.toLowerCase()}`}>{issue.severity}</span><h4>{qualityLabel(issue.code)}</h4></header><p>{issue.message}</p><dl><div><dt>POV</dt><dd>{issue.pov.map(([dimension, member]) => `${dimension}: ${member}`).join(" · ") || "No fixed POV"}</dd></div><div><dt>Row</dt><dd>{intersection(issue.row_headers)}</dd></div><div><dt>Column</dt><dd>{intersection(issue.column_headers)}</dd></div><div><dt>Value</dt><dd>{displayValue(issue.raw_value)}</dd></div></dl></aside>;
}

function ComparisonInspector({ cell, sourceCube, targetCube, sourcePov, targetPov }: { cell: DataComparisonCell; sourceCube: string; targetCube: string; sourcePov: Record<string, string>; targetPov: Record<string, string> }) {
  return <aside className="validation-inspector"><header><span className="validation-status validation-status--error">Difference</span><h4>Reconciliation detail</h4></header><dl><div><dt>Intersection</dt><dd>{intersection(cell.row_headers)} × {intersection(cell.column_headers)}</dd></div><div><dt>{sourceCube}</dt><dd>{displayValue(cell.source_value)}</dd></div><div><dt>{targetCube}</dt><dd>{displayValue(cell.target_value)}</dd></div><div><dt>Difference</dt><dd>{displayValue(cell.difference)}</dd></div><div><dt>Source POV</dt><dd>{mappingLabel(sourcePov)}</dd></div><div><dt>Target POV</dt><dd>{mappingLabel(targetPov)}</dd></div></dl></aside>;
}

function ValidationEmpty({ title, description }: { title: string; description: string }) {
  return <div className="validation-empty"><span><Icon name="check" /></span><strong>{title}</strong><small>{description}</small></div>;
}

function optionalNumber(value: string, label: string) {
  if (!value.trim()) return null;
  return numericValue(value, label);
}

function numericValue(value: string, label: string, fallback?: number) {
  if (!value.trim() && fallback != null) return fallback;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) throw new Error(`${label} must be numeric.`);
  if (label === "Tolerance" && parsed < 0) throw new Error("Tolerance cannot be negative.");
  return parsed;
}

function qualityLabel(code: DataQualityIssue["code"]) {
  return { MISSING: "Missing value", ZERO: "Zero value", BELOW_MINIMUM: "Below minimum", ABOVE_MAXIMUM: "Above maximum", NON_NUMERIC: "Non-numeric value" }[code];
}

function intersection(values: string[]) {
  return values.join(" · ") || "—";
}

function mappingLabel(values: Record<string, string>) {
  return Object.entries(values).map(([dimension, member]) => `${dimension}: ${member}`).join(" · ") || "No fixed POV";
}

function displayValue(value: unknown) {
  if (value == null || ["", "#missing", "missing", "none", "null"].includes(String(value).trim().toLowerCase())) return "Missing";
  const numeric = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numeric) ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 12 }).format(numeric) : String(value);
}

function safeFilename(value: string) {
  return value.trim().replace(/[^a-z0-9_-]+/gi, "-") || "planning";
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : "Planning validation could not be completed.";
}

import { useEffect, useMemo, useState, type FormEvent } from "react";

import { api } from "../api/client";
import type {
  OperationSummary,
  SubstitutionVariableDefinition,
  SubstitutionVariableRunInput
} from "../api/types";
import { ExecutionStep, ResultStep } from "./OperationRunner";
import { Icon } from "./Icon";
import { FeedbackBanner } from "./Feedback";
import { useOperationMonitor } from "./useOperationMonitor";

type RunnerStep = "SETUP" | "REVIEW" | "RUNNING" | "RESULT";
type VariableMode = "UPDATE" | "CREATE";

interface ReviewedVariableChange {
  payload: SubstitutionVariableRunInput;
  currentValue: string | null;
}

interface SubstitutionVariableRunnerProps {
  operation: OperationSummary;
  csrfToken: string;
  planningTaskId?: number | null;
  onBack: () => void;
}

export function SubstitutionVariableRunner({
  operation,
  csrfToken,
  planningTaskId = null,
  onBack
}: SubstitutionVariableRunnerProps) {
  const [step, setStep] = useState<RunnerStep>("SETUP");
  const [mode, setMode] = useState<VariableMode>("UPDATE");
  const [variables, setVariables] = useState<SubstitutionVariableDefinition[]>([]);
  const [scopes, setScopes] = useState<string[]>(["ALL"]);
  const [query, setQuery] = useState("");
  const [scopeFilter, setScopeFilter] = useState("ALL_SCOPES");
  const [selected, setSelected] = useState<SubstitutionVariableDefinition | null>(null);
  const [scope, setScope] = useState("ALL");
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [reviewed, setReviewed] = useState<ReviewedVariableChange | null>(null);
  const [approved, setApproved] = useState(false);
  const [executionId, setExecutionId] = useState<string | null>(null);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { execution, monitorError } = useOperationMonitor(executionId);

  const filteredVariables = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return variables.filter((variable) => {
      const matchesScope = scopeFilter === "ALL_SCOPES" || variable.scope === scopeFilter;
      const matchesQuery = !needle || [variable.name, variable.value, variable.scope]
        .some((item) => item.toLowerCase().includes(needle));
      return matchesScope && matchesQuery;
    });
  }, [query, scopeFilter, variables]);

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
      const response = await api.substitutionVariableCatalog();
      setVariables(response.catalog.variables);
      setScopes(response.catalog.scopes.length ? response.catalog.scopes : ["ALL"]);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setLoadingCatalog(false);
    }
  }

  function chooseMode(nextMode: VariableMode) {
    setMode(nextMode);
    setSelected(null);
    setScope("ALL");
    setName("");
    setValue("");
    setReviewed(null);
    setApproved(false);
    setError(null);
  }

  function selectVariable(variable: SubstitutionVariableDefinition) {
    setSelected(variable);
    setScope(variable.scope);
    setName(variable.name);
    setValue(variable.value);
    setError(null);
  }

  function review(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const normalizedScope = scope.trim();
    const normalizedName = name.trim();
    const normalizedValue = value.trim();
    if (mode === "UPDATE" && !selected) {
      setError("Select the existing substitution variable you want to update.");
      return;
    }
    if (!normalizedScope || !normalizedName || !normalizedValue) {
      setError("Scope, variable name, and value are required.");
      return;
    }
    if (mode === "UPDATE" && normalizedValue === selected?.value) {
      setError("Enter a different value before reviewing this update.");
      return;
    }
    if (mode === "CREATE" && variables.some((variable) => variable.scope.toLowerCase() === normalizedScope.toLowerCase() && variable.name.toLowerCase() === normalizedName.toLowerCase())) {
      setError(`A variable named ${normalizedName} already exists in ${normalizedScope}. Choose Update existing instead.`);
      return;
    }
    setReviewed({
      payload: {
        action: mode,
        scope: normalizedScope,
        name: normalizedName,
        value: normalizedValue,
        expected_current_value: mode === "UPDATE" ? selected!.value : null,
        ...(planningTaskId ? { planning_task_id: planningTaskId } : {})
      },
      currentValue: mode === "UPDATE" ? selected!.value : null
    });
    setApproved(false);
    setStep("REVIEW");
  }

  async function start() {
    if (!reviewed || !approved) return;
    setStarting(true);
    setError(null);
    try {
      const accepted = await api.startSubstitutionVariable(reviewed.payload, csrfToken);
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
    setSelected(null);
    setName("");
    setValue("");
    setError(null);
    setStep("SETUP");
    void loadCatalog();
  }

  return <section className="operation-runner">
    <header className="runner-header">
      <button className="button button--quiet" onClick={onBack}><Icon name="arrow" /> Back to Operations</button>
      <div><span className="eyebrow">{operation.category}</span><h1>{operation.display_name}</h1><p>{operation.description}</p></div>
      <span className="risk-badge">{planningTaskId ? "Assigned task" : operation.risk_level}</span>
    </header>

    <RunnerSteps current={step} />
    {(error || monitorError) && <FeedbackBanner tone="error" title="Action required" message={error || monitorError} />}

    {step === "SETUP" && <form className="panel runner-form variable-runner" onSubmit={review}>
      <div className="panel-heading variable-heading"><div><span className="eyebrow">Step 1</span><h2>Choose the variable change</h2><p>Review live Oracle values before updating an existing definition or creating a new one.</p></div><span className="result-count">{loadingCatalog ? "Loading..." : `${variables.length} live variables`}</span></div>
      <div className="variable-mode-tabs" role="tablist" aria-label="Substitution variable action">
        <button type="button" role="tab" aria-selected={mode === "UPDATE"} className={mode === "UPDATE" ? "is-active" : ""} onClick={() => chooseMode("UPDATE")}><Icon name="settings" /><span><strong>Update existing</strong><small>Choose a live variable and change its value safely.</small></span></button>
        <button type="button" role="tab" aria-selected={mode === "CREATE"} className={mode === "CREATE" ? "is-active" : ""} onClick={() => chooseMode("CREATE")}><Icon name="tasks" /><span><strong>Create new</strong><small>Add a new application- or cube-scoped definition.</small></span></button>
      </div>

      {mode === "UPDATE" ? <div className="variable-update-layout">
        <section className="variable-catalog"><header><div><h3>Select a live variable</h3><p>The observed value protects this update from overwriting a newer Oracle change.</p></div></header>
          <div className="variable-filters"><label className="search-field"><Icon name="search" /><span className="sr-only">Search variables</span><input aria-label="Search variables" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search name, value, or scope" /></label><label><span>Scope</span><select aria-label="Filter variables by scope" value={scopeFilter} onChange={(event) => setScopeFilter(event.target.value)}><option value="ALL_SCOPES">All scopes</option>{scopes.map((item) => <option key={item}>{item}</option>)}</select></label></div>
          {loadingCatalog ? <VariableLoading /> : filteredVariables.length ? <div className="variable-table-wrap"><table className="variable-table"><thead><tr><th>Variable</th><th>Scope</th><th>Current value</th><th><span className="sr-only">Action</span></th></tr></thead><tbody>{filteredVariables.map((variable) => <tr className={selected?.name === variable.name && selected.scope === variable.scope ? "is-selected" : ""} key={`${variable.scope}:${variable.name}`}><td><strong>{variable.name}</strong></td><td><span className="variable-scope-badge">{variable.scope}</span></td><td>{variable.value || <em>Empty</em>}</td><td><button type="button" className="button button--quiet" aria-label={`Edit ${variable.name} in ${variable.scope}`} onClick={() => selectVariable(variable)}>{selected?.name === variable.name && selected.scope === variable.scope ? "Selected" : "Edit"}</button></td></tr>)}</tbody></table></div> : <div className="runner-pair-empty"><Icon name="search" /><span><strong>No variables match</strong><small>Clear the search or select another scope.</small></span></div>}
        </section>
        <VariableEditor mode={mode} selected={selected} scopes={scopes} scope={scope} name={name} value={value} onScope={setScope} onName={setName} onValue={setValue} />
      </div> : <div className="variable-create-layout"><VariableEditor mode={mode} selected={selected} scopes={scopes} scope={scope} name={name} value={value} onScope={setScope} onName={setName} onValue={setValue} /><aside className="panel runner-guidance variable-create-guidance"><span className="eyebrow">Scope guidance</span><h2>Create deliberately</h2><ol><li><span>1</span><div><strong>ALL</strong><p>Makes the variable application-wide.</p></div></li><li><span>2</span><div><strong>Cube name</strong><p>Limits the definition to the selected Planning cube.</p></div></li><li><span>3</span><div><strong>Exact identity</strong><p>The same variable name may exist in different scopes, but cannot be duplicated within one scope.</p></div></li></ol></aside></div>}

      <footer className="runner-actions"><button type="button" className="button button--quiet" onClick={onBack}>Cancel</button><button className="button button--primary" disabled={loadingCatalog}>{mode === "UPDATE" ? "Review Variable Update" : "Review New Variable"} <Icon name="arrow" /></button></footer>
    </form>}

    {step === "REVIEW" && reviewed && <section className="panel runner-review">
      <div className="panel-heading"><span className="eyebrow">Step 2</span><h2>Review the substitution variable change</h2><p>Oracle has not been changed. Confirm the exact scope, name, and value first.</p></div>
      <div className="review-target"><span className="service-card__icon"><Icon name="settings" /></span><div><small>{reviewed.payload.action === "UPDATE" ? "Existing substitution variable" : "New substitution variable"}</small><strong>{reviewed.payload.scope}.{reviewed.payload.name}</strong></div></div>
      <dl className="review-summary"><div><dt>Action</dt><dd>{reviewed.payload.action === "UPDATE" ? "Update existing" : "Create new"}</dd></div><div><dt>Scope</dt><dd>{reviewed.payload.scope}</dd></div><div><dt>Current value</dt><dd>{reviewed.currentValue ?? "Does not exist"}</dd></div><div><dt>New value</dt><dd>{reviewed.payload.value}</dd></div></dl>
      {reviewed.payload.action === "UPDATE" ? <div className="native-import-note"><Icon name="check" /><div><strong>Protected against stale updates</strong><p>The change proceeds only if Oracle still contains the current value shown above.</p></div></div> : <div className="runner-warning"><Icon name="alert" /><div><strong>This creates a new Oracle definition</strong><p>Confirm that the selected scope and name are intentional. An existing definition with the same identity will be rejected.</p></div></div>}
      <label className="runner-approval"><input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} /><span><strong>I reviewed the scope, variable name, and value</strong><small>The platform will apply this one approved change and verify Oracle’s result.</small></span></label>
      <footer className="runner-actions"><button className="button button--quiet" disabled={starting} onClick={() => { setError(null); setStep("SETUP"); }}>Back to inputs</button><button className="button button--primary" disabled={!approved || starting} onClick={() => void start()}>{starting ? <><span className="spinner" /> Applying change...</> : <>{reviewed.payload.action === "UPDATE" ? "Update Variable" : "Create Variable"} <Icon name="arrow" /></>}</button></footer>
    </section>}

    {step === "RUNNING" && <ExecutionStep execution={execution} operation={operation} />}
    {step === "RESULT" && execution && <ResultStep execution={execution} onAgain={runAgain} onBack={onBack} />}
  </section>;
}

interface VariableEditorProps {
  mode: VariableMode;
  selected: SubstitutionVariableDefinition | null;
  scopes: string[];
  scope: string;
  name: string;
  value: string;
  onScope: (value: string) => void;
  onName: (value: string) => void;
  onValue: (value: string) => void;
}

function VariableEditor({ mode, selected, scopes, scope, name, value, onScope, onName, onValue }: VariableEditorProps) {
  return <section className="variable-editor"><header><span className="service-card__icon"><Icon name="settings" /></span><div><h3>{mode === "UPDATE" ? "Enter the new value" : "Define the new variable"}</h3><p>{mode === "UPDATE" ? selected ? `${selected.scope}.${selected.name} currently equals ${selected.value}.` : "Select a variable from the live catalog first." : "Use an application-wide or discovered cube scope."}</p></div></header>
    <div className="variable-editor-fields">
      <label className="runner-field"><span>Scope *</span><input list="variable-scopes" aria-label="Variable scope" value={scope} disabled={mode === "UPDATE"} onChange={(event) => onScope(event.target.value)} placeholder="ALL or exact cube name" /><datalist id="variable-scopes">{scopes.map((item) => <option value={item} key={item} />)}</datalist><small>{mode === "UPDATE" ? "Scope comes from the selected live definition." : "ALL is application-wide; otherwise use an exact discovered cube name."}</small></label>
      <label className="runner-field"><span>Variable name *</span><input aria-label="Variable name" value={name} disabled={mode === "UPDATE"} onChange={(event) => onName(event.target.value)} placeholder="CurForecastYear" maxLength={80} /></label>
      <label className="runner-field variable-value-field"><span>{mode === "UPDATE" ? "New value *" : "Initial value *"}</span><input aria-label={mode === "UPDATE" ? "New variable value" : "Initial variable value"} value={value} disabled={mode === "UPDATE" && !selected} onChange={(event) => onValue(event.target.value)} placeholder="FY27" maxLength={255} /><small>Enter the exact member name or text expected by your Oracle jobs, forms, rules, and reports.</small></label>
    </div>
  </section>;
}

function VariableLoading() {
  return <div className="variable-loading" aria-live="polite"><span className="spinner" /><div><strong>Loading live Oracle variables</strong><small>Reading application- and cube-scoped definitions.</small></div></div>;
}

function RunnerSteps({ current }: { current: RunnerStep }) {
  const steps: [RunnerStep, string, string][] = [["SETUP", "Prepare", "Select one change"], ["REVIEW", "Review", "Confirm scope and value"], ["RUNNING", "Apply", "Verify with Oracle"], ["RESULT", "Result", "Review outcome"]];
  const currentIndex = steps.findIndex(([value]) => value === current);
  return <ol className="runner-steps" aria-label="Operation progress">{steps.map(([value, label, description], index) => <li className={index === currentIndex ? "is-current" : index < currentIndex ? "is-complete" : ""} key={value}><span>{index < currentIndex ? <Icon name="check" /> : index + 1}</span><div><strong>{label}</strong><small>{description}</small></div></li>)}</ol>;
}

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : "The substitution variable change could not be completed.";
}

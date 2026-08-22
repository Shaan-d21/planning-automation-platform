import { useEffect, useMemo, useState, type FormEvent } from "react";

import { api } from "../api/client";
import type {
  OperationSummary,
  UserVariableDefinition,
  UserVariableRunInput,
  UserVariableValue
} from "../api/types";
import { FeedbackBanner } from "./Feedback";
import { Icon } from "./Icon";
import { ExecutionStep, ResultStep } from "./OperationRunner";
import { useOperationMonitor } from "./useOperationMonitor";

type RunnerStep = "SETUP" | "REVIEW" | "RUNNING" | "RESULT";

interface UserVariableRunnerProps {
  operation: OperationSummary;
  csrfToken: string;
  currentUsername: string;
  canManageUsers: boolean;
  planningTaskId?: number | null;
  onBack: () => void;
}

export function UserVariableRunner({
  operation,
  csrfToken,
  currentUsername,
  canManageUsers,
  planningTaskId = null,
  onBack
}: UserVariableRunnerProps) {
  const [step, setStep] = useState<RunnerStep>("SETUP");
  const [targetUser, setTargetUser] = useState(currentUsername);
  const [definitions, setDefinitions] = useState<UserVariableDefinition[]>([]);
  const [values, setValues] = useState<UserVariableValue[]>([]);
  const [selectedName, setSelectedName] = useState("");
  const [member, setMember] = useState("");
  const [reviewed, setReviewed] = useState<UserVariableRunInput | null>(null);
  const [approved, setApproved] = useState(false);
  const [loading, setLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [executionId, setExecutionId] = useState<string | null>(null);
  const { execution, monitorError } = useOperationMonitor(executionId);

  const selectedDefinition = useMemo(
    () => definitions.find((item) => item.name === selectedName) ?? null,
    [definitions, selectedName]
  );
  const currentValue = useMemo(
    () => values.find((item) => item.name.toLowerCase() === selectedName.toLowerCase()) ?? null,
    [selectedName, values]
  );

  useEffect(() => { void loadCatalog(currentUsername); }, [currentUsername]);
  useEffect(() => { if (execution?.terminal) setStep("RESULT"); }, [execution]);

  async function loadCatalog(userName = targetUser) {
    const normalized = userName.trim();
    if (!normalized) {
      setError("Enter the Oracle user name before loading variables.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const response = await api.userVariableCatalog(normalized);
      setTargetUser(response.catalog.user_name);
      setDefinitions(response.catalog.definitions);
      setValues(response.catalog.values);
      setSelectedName("");
      setMember("");
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setLoading(false);
    }
  }

  function chooseDefinition(name: string) {
    setSelectedName(name);
    const existing = values.find((item) => item.name.toLowerCase() === name.toLowerCase());
    setMember(existing?.member ?? "");
    setError(null);
  }

  function review(event: FormEvent) {
    event.preventDefault();
    if (!selectedDefinition) {
      setError("Choose a live Planning user variable.");
      return;
    }
    const normalizedMember = member.trim();
    if (!normalizedMember) {
      setError("Enter the exact member to assign.");
      return;
    }
    if (currentValue?.member === normalizedMember) {
      setError("Choose a different member before reviewing the change.");
      return;
    }
    setReviewed({
      user_name: targetUser,
      name: selectedDefinition.name,
      dimension: selectedDefinition.dimension,
      member: normalizedMember,
      expected_current_member: currentValue?.member ?? null,
      ...(planningTaskId ? { planning_task_id: planningTaskId } : {})
    });
    setApproved(false);
    setStep("REVIEW");
  }

  async function start() {
    if (!reviewed || !approved) return;
    setStarting(true);
    setError(null);
    try {
      const accepted = await api.startUserVariable(reviewed, csrfToken);
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
    setStep("SETUP");
    void loadCatalog(targetUser);
  }

  return <section className="operation-runner">
    <header className="runner-header">
      <button className="button button--quiet" onClick={onBack}><Icon name="arrow" /> Back to Operations</button>
      <div><span className="eyebrow">{operation.category}</span><h1>{operation.display_name}</h1><p>{operation.description}</p></div>
      <span className="risk-badge">{operation.risk_level}</span>
    </header>

    {(error || monitorError) && <FeedbackBanner tone="error" title="Action required" message={error || monitorError} />}

    {step === "SETUP" && <form className="panel runner-form variable-runner" onSubmit={review}>
      <div className="panel-heading"><div><span className="eyebrow">Step 1</span><h2>Choose a user-variable value</h2><p>Values personalize forms and dashboards for one Oracle Planning user. Definitions are not created on this screen.</p></div><span className="result-count">{loading ? "Loading..." : `${definitions.length} variables`}</span></div>

      <div className="runner-form-grid">
        <label className="runner-field"><span>Oracle user *</span><input aria-label="Oracle user" value={targetUser} disabled={!canManageUsers || loading} onChange={(event) => setTargetUser(event.target.value)} /><small>{canManageUsers ? "Administrators may enter another exact Oracle user name, then refresh." : "Your role can update only your own assignments."}</small></label>
        {canManageUsers && <div className="runner-field"><span>&nbsp;</span><button type="button" className="button button--quiet" disabled={loading} onClick={() => void loadCatalog()}><Icon name="refresh" /> Load this user</button></div>}
        <label className="runner-field"><span>User variable *</span><select aria-label="User variable" value={selectedName} disabled={loading} onChange={(event) => chooseDefinition(event.target.value)}><option value="">Select a live variable</option>{definitions.map((item) => <option value={item.name} key={`${item.name}:${item.dimension}`}>{item.name} · {item.dimension}</option>)}</select><small>The list comes from the connected Planning application.</small></label>
        <label className="runner-field"><span>New member *</span><input aria-label="New user variable member" value={member} disabled={!selectedDefinition} onChange={(event) => setMember(event.target.value)} placeholder={selectedDefinition ? `Exact ${selectedDefinition.dimension} member` : "Choose a variable first"} /><small>Current value: {currentValue?.member || "Not assigned"}</small></label>
      </div>
      {!loading && !definitions.length && <div className="runner-pair-empty"><Icon name="settings" /><span><strong>No user variables were returned</strong><small>Ask a Planning administrator to define user variables, or confirm this Oracle version exposes them through REST.</small></span></div>}
      <footer className="runner-actions"><button type="button" className="button button--quiet" onClick={onBack}>Cancel</button><button className="button button--primary" disabled={loading || !selectedDefinition}>Review User Variable <Icon name="arrow" /></button></footer>
    </form>}

    {step === "REVIEW" && reviewed && <section className="panel runner-review">
      <div className="panel-heading"><span className="eyebrow">Step 2</span><h2>Review the personal Planning context</h2><p>Oracle has not been changed. Confirm the exact user, variable, dimension, and member.</p></div>
      <dl className="review-summary"><div><dt>Oracle user</dt><dd>{reviewed.user_name}</dd></div><div><dt>User variable</dt><dd>{reviewed.name}</dd></div><div><dt>Dimension</dt><dd>{reviewed.dimension}</dd></div><div><dt>Current member</dt><dd>{reviewed.expected_current_member ?? "Not assigned"}</dd></div><div><dt>New member</dt><dd>{reviewed.member}</dd></div></dl>
      <div className="native-import-note"><Icon name="check" /><div><strong>Protected against stale changes</strong><p>The update runs only if Oracle still has the current assignment shown above.</p></div></div>
      <label className="runner-approval"><input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} /><span><strong>I reviewed this user-variable assignment</strong><small>The platform will update one value and verify it directly with Oracle.</small></span></label>
      <footer className="runner-actions"><button className="button button--quiet" disabled={starting} onClick={() => setStep("SETUP")}>Back to inputs</button><button className="button button--primary" disabled={!approved || starting} onClick={() => void start()}>{starting ? "Applying..." : <>Update User Variable <Icon name="arrow" /></>}</button></footer>
    </section>}

    {step === "RUNNING" && <ExecutionStep execution={execution} operation={operation} />}
    {step === "RESULT" && execution && <ResultStep execution={execution} onAgain={runAgain} onBack={onBack} />}
  </section>;
}

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : "The user-variable request could not be completed.";
}

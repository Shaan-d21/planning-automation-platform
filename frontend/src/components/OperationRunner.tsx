import { useEffect, useRef, useState, type FormEvent } from "react";

import { api } from "../api/client";
import type {
  BusinessRuleRTPDefinition,
  BusinessRuleRTPRegistryStatus,
  BusinessRuleRunInput,
  DataMapRunInput,
  OperationExecution,
  OperationSummary
} from "../api/types";
import { Icon } from "./Icon";
import { OracleLoadStatistics } from "./OracleLoadStatistics";
import { FeedbackBanner } from "./Feedback";
import { useOperationMonitor } from "./useOperationMonitor";

type RunnerCode = "business-rules" | "data-maps";
type RunnerStep = "SETUP" | "REVIEW" | "RUNNING" | "RESULT";
type ReviewedRun =
  | { code: "business-rules"; payload: BusinessRuleRunInput }
  | { code: "data-maps"; payload: DataMapRunInput };

interface PairInput {
  id: number;
  name: string;
  value: string;
}

interface OperationRunnerProps {
  operation: OperationSummary;
  csrfToken: string;
  canManageCatalog: boolean;
  planningTaskId?: number | null;
  agentDraftId?: string | null;
  onBack: () => void;
}

export function OperationRunner({ operation, csrfToken, canManageCatalog, planningTaskId = null, agentDraftId = null, onBack }: OperationRunnerProps) {
  const code = operation.code as RunnerCode;
  const [step, setStep] = useState<RunnerStep>("SETUP");
  const [artifacts, setArtifacts] = useState<string[]>([]);
  const [target, setTarget] = useState("");
  const [clearTarget, setClearTarget] = useState(false);
  const [primaryPairs, setPrimaryPairs] = useState<PairInput[]>([]);
  const [exclusionPairs, setExclusionPairs] = useState<PairInput[]>([]);
  const [reviewed, setReviewed] = useState<ReviewedRun | null>(null);
  const [approved, setApproved] = useState(false);
  const [executionId, setExecutionId] = useState<string | null>(null);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [rtpDefinition, setRtpDefinition] = useState<BusinessRuleRTPDefinition | null>(null);
  const [rtpRegistry, setRtpRegistry] = useState<BusinessRuleRTPRegistryStatus | null>(null);
  const [loadingRtpRegistry, setLoadingRtpRegistry] = useState(code === "business-rules");
  const [loadingRtp, setLoadingRtp] = useState(false);
  const [importingRtp, setImportingRtp] = useState(false);
  const [rtpNotice, setRtpNotice] = useState<string | null>(null);
  const [handoffLoaded, setHandoffLoaded] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nextPairId = useRef(1);
  const { execution, monitorError } = useOperationMonitor(executionId);

  useEffect(() => {
    let active = true;
    setLoadingCatalog(true);
    setError(null);
    if (code === "business-rules") setLoadingRtpRegistry(true);
    async function loadCatalog() {
      try {
        let jobs: string[];
        if (code === "business-rules") {
          const ruleCatalog = await api.businessRuleCatalog();
          if (!active) return;
          jobs = ruleCatalog.jobs;
          setRtpRegistry(ruleCatalog.rtp_registry ?? null);
        } else {
          const catalog = await api.operationArtifacts(code);
          if (!active) return;
          jobs = catalog.jobs;
        }
        setArtifacts(jobs);
        if (jobs.length === 0) {
          setError(`No ${code === "business-rules" ? "Business Rules" : "Data Maps"} are visible to the configured Oracle user.`);
        }
      } catch (reason) {
        if (active) setError(errorMessage(reason));
      } finally {
        if (!active) return;
        setLoadingCatalog(false);
        if (code === "business-rules") setLoadingRtpRegistry(false);
      }
    }
    void loadCatalog();
    return () => { active = false; };
  }, [code]);

  useEffect(() => {
    if (code !== "business-rules" || !target) {
      setRtpDefinition(null);
      return;
    }
    let active = true;
    setLoadingRtp(true);
    setRtpNotice(null);
    api.businessRuleRTPDefinition(target)
      .then((response) => {
        if (!active) return;
        setRtpDefinition(response.definition);
        if (response.definition) {
          setPrimaryPairs((current) => {
            const currentValues = new Map(current.map((item) => [item.name.toLowerCase(), item.value]));
            return response.definition!.prompts.map((prompt) => ({
              id: nextPairId.current++,
              name: prompt.name,
              value: currentValues.get(prompt.name.toLowerCase()) || ""
            }));
          });
        }
        setRtpNotice(response.message);
      })
      .catch((reason: unknown) => active && setError(errorMessage(reason)))
      .finally(() => active && setLoadingRtp(false));
    return () => { active = false; };
  }, [code, target]);

  useEffect(() => {
    if (!agentDraftId || code !== "business-rules") return;
    let active = true;
    api.agentOperationHandoff(agentDraftId, code)
      .then((response) => {
        if (!active) return;
        const draft = response.action_draft;
        setTarget(draft.artifact_name || "");
        const prompts = stringRecord(draft.input_values.runtime_prompts);
        setPrimaryPairs(Object.entries(prompts).map(([name, value]) => ({ id: nextPairId.current++, name, value })));
        setHandoffLoaded(true);
      })
      .catch((reason: unknown) => active && setError(errorMessage(reason)));
    return () => { active = false; };
  }, [agentDraftId, code]);

  useEffect(() => {
    if (execution?.terminal) setStep("RESULT");
  }, [execution]);

  function addPair(type: "primary" | "exclusion") {
    const pair = { id: nextPairId.current++, name: "", value: "" };
    if (type === "primary") setPrimaryPairs((current) => [...current, pair]);
    else setExclusionPairs((current) => [...current, pair]);
  }

  function updatePair(type: "primary" | "exclusion", id: number, key: "name" | "value", value: string) {
    const update = (pairs: PairInput[]) => pairs.map((pair) => pair.id === id ? { ...pair, [key]: value } : pair);
    if (type === "primary") setPrimaryPairs(update);
    else setExclusionPairs(update);
  }

  function removePair(type: "primary" | "exclusion", id: number) {
    if (type === "primary") setPrimaryPairs((current) => current.filter((pair) => pair.id !== id));
    else setExclusionPairs((current) => current.filter((pair) => pair.id !== id));
  }

  function review(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (!target) {
      setError(`Select a ${code === "business-rules" ? "Business Rule" : "Data Map"} before continuing.`);
      return;
    }
    try {
      const primary = code === "business-rules" && rtpDefinition
        ? registeredPromptsToRecord(primaryPairs, rtpDefinition)
        : pairsToRecord(primaryPairs, code === "business-rules" ? "Runtime prompt" : "Member override");
      setReviewed(code === "business-rules"
        ? { code, payload: { rule_name: target, runtime_prompts: primary } }
        : { code, payload: { data_map_name: target, clear_target: clearTarget, member_overrides: primary, exclusion_overrides: pairsToRecord(exclusionPairs, "Exclusion override") } });
      setApproved(false);
      setStep("REVIEW");
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }

  async function importRtpRegistry(file: File) {
    setImportingRtp(true);
    setError(null);
    setRtpNotice(null);
    try {
      const response = await api.importBusinessRuleRTPRegistry(file, csrfToken);
      setRtpNotice(response.message);
      await refreshRtpRegistryStatus();
      if (target) {
        const refreshed = await api.businessRuleRTPDefinition(target);
        setRtpDefinition(refreshed.definition);
        if (refreshed.definition) {
          setPrimaryPairs(refreshed.definition.prompts.map((prompt) => ({ id: nextPairId.current++, name: prompt.name, value: "" })));
        }
      }
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setImportingRtp(false);
    }
  }

  async function refreshRtpRegistryStatus() {
    setLoadingRtpRegistry(true);
    try {
      const response = await api.businessRuleRTPRegistryStatus();
      setRtpRegistry(response.registry);
      if (response.live_catalog_error) {
        setRtpNotice(
          "The registry is available, but the live Oracle Business Rule catalog could not be compared."
        );
      }
    } finally {
      setLoadingRtpRegistry(false);
    }
  }

  function selectTarget(value: string) {
    setTarget(value);
    setPrimaryPairs([]);
    setRtpDefinition(null);
    setRtpNotice(null);
  }

  async function start() {
    if (!reviewed || !approved) return;
    setStarting(true);
    setError(null);
    try {
      const accepted = reviewed.code === "business-rules"
        ? await api.startBusinessRule({ ...reviewed.payload, ...(planningTaskId ? { planning_task_id: planningTaskId } : {}) }, csrfToken)
        : await api.startDataMap({ ...reviewed.payload, ...(planningTaskId ? { planning_task_id: planningTaskId } : {}) }, csrfToken);
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
      <span className={`risk-badge risk-badge--${operation.risk_level.toLowerCase().replaceAll(" ", "-")}`}>{operation.risk_level}</span>
    </header>

    <ol className="runner-steps" aria-label="Operation progress">
      {[["SETUP", "Prepare"], ["REVIEW", "Review"], ["RUNNING", "Run"], ["RESULT", "Result"]].map(([value, label], index) => <li className={runnerStepClass(value as RunnerStep, step)} key={value}><span>{stepAfter(value as RunnerStep, step) ? <Icon name="check" /> : index + 1}</span><div><strong>{label}</strong><small>{runnerStepDescription(value as RunnerStep)}</small></div></li>)}
    </ol>

    {(error || monitorError) && <FeedbackBanner tone="error" title="Action required" message={error || monitorError} />}
    {rtpNotice && step === "SETUP" && <FeedbackBanner tone={rtpDefinition ? "success" : "info"} title={rtpDefinition ? "Runtime prompts loaded" : "Manual RTP fallback"} message={rtpNotice} />}
    {handoffLoaded && step === "SETUP" && <FeedbackBanner tone="success" title="Assistant preparation applied" message="The verified Business Rule and runtime prompt choices are already filled in. Review them before continuing; nothing has run in Oracle yet." />}

    {step === "SETUP" && <SetupStep code={code} artifacts={artifacts} target={target} clearTarget={clearTarget} primaryPairs={primaryPairs} exclusionPairs={exclusionPairs} loading={loadingCatalog} loadingRtp={loadingRtp} importingRtp={importingRtp} loadingRtpRegistry={loadingRtpRegistry} canManageCatalog={canManageCatalog} rtpDefinition={rtpDefinition} rtpRegistry={rtpRegistry} onTarget={selectTarget} onClearTarget={setClearTarget} onAdd={addPair} onUpdate={updatePair} onRemove={removePair} onImportRtp={importRtpRegistry} onRefreshRtpRegistry={refreshRtpRegistryStatus} onReview={review} onCancel={onBack} />}
    {step === "REVIEW" && reviewed && <ReviewStep reviewed={reviewed} approved={approved} starting={starting} onApproved={setApproved} onBack={() => { setError(null); setStep("SETUP"); }} onStart={start} />}
    {step === "RUNNING" && <ExecutionStep execution={execution} operation={operation} />}
    {step === "RESULT" && execution && <ResultStep execution={execution} onAgain={runAgain} onBack={onBack} />}
  </section>;
}

function SetupStep({ code, artifacts, target, clearTarget, primaryPairs, exclusionPairs, loading, loadingRtp, importingRtp, loadingRtpRegistry, canManageCatalog, rtpDefinition, rtpRegistry, onTarget, onClearTarget, onAdd, onUpdate, onRemove, onImportRtp, onRefreshRtpRegistry, onReview, onCancel }: {
  code: RunnerCode;
  artifacts: string[];
  target: string;
  clearTarget: boolean;
  primaryPairs: PairInput[];
  exclusionPairs: PairInput[];
  loading: boolean;
  loadingRtp: boolean;
  importingRtp: boolean;
  loadingRtpRegistry: boolean;
  canManageCatalog: boolean;
  rtpDefinition: BusinessRuleRTPDefinition | null;
  rtpRegistry: BusinessRuleRTPRegistryStatus | null;
  onTarget: (value: string) => void;
  onClearTarget: (value: boolean) => void;
  onAdd: (type: "primary" | "exclusion") => void;
  onUpdate: (type: "primary" | "exclusion", id: number, key: "name" | "value", value: string) => void;
  onRemove: (type: "primary" | "exclusion", id: number) => void;
  onImportRtp: (file: File) => Promise<void>;
  onRefreshRtpRegistry: () => Promise<void>;
  onReview: (event: FormEvent) => void;
  onCancel: () => void;
}) {
  const isRule = code === "business-rules";
  return <div className="runner-layout"><form className="panel runner-form" onSubmit={onReview}>
    <div className="panel-heading"><span className="eyebrow">Step 1</span><h2>Prepare the operation</h2><p>Choose a live Oracle artifact and supply only the run-time inputs this execution requires.</p></div>
    <label className="runner-field"><span>{isRule ? "Business Rule" : "Data Map"} *</span><select value={target} disabled={loading || artifacts.length === 0} onChange={(event) => onTarget(event.target.value)} required><option value="">{loading ? "Loading from Oracle..." : `Select a ${isRule ? "Business Rule" : "Data Map"}`}</option>{artifacts.map((name) => <option value={name} key={name}>{name}</option>)}</select><small>{loading ? "Checking the connected Planning application." : `${artifacts.length} live artifact${artifacts.length === 1 ? "" : "s"} available.`}</small></label>
    {!isRule && <label className={`runner-toggle${clearTarget ? " is-selected" : ""}`}><input type="checkbox" checked={clearTarget} onChange={(event) => onClearTarget(event.target.checked)} /><span><strong>Clear the target region before copying data</strong><small>Enable only when existing data in the Data Map target region should be replaced.</small></span></label>}
    {isRule && <RTPRegistryPanel status={rtpRegistry} loading={loadingRtpRegistry} importing={importingRtp} canManage={canManageCatalog} onImport={onImportRtp} onRefresh={onRefreshRtpRegistry} />}
    {isRule && loadingRtp && <div className="runner-pair-empty"><span className="spinner spinner--dark" /><span><strong>Loading runtime prompts</strong><small>Checking the synchronized Calc Manager definition.</small></span></div>}
    {isRule && !loadingRtp && rtpDefinition && <RegisteredPromptEditor definition={rtpDefinition} pairs={primaryPairs} onUpdate={onUpdate} />}
    {(!isRule || (!loadingRtp && !rtpDefinition)) && <PairEditor title={isRule ? "Runtime prompts" : "Member overrides"} description={isRule ? "No synchronized definition is available. Enter exact RTP names, or leave empty to use Calculation Manager defaults." : "Optionally narrow the Data Map source POV for this run."} type="primary" pairs={primaryPairs} nameLabel={isRule ? "Prompt name" : "Dimension"} valueLabel={isRule ? "Member or value" : "Member selection"} onAdd={onAdd} onUpdate={onUpdate} onRemove={onRemove} />}
    {!isRule && <PairEditor title="Exclusion overrides" description="Optionally remove supported member selections from this run." type="exclusion" pairs={exclusionPairs} nameLabel="Dimension" valueLabel="Excluded selection" onAdd={onAdd} onUpdate={onUpdate} onRemove={onRemove} />}
    <footer className="runner-actions"><button type="button" className="button button--quiet" onClick={onCancel}>Cancel</button><button className="button button--primary" disabled={loading || loadingRtp || artifacts.length === 0}>Review execution <Icon name="arrow" /></button></footer>
  </form><RunnerGuidance isRule={isRule} /></div>;
}

function RTPRegistryPanel({ status, loading, importing, canManage, onImport, onRefresh }: { status: BusinessRuleRTPRegistryStatus | null; loading: boolean; importing: boolean; canManage: boolean; onImport: (file: File) => Promise<void>; onRefresh: () => Promise<void> }) {
  const attention = status?.health === "ATTENTION";
  const latest = status?.recent_syncs[0];
  return <section className="runner-pairs rtp-registry"><header><div><h3>Calc Manager RTP registry</h3><p>Review the definitions synchronized for this Oracle environment. Failed imports preserve the last valid registry.</p></div><div className="rtp-registry__actions"><button type="button" className="button button--quiet" disabled={loading || importing} onClick={() => void onRefresh()}>{loading ? "Refreshing..." : "Refresh status"}</button>{canManage && <label className={`button button--quiet${importing ? " is-disabled" : ""}`}>{importing ? "Importing..." : "Import XML / ZIP"}<input type="file" hidden disabled={importing} accept=".xml,.zip,application/xml,application/zip" onChange={(event) => { const file = event.target.files?.[0]; if (file) void onImport(file); event.currentTarget.value = ""; }} /></label>}</div></header>
    {loading && !status ? <div className="runner-pair-empty"><span className="spinner spinner--dark" /><span><strong>Checking registry health</strong><small>Comparing synchronized definitions with the live Oracle catalog.</small></span></div> : status ? <>
      <div className="rtp-registry__summary"><div><small>Registry health</small><strong className={attention ? "is-warning" : ""}>{friendlyStatus(status.health)}</strong></div><div><small>Synchronized rules</small><strong>{status.synchronized_rule_count}</strong></div><div><small>Registered prompts</small><strong>{status.synchronized_prompt_count}</strong></div><div><small>Live Oracle rules</small><strong>{status.live_rule_count ?? "Unavailable"}</strong></div></div>
      {status.definitions_not_in_live_catalog.length > 0 && <div className="runner-warning"><Icon name="alert" /><div><strong>Possible renamed or removed rules</strong><p>{status.definitions_not_in_live_catalog.join(", ")} no longer match the live Oracle catalog. Import the current Calc Manager export after confirming the change.</p></div></div>}
      {latest?.status === "FAILED" && <div className="runner-warning"><Icon name="alert" /><div><strong>The latest import failed; last-known-good definitions remain active</strong><p>{latest.error_summary || "Review the source package and try again."}</p></div></div>}
      <details className="rtp-registry__details"><summary>View synchronized definitions ({status.definitions.length})</summary>{status.definitions.length ? <div className="rtp-registry__table-wrap"><table><thead><tr><th>Business Rule</th><th>Cube</th><th>Prompts</th><th>Source</th><th>Oracle status</th></tr></thead><tbody>{status.definitions.map((item) => <tr key={item.rule_name}><td><strong>{item.rule_name}</strong><small>Updated {formatDateTime(item.synchronized_at)}</small></td><td>{item.cube_name || "Not specified"}</td><td>{item.prompt_count}<small>{item.required_prompt_count} required</small></td><td>{item.source_name}</td><td><span className={`rtp-registry__state is-${item.live_status.toLowerCase().replaceAll("_", "-")}`}>{friendlyStatus(item.live_status)}</span></td></tr>)}</tbody></table></div> : <div className="runner-pair-empty"><Icon name="settings" /><span><strong>No synchronized definitions</strong><small>Import a supported Calc Manager XML or LCM ZIP package.</small></span></div>}</details>
      <details className="rtp-registry__details"><summary>Recent synchronization attempts ({status.recent_syncs.length})</summary>{status.recent_syncs.length ? <ul className="rtp-registry__history">{status.recent_syncs.map((sync) => <li key={sync.sync_run_id}><span className={`rtp-registry__state is-${sync.status.toLowerCase()}`}>{friendlyStatus(sync.status)}</span><div><strong>{sync.source_name}</strong><small>{formatDateTime(sync.started_at)} · {sync.rules_imported} rules · {sync.prompts_imported} prompts</small>{sync.warnings.length > 0 && <small>{sync.warnings.join(" ")}</small>}</div></li>)}</ul> : <div className="runner-pair-empty"><Icon name="settings" /><span><strong>No synchronization history</strong><small>The first valid import will appear here.</small></span></div>}</details>
      {status.unsynchronized_live_rules.length > 0 && <details className="rtp-registry__details"><summary>Live rules without synchronized RTP definitions ({status.unsynchronized_live_rules.length})</summary><p className="rtp-registry__note">This is informational: many Business Rules do not use RTPs. Import definitions only for rules that require governed prompt discovery.</p><div className="rtp-registry__chips">{status.unsynchronized_live_rules.map((name) => <span key={name}>{name}</span>)}</div></details>}
    </> : <div className="runner-pair-empty"><Icon name="settings" /><span><strong>Registry status unavailable</strong><small>Refresh the status to inspect synchronized definitions.</small></span></div>}
  </section>;
}

function RegisteredPromptEditor({ definition, pairs, onUpdate }: { definition: BusinessRuleRTPDefinition; pairs: PairInput[]; onUpdate: (type: "primary" | "exclusion", id: number, key: "name" | "value", value: string) => void }) {
  const pairByName = new Map(pairs.map((pair) => [pair.name.toLowerCase(), pair]));
  return <section className="runner-pairs"><header><div><h3>Registered runtime prompts</h3><p>{definition.prompts.length} prompt{definition.prompts.length === 1 ? "" : "s"} loaded from {definition.source_name}. Leave an optional value empty to use Oracle's configured default.</p></div><span className="risk-badge risk-badge--read-only">Synchronized</span></header>
    {definition.prompts.length ? <div className="runner-pair-list">{definition.prompts.map((prompt) => {
      const pair = pairByName.get(prompt.name.toLowerCase());
      if (!pair) return null;
      const scope = prompt.scope_name ? `${prompt.scope_type}: ${prompt.scope_name}` : prompt.scope_type;
      const details = [prompt.value_type, prompt.dimension, scope].filter(Boolean).join(" · ");
      return <div className="runner-pair" key={prompt.name}><label><span>{prompt.label}{prompt.required && !prompt.has_default ? " *" : ""}</span><input value={prompt.name} readOnly aria-label={`${prompt.label} prompt name`} /><small>{details}</small></label><label><span>Value</span><input value={pair.value} required={prompt.required && !prompt.has_default} placeholder={prompt.has_default ? `Oracle default: ${prompt.default_value}` : "Enter a value"} onChange={(event) => onUpdate("primary", pair.id, "value", event.target.value)} /><small>{prompt.allow_multiple ? "Multiple values are supported; use Oracle's accepted member syntax." : "Enter one exact member or value."}{prompt.limit_value ? ` Limit: ${prompt.limit_value}` : ""}</small></label><span aria-hidden="true" /></div>;
    })}</div> : <div className="runner-pair-empty"><Icon name="check" /><span><strong>This rule has no RTPs</strong><small>It can run without runtime prompt values.</small></span></div>}
  </section>;
}

function PairEditor({ title, description, type, pairs, nameLabel, valueLabel, onAdd, onUpdate, onRemove }: {
  title: string;
  description: string;
  type: "primary" | "exclusion";
  pairs: PairInput[];
  nameLabel: string;
  valueLabel: string;
  onAdd: (type: "primary" | "exclusion") => void;
  onUpdate: (type: "primary" | "exclusion", id: number, key: "name" | "value", value: string) => void;
  onRemove: (type: "primary" | "exclusion", id: number) => void;
}) {
  return <section className="runner-pairs"><header><div><h3>{title}</h3><p>{description}</p></div><button className="button button--quiet" type="button" onClick={() => onAdd(type)}>Add {type === "exclusion" ? "exclusion" : "input"}</button></header>
    {pairs.length ? <div className="runner-pair-list">{pairs.map((pair) => <div className="runner-pair" key={pair.id}><label><span>{nameLabel}</span><input value={pair.name} onChange={(event) => onUpdate(type, pair.id, "name", event.target.value)} /></label><label><span>{valueLabel}</span><input value={pair.value} onChange={(event) => onUpdate(type, pair.id, "value", event.target.value)} /></label><button type="button" aria-label={`Remove ${title.toLowerCase()} row`} onClick={() => onRemove(type, pair.id)}><Icon name="close" /></button></div>)}</div> : <div className="runner-pair-empty"><Icon name="settings" /><span><strong>No optional inputs added</strong><small>Oracle's configured values will be used.</small></span></div>}
  </section>;
}

function RunnerGuidance({ isRule }: { isRule: boolean }) {
  const items = isRule
    ? [["Exact prompt names", "Runtime prompt names must match Calculation Manager, including case."], ["Valid members", "Enter members or values accepted by the deployed rule."], ["Safe defaults", "Leave prompts empty when the rule's configured defaults are appropriate."]]
    : [["Oracle owns the map", "Source, target, mappings, and default POV remain configured in Planning."], ["Overrides are optional", "Use overrides only to narrow this specific execution."], ["Clear requires approval", "Target clearing is highlighted again before execution."]];
  return <aside className="panel runner-guidance"><span className="eyebrow">Before you run</span><h2>{isRule ? "Runtime prompt guidance" : "Data movement boundaries"}</h2><ol>{items.map(([title, detail], index) => <li key={title}><span>{index + 1}</span><div><strong>{title}</strong><p>{detail}</p></div></li>)}</ol></aside>;
}

function ReviewStep({ reviewed, approved, starting, onApproved, onBack, onStart }: { reviewed: ReviewedRun; approved: boolean; starting: boolean; onApproved: (value: boolean) => void; onBack: () => void; onStart: () => Promise<void> }) {
  const isRule = reviewed.code === "business-rules";
  const target = isRule ? reviewed.payload.rule_name : reviewed.payload.data_map_name;
  const primary = isRule ? reviewed.payload.runtime_prompts : reviewed.payload.member_overrides;
  return <section className="panel runner-review"><div className="panel-heading"><span className="eyebrow">Step 2</span><h2>Review before execution</h2><p>Nothing has been sent to Oracle yet. Confirm the exact artifact and run-time scope.</p></div>
    <div className="review-target"><span className="service-card__icon"><Icon name={isRule ? "settings" : "automation"} /></span><div><small>{isRule ? "Business Rule" : "Data Map"}</small><strong>{target}</strong></div></div>
    <dl className="review-summary"><div><dt>{isRule ? "Runtime prompts" : "Member overrides"}</dt><dd>{Object.keys(primary).length || "Use Oracle defaults"}</dd></div>{!isRule && <><div><dt>Exclusions</dt><dd>{Object.keys(reviewed.payload.exclusion_overrides).length || "None"}</dd></div><div><dt>Clear target</dt><dd className={reviewed.payload.clear_target ? "is-warning" : ""}>{reviewed.payload.clear_target ? "Yes" : "No"}</dd></div></>}</dl>
    {Object.keys(primary).length > 0 && <ReviewPairs title={isRule ? "Prompt values" : "Member overrides"} values={primary} />}
    {!isRule && Object.keys(reviewed.payload.exclusion_overrides).length > 0 && <ReviewPairs title="Exclusions" values={reviewed.payload.exclusion_overrides} />}
    {!isRule && reviewed.payload.clear_target && <div className="runner-warning"><Icon name="alert" /><div><strong>Target clearing is enabled</strong><p>Existing data inside the configured target region may be removed before data is copied.</p></div></div>}
    <label className="runner-approval"><input type="checkbox" checked={approved} onChange={(event) => onApproved(event.target.checked)} /><span><strong>I reviewed these inputs and approve this execution</strong><small>The action will be submitted to the connected Oracle Planning application.</small></span></label>
    <footer className="runner-actions"><button className="button button--quiet" disabled={starting} onClick={onBack}>Back to inputs</button><button className="button button--primary" disabled={!approved || starting} onClick={() => void onStart()}>{starting ? <><span className="spinner" /> Starting...</> : <>Start operation <Icon name="arrow" /></>}</button></footer>
  </section>;
}

function ReviewPairs({ title, values }: { title: string; values: Record<string, string> }) {
  return <section className="review-pairs"><h3>{title}</h3><dl>{Object.entries(values).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl></section>;
}

export function ExecutionStep({ execution, operation }: { execution: OperationExecution | null; operation: OperationSummary }) {
  return <section className="panel runner-execution"><div className="runner-status-mark"><span className="spinner spinner--dark" /></div><span className="eyebrow">Step 3</span><h2>{execution?.status === "QUEUED" ? "Waiting to start" : "Operation in progress"}</h2><p>{operation.display_name} is running in Oracle Planning. You can leave this page; the execution is retained in Jobs &amp; Activity.</p><div className="runner-progress"><span style={{ width: `${execution?.total_steps ? Math.max(10, execution.completed_steps / execution.total_steps * 100) : 12}%` }} /></div><small>{execution ? `${execution.completed_steps} of ${execution.total_steps || "planned"} steps completed` : "Creating the monitored execution..."}</small>{execution?.steps.length ? <ExecutionTimeline execution={execution} /> : null}</section>;
}

export function ResultStep({ execution, onAgain, onBack }: { execution: OperationExecution; onAgain: () => void; onBack: () => void }) {
  const success = execution.status === "SUCCESS";
  return <section className={`panel runner-result runner-result--${success ? "success" : "failed"}`}><span className="runner-result-icon"><Icon name={success ? "check" : "alert"} /></span><span className="eyebrow">Step 4</span><h2>{success ? "Operation completed successfully" : "Operation failed"}</h2><p>{success ? "Oracle completed every required step. The retained evidence is available below and in Jobs & Activity." : execution.error_message || "Oracle did not complete the operation. Review the failed step before trying again."}</p><div className="result-identity"><span><small>Execution</small><strong>{execution.execution_id.slice(0, 8)}</strong></span><span><small>Steps completed</small><strong>{execution.completed_steps} of {execution.total_steps}</strong></span><span><small>Started by</small><strong>{execution.initiated_by || "Current user"}</strong></span><span><small>Oracle executor</small><strong>{execution.executed_by || "Not recorded"}</strong></span></div>{execution.record_statistics && <OracleLoadStatistics statistics={execution.record_statistics} />}{execution.steps.length ? <ExecutionTimeline execution={execution} /> : null}{execution.artifacts.length ? <section className="result-artifacts"><h3>Generated files</h3>{execution.artifacts.map((artifact) => <a className="result-artifact" href={artifact.url} key={artifact.url}><span><Icon name="reports" /></span><div><strong>{artifact.name}</strong><small>Download generated output</small></div><Icon name="arrow" /></a>)}</section> : null}<footer className="runner-actions"><button className="button button--quiet" onClick={onBack}>Return to Operations</button>{execution.log_url && <a className="button button--quiet" href={execution.log_url}>Download log</a>}<a className="button button--quiet" href="#jobs">View Jobs &amp; Activity</a><button className="button button--primary" onClick={onAgain}>Run again</button></footer></section>;
}

function ExecutionTimeline({ execution }: { execution: OperationExecution }) {
  return <ol className="runner-timeline">{execution.steps.map((step) => <li className={`is-${step.status.toLowerCase()}`} key={`${step.sequence}-${step.name}`}><span>{step.status === "SUCCESS" ? <Icon name="check" /> : step.status === "FAILED" ? <Icon name="alert" /> : step.sequence}</span><div><strong>{step.name}</strong><small>{friendlyStatus(step.status)}</small>{step.error_message && <p>{step.error_message}</p>}</div></li>)}</ol>;
}

function pairsToRecord(pairs: PairInput[], label: string) {
  const result: Record<string, string> = {};
  const seen = new Set<string>();
  for (const pair of pairs) {
    const name = pair.name.trim();
    const value = pair.value.trim();
    if (!name && !value) continue;
    if (!name || !value) throw new Error(`${label} requires both a name and value.`);
    const normalized = name.toLowerCase();
    if (seen.has(normalized)) throw new Error(`${label} '${name}' was entered more than once.`);
    seen.add(normalized);
    result[name] = value;
  }
  return result;
}

function registeredPromptsToRecord(
  pairs: PairInput[],
  definition: BusinessRuleRTPDefinition
) {
  const values = new Map(
    pairs.map((pair) => [pair.name.toLowerCase(), pair.value.trim()])
  );
  const result: Record<string, string> = {};
  for (const prompt of definition.prompts) {
    const value = values.get(prompt.name.toLowerCase()) || "";
    if (!value) {
      if (prompt.required && !prompt.has_default) {
        throw new Error(`Runtime prompt '${prompt.label}' requires a value.`);
      }
      continue;
    }
    result[prompt.name] = value;
  }
  return result;
}

const STEP_ORDER: RunnerStep[] = ["SETUP", "REVIEW", "RUNNING", "RESULT"];
function stepAfter(candidate: RunnerStep, current: RunnerStep) { return STEP_ORDER.indexOf(candidate) < STEP_ORDER.indexOf(current); }
function runnerStepClass(candidate: RunnerStep, current: RunnerStep) { return candidate === current ? "is-current" : stepAfter(candidate, current) ? "is-complete" : ""; }
function runnerStepDescription(step: RunnerStep) { return ({ SETUP: "Select inputs", REVIEW: "Confirm scope", RUNNING: "Monitor Oracle", RESULT: "Review outcome" })[step]; }
function friendlyStatus(value: string) { return value.toLowerCase().replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase()); }
function formatDateTime(value: string) { const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString(); }
function errorMessage(reason: unknown) { return reason instanceof Error ? reason.message : "The operation could not be completed."; }
function stringRecord(value: unknown): Record<string, string> { return value && typeof value === "object" && !Array.isArray(value) ? Object.fromEntries(Object.entries(value).map(([name, item]) => [name, String(item)])) : {}; }

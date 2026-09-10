import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import type { OracleCatalogResponse, OperationSummary, OperationsResponse } from "../api/types";
import { FeedbackBanner } from "./Feedback";
import { Icon } from "./Icon";
import { OperationRunner } from "./OperationRunner";
import { DataIntegrationRunner } from "./DataIntegrationRunner";
import { PipelineRunner } from "./PipelineRunner";
import { NativeDataImportRunner } from "./NativeDataImportRunner";
import { MetadataImportRunner } from "./MetadataImportRunner";
import { CubeRefreshRunner } from "./CubeRefreshRunner";
import { SubstitutionVariableRunner } from "./SubstitutionVariableRunner";
import { UserVariableRunner } from "./UserVariableRunner";

interface OperationsWorkspaceProps {
  data: OperationsResponse;
  csrfToken: string;
  canManageCatalog: boolean;
  currentUsername: string;
  canManageUsers: boolean;
}

export function OperationsWorkspace({ data, csrfToken, canManageCatalog, currentUsername, canManageUsers }: OperationsWorkspaceProps) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("ALL");
  const [catalog, setCatalog] = useState<OracleCatalogResponse | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogSyncing, setCatalogSyncing] = useState(false);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [catalogNotice, setCatalogNotice] = useState<string | null>(null);
  const [selectedOperation, setSelectedOperation] = useState<OperationSummary | null>(
    () => operationFromLocation(data.operations.filter((item) => item.code !== "report-generation"))
  );
  const planningTaskId = planningTaskIdFromLocation(selectedOperation?.code);
  const agentDraftId = agentDraftIdFromLocation(selectedOperation?.code);
  const closeRunner = () => {
    clearOperationLocation();
    setSelectedOperation(null);
  };
  const categories = useMemo(
    () => [...new Set(data.operations.filter((item) => item.code !== "report-generation").map((operation) => operation.category))].sort(),
    [data.operations]
  );
  const operations = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return data.operations.filter((operation) => {
      if (operation.code === "report-generation") return false;
      const matchesQuery = !needle || [
        operation.display_name,
        operation.description,
        operation.category
      ].some((value) => value.toLowerCase().includes(needle));
      return matchesQuery && (category === "ALL" || operation.category === category);
    });
  }, [category, data.operations, query]);

  useEffect(() => {
    let active = true;
    api.oracleCatalog()
      .then((response) => active && setCatalog(response))
      .catch((reason: unknown) => active && setCatalogError(errorMessage(reason)))
      .finally(() => active && setCatalogLoading(false));
    return () => { active = false; };
  }, []);

  async function synchronizeCatalog() {
    setCatalogSyncing(true);
    setCatalogError(null);
    setCatalogNotice(null);
    try {
      const result = await api.synchronizeOracleCatalog(csrfToken);
      setCatalog({
        status: "success",
        environment: {
          application_name: result.sync.application_name,
          deployment_mode: "Oracle EPM"
        },
        summary: {
          verified: result.sync.total_verified,
          attention: result.sync.total_hidden + result.sync.verification_errors,
          last_synchronized_at: result.sync.synchronized_at
        },
        artifacts: result.artifacts
      });
      setCatalogNotice(result.sync.message);
    } catch (reason) {
      setCatalogError(errorMessage(reason));
    } finally {
      setCatalogSyncing(false);
    }
  }

  if (selectedOperation) {
    if (selectedOperation.code === "data-integrations") {
      return <DataIntegrationRunner operation={selectedOperation} csrfToken={csrfToken} canManageCatalog={canManageCatalog} planningTaskId={planningTaskId} onBack={closeRunner} />;
    }
    if (selectedOperation.code === "pipelines") {
      return <PipelineRunner operation={selectedOperation} csrfToken={csrfToken} canManageCatalog={canManageCatalog} planningTaskId={planningTaskId} onBack={closeRunner} />;
    }
    if (selectedOperation.code === "data-import") {
      return <NativeDataImportRunner operation={selectedOperation} csrfToken={csrfToken} planningTaskId={planningTaskId} onBack={closeRunner} />;
    }
    if (selectedOperation.code === "metadata-import") {
      return <MetadataImportRunner operation={selectedOperation} csrfToken={csrfToken} planningTaskId={planningTaskId} onBack={closeRunner} />;
    }
    if (selectedOperation.code === "cube-refresh") {
      return <CubeRefreshRunner operation={selectedOperation} csrfToken={csrfToken} planningTaskId={planningTaskId} onBack={closeRunner} />;
    }
    if (selectedOperation.code === "substitution-variables") {
      return <SubstitutionVariableRunner operation={selectedOperation} csrfToken={csrfToken} planningTaskId={planningTaskId} onBack={closeRunner} />;
    }
    if (selectedOperation.code === "user-variables") {
      return <UserVariableRunner operation={selectedOperation} csrfToken={csrfToken} currentUsername={currentUsername} canManageUsers={canManageUsers} planningTaskId={planningTaskId} onBack={closeRunner} />;
    }
    return <OperationRunner operation={selectedOperation} csrfToken={csrfToken} canManageCatalog={canManageCatalog} planningTaskId={planningTaskId} agentDraftId={agentDraftId} onBack={closeRunner} />;
  }

  return <section className="operations-workspace">
    <header className="page-intro operations-page-intro">
      <div>
        <span className="eyebrow">Standalone automation</span>
        <h1>Operations</h1>
        <p>Run one Oracle EPM service independently when a complete Planning cycle or Pipeline is not required.</p>
      </div>
      <span className="operations-count"><Icon name="automation" /> {data.operations.filter((item) => item.code !== "report-generation").length} services available</span>
    </header>

    <aside className="operations-guidance">
      <span><Icon name="sparkle" /></span>
      <div>
        <strong>Choose the smallest action that completes your task</strong>
        <p>Each service guides you through inputs, validation, review, execution, and monitoring. Completed runs remain available in Jobs &amp; Activity.</p>
      </div>
    </aside>

    <CatalogHealth
      catalog={catalog}
      loading={catalogLoading}
      syncing={catalogSyncing}
      canManage={canManageCatalog}
      error={catalogError}
      notice={catalogNotice}
      onSync={() => void synchronizeCatalog()}
      onDismissError={() => setCatalogError(null)}
      onDismissNotice={() => setCatalogNotice(null)}
    />

    <section className="panel operations-catalog-panel">
      <div className="panel-heading operations-heading">
        <div><span className="eyebrow">Service catalog</span><h2>What do you want to run?</h2><p>Only services permitted for your platform role are shown.</p></div>
        <span className="result-count">{operations.length} shown</span>
      </div>
      <div className="operations-filters">
        <label className="search-field"><Icon name="search" /><span className="sr-only">Search services</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search by service or purpose" /></label>
        <label><span>Category</span><select value={category} onChange={(event) => setCategory(event.target.value)}><option value="ALL">All categories</option>{categories.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
      </div>

      {operations.length
        ? <div className="operations-grid">{operations.map((operation) => <OperationCard operation={operation} onOpen={setSelectedOperation} key={operation.code} />)}</div>
        : <div className="work-empty"><span><Icon name="search" /></span><h2>No services match your search</h2><p>Clear the search or choose another category.</p></div>}
    </section>
  </section>;
}

function CatalogHealth({ catalog, loading, syncing, canManage, error, notice, onSync, onDismissError, onDismissNotice }: {
  catalog: OracleCatalogResponse | null;
  loading: boolean;
  syncing: boolean;
  canManage: boolean;
  error: string | null;
  notice: string | null;
  onSync: () => void;
  onDismissError: () => void;
  onDismissNotice: () => void;
}) {
  const currentTypes = new Set(
    catalog?.artifacts.filter((item) => item.is_verified).map((item) => item.artifact_type) ?? []
  ).size;
  return <section className="panel oracle-catalog-health" aria-busy={loading || syncing}>
    <div className="oracle-catalog-health__copy">
      <span className="eyebrow">Connected Oracle catalog</span>
      <h2>{loading ? "Reading synchronized catalog..." : catalog?.environment.application_name || "Oracle catalog not synchronized"}</h2>
      <p>{catalog?.summary.last_synchronized_at
        ? `Last synchronized ${formatTimestamp(catalog.summary.last_synchronized_at)}. Selectors use only artifacts verified for this application.`
        : "Synchronize once to discover current Oracle jobs and cubes and verify registered Pipelines and Data Integrations."}</p>
    </div>
    <div className="oracle-catalog-health__metrics" aria-label="Oracle catalog status">
      <span><strong>{catalog?.summary.verified ?? 0}</strong><small>current artifacts</small></span>
      <span><strong>{currentTypes}</strong><small>artifact types</small></span>
      <span className={(catalog?.summary.attention ?? 0) > 0 ? "has-attention" : ""}><strong>{catalog?.summary.attention ?? 0}</strong><small>need attention</small></span>
    </div>
    {canManage
      ? <button type="button" className="button button--quiet" disabled={loading || syncing} onClick={onSync}><Icon name="refresh" /> {syncing ? "Synchronizing..." : "Sync from Oracle"}</button>
      : <span className="catalog-readonly"><Icon name="check" /> Managed by an administrator</span>}
    {error && <FeedbackBanner tone="error" title="Oracle catalog unavailable" message={error} onDismiss={onDismissError} />}
    {notice && <FeedbackBanner tone="success" title="Catalog synchronized" message={notice} onDismiss={onDismissNotice} />}
  </section>;
}

function OperationCard({ operation, onOpen }: { operation: OperationSummary; onOpen: (operation: OperationSummary) => void }) {
  const hasModernRunner = ["business-rules", "data-maps", "data-integrations", "pipelines", "data-import", "metadata-import", "cube-refresh", "substitution-variables", "user-variables"].includes(operation.code);
  return <article className="service-card">
    <header>
      <span className="service-card__icon"><Icon name={operationIcon(operation.code)} /></span>
      <span className={`risk-badge risk-badge--${riskClass(operation.risk_level)}`}>{operation.risk_level}</span>
    </header>
    <div className="service-card__body">
      <span className="service-category">{operation.category}</span>
      <h3>{operation.display_name}</h3>
      <p>{operation.description}</p>
    </div>
    <footer>
      <span><Icon name="check" /> Governed and monitored</span>
      {hasModernRunner
        ? <button className="button button--primary" onClick={() => onOpen(operation)}>Open service <Icon name="arrow" /></button>
        : <a className="button button--primary" href={operation.route}>Open service <Icon name="arrow" /></a>}
    </footer>
  </article>;
}

function operationFromLocation(operations: OperationSummary[]) {
  const requested = new URLSearchParams(window.location.search).get("operation");
  return operations.find((operation) => operation.code === requested) ?? null;
}

function planningTaskIdFromLocation(selectedOperationCode?: string) {
  const query = new URLSearchParams(window.location.search);
  if (!selectedOperationCode || query.get("operation") !== selectedOperationCode) return null;
  const value = Number(query.get("planning_task_id"));
  return Number.isInteger(value) && value > 0 ? value : null;
}

function agentDraftIdFromLocation(selectedOperationCode?: string) {
  const query = new URLSearchParams(window.location.search);
  if (!selectedOperationCode || query.get("operation") !== selectedOperationCode) return null;
  return query.get("agent_draft")?.trim() || null;
}

function clearOperationLocation() {
  const url = new URL(window.location.href);
  url.searchParams.delete("operation");
  url.searchParams.delete("planning_task_id");
  url.searchParams.delete("agent_draft");
  window.history.replaceState({}, "", `${url.pathname}${url.search}#operations`);
}

function riskClass(value: string) {
  return value.toLowerCase().replaceAll(" ", "-");
}

function operationIcon(code: string): "automation" | "reports" | "refresh" | "settings" | "data" | "tasks" {
  const icons = {
    "report-generation": "reports",
    "cube-refresh": "refresh",
    "substitution-variables": "settings",
    "user-variables": "settings",
    "data-import": "data",
    "metadata-import": "tasks",
    pipelines: "automation",
    "data-integrations": "data",
    "business-rules": "settings",
    "data-maps": "automation"
  } as const;
  return icons[code as keyof typeof icons] ?? "automation";
}

function formatTimestamp(value: string) {
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.getTime()) ? value : timestamp.toLocaleString();
}

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : "The Oracle catalog could not be loaded.";
}

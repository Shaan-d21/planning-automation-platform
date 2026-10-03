import { useEffect, useMemo, useState, type FormEvent } from "react";

import { api } from "../api/client";
import type {
  FCCSDimensionsResponse,
  FCCSJobDefinition,
  FCCSJournal,
  FCCSJournalDetailResponse,
  FCCSJournalsResponse,
  FCCSOverviewResponse
} from "../api/types";
import { Icon } from "./Icon";
import { FeedbackBanner, WorkspaceLoading } from "./Feedback";

export type FCCSReadView =
  | "fccs-overview"
  | "fccs-dimensions"
  | "fccs-jobs"
  | "fccs-journals";

export function FCCSReadWorkspace({ view }: { view: FCCSReadView }) {
  if (view === "fccs-overview") return <FCCSOverview />;
  if (view === "fccs-dimensions") return <FCCSDimensions />;
  if (view === "fccs-jobs") return <FCCSJobs />;
  return <FCCSJournals />;
}

function FCCSOverview() {
  const [data, setData] = useState<FCCSOverviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.fccsOverview().then(setData).catch((reason: unknown) => setError(errorMessage(reason)));
  }, []);

  if (!data && !error) return <WorkspaceLoading label="Close overview" message="Verifying the FCCS application and retrieving live read-only metadata…" />;

  return <section className="fccs-workspace">
    <FCCSHeading title="Financial Consolidation & Close" description="A live, read-only view of the verified Oracle FCCS application. No consolidation, journal, or data changes can be started here." />
    {error && <FeedbackBanner tone="error" title="FCCS overview unavailable" message={error} />}
    {data && <>
      <div className="fccs-metrics" aria-label="FCCS application summary">
        <Metric icon="check" label="Connection" value={data.connected ? "Verified" : "Unavailable"} />
        <Metric icon="data" label="Cubes" value={data.plan_types.length.toLocaleString()} />
        <Metric icon="automation" label="Saved jobs" value={data.job_definitions.length.toLocaleString()} />
        <Metric icon="settings" label="Mode" value="Read only" />
      </div>
      <section className="panel fccs-panel">
        <div className="panel-heading"><div><span className="eyebrow">Verified application</span><h2>{data.application_name}</h2><p>Product identity comes from Oracle application metadata, not from the application name.</p></div><span className="fccs-read-badge"><Icon name="check" /> Read-only foundation</span></div>
        <dl className="fccs-identity">
          <div><dt>Oracle product type</dt><dd>{data.product_type || "Not returned"}</dd></div>
          <div><dt>Application type</dt><dd>{data.application_type || "Not returned"}</dd></div>
          <div><dt>Available dimensions</dt><dd>{data.plan_types.reduce((total, item) => total + item.dimensions.length, 0).toLocaleString()}</dd></div>
          <div><dt>Write operations</dt><dd>Disabled in this release phase</dd></div>
        </dl>
      </section>
    </>}
  </section>;
}

function FCCSDimensions() {
  const [data, setData] = useState<FCCSDimensionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    api.fccsDimensions().then(setData).catch((reason: unknown) => setError(errorMessage(reason)));
  }, []);

  const cubes = useMemo(() => (data?.plan_types ?? []).map((cube) => ({
    ...cube,
    dimensions: cube.dimensions.filter((dimension) => dimension.name.toLowerCase().includes(query.trim().toLowerCase()))
  })).filter((cube) => !query.trim() || cube.name.toLowerCase().includes(query.trim().toLowerCase()) || cube.dimensions.length), [data, query]);

  if (!data && !error) return <WorkspaceLoading label="FCCS dimensions" message="Reading live cube and dimension metadata from Oracle…" />;

  return <section className="fccs-workspace">
    <FCCSHeading title="Dimensions" description="Inspect the cubes and dimensions Oracle exposes for the active FCCS application." />
    {error && <FeedbackBanner tone="error" title="Dimensions unavailable" message={error} />}
    {data && <section className="panel fccs-panel">
      <div className="fccs-toolbar"><label className="search-field"><Icon name="search" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search cube or dimension" /></label><span>{cubes.reduce((total, item) => total + item.dimensions.length, 0)} dimensions shown</span></div>
      <div className="fccs-cube-grid">{cubes.map((cube) => <article className="fccs-cube" key={cube.cube_name}><header><span><Icon name="data" /></span><div><h2>{cube.name}</h2><p>{cube.cube_name}{cube.cube_type !== null ? ` · Type ${cube.cube_type}` : ""}</p></div><b>{cube.dimensions.length}</b></header>{cube.dimensions.length ? <ul>{cube.dimensions.map((dimension) => <li key={`${cube.cube_name}-${dimension.name}`}><strong>{dimension.name}</strong><span>{dimension.dimension_type || "Dimension"}</span></li>)}</ul> : <p className="fccs-empty-copy">No dimensions match this search.</p>}</article>)}</div>
      {!cubes.length && <EmptyState title="No dimensions match" message="Clear the search to see the live Oracle dimension catalog." />}
    </section>}
  </section>;
}

function FCCSJobs() {
  const [jobs, setJobs] = useState<FCCSJobDefinition[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [type, setType] = useState("ALL");

  useEffect(() => {
    api.fccsJobs().then((response) => setJobs(response.jobs)).catch((reason: unknown) => setError(errorMessage(reason)));
  }, []);

  const types = [...new Set((jobs ?? []).map((job) => job.job_type))].sort();
  const visibleJobs = (jobs ?? []).filter((job) => {
    const needle = query.trim().toLowerCase();
    return (!needle || `${job.job_name} ${job.job_type}`.toLowerCase().includes(needle)) && (type === "ALL" || job.job_type === type);
  });

  if (!jobs && !error) return <WorkspaceLoading label="Oracle jobs" message="Reading saved FCCS job definitions without starting them…" />;

  return <section className="fccs-workspace">
    <FCCSHeading title="Oracle Jobs" description="Review saved FCCS job definitions. This workspace cannot execute, cancel, or modify a job." />
    {error && <FeedbackBanner tone="error" title="Job definitions unavailable" message={error} />}
    {jobs && <section className="panel fccs-panel">
      <div className="fccs-toolbar fccs-toolbar--jobs"><label className="search-field"><Icon name="search" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search saved jobs" /></label><label><span>Job type</span><select value={type} onChange={(event) => setType(event.target.value)}><option value="ALL">All types</option>{types.map((item) => <option value={item} key={item}>{item}</option>)}</select></label><span>{visibleJobs.length} shown</span></div>
      {visibleJobs.length ? <div className="fccs-table-wrap"><table className="fccs-table"><thead><tr><th>Job name</th><th>Oracle job type</th><th>Availability</th></tr></thead><tbody>{visibleJobs.map((job) => <tr key={`${job.job_type}-${job.job_name}`}><td><strong>{job.job_name}</strong></td><td>{job.job_type}</td><td><span className="fccs-status">Read only</span></td></tr>)}</tbody></table></div> : <EmptyState title="No jobs match" message="Clear the filters to see the Oracle job definitions available to this account." />}
    </section>}
  </section>;
}

const emptyJournalFilters = { label: "", scenario: "", year: "", period: "", status: "", consolidation: "" };

function FCCSJournals() {
  const [data, setData] = useState<FCCSJournalsResponse | null>(null);
  const [filters, setFilters] = useState(emptyJournalFilters);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<FCCSJournalDetailResponse | null>(null);

  async function load(activeFilters: Record<string, string> = filters) {
    setBusy(true);
    setError(null);
    try {
      setData(await api.fccsJournals(activeFilters));
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { void load(emptyJournalFilters); }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    await load();
  }

  async function inspect(journal: FCCSJournal) {
    if (!journal.scenario || !journal.year || !journal.period) {
      setError("Oracle did not return the scenario, year, and period required to open this journal.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setDetail(await api.fccsJournalDetail(journal.label, {
        scenario: journal.scenario,
        year: journal.year,
        period: journal.period,
        consolidation: journal.consolidation
      }));
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  if (!data && busy && !error) return <WorkspaceLoading label="FCCS journals" message="Retrieving consolidation journals from Oracle…" />;

  return <section className="fccs-workspace">
    <FCCSHeading title="Consolidation Journals" description="Search and inspect journals through Oracle's supported read API. Creating, posting, unposting, and deleting journals remain disabled." />
    {error && <FeedbackBanner tone="error" title="Journal review unavailable" message={error} />}
    <section className="panel fccs-panel">
      <form className="fccs-journal-filters" onSubmit={submit}>{Object.entries(filters).map(([key, value]) => <label key={key}><span>{friendlyLabel(key)}</span><input value={value} onChange={(event) => setFilters((current) => ({ ...current, [key]: event.target.value }))} placeholder={key === "label" ? "Journal label" : friendlyLabel(key)} /></label>)}<button className="button button--primary" disabled={busy} type="submit">{busy ? <span className="spinner" /> : <Icon name="search" />} Search journals</button></form>
      {data?.journals.length ? <div className="fccs-table-wrap"><table className="fccs-table"><thead><tr><th>Journal</th><th>POV</th><th>Status</th><th>Group</th><th><span className="sr-only">Action</span></th></tr></thead><tbody>{data.journals.map((journal) => <tr key={`${journal.label}-${journal.scenario}-${journal.year}-${journal.period}`}><td><strong>{journal.label}</strong><small>{journal.description || journal.journal_type || "Consolidation journal"}</small></td><td>{[journal.scenario, journal.year, journal.period, journal.consolidation].filter(Boolean).join(" · ") || "Not returned"}</td><td><span className="fccs-status">{journal.status || "Unknown"}</span></td><td>{journal.group || "—"}</td><td><button className="button button--quiet" type="button" disabled={busy} onClick={() => void inspect(journal)}>View details</button></td></tr>)}</tbody></table></div> : data && <EmptyState title="No journals found" message="Adjust the filters or confirm that this Oracle account can review consolidation journals." />}
    </section>
    {detail && <JournalDialog detail={detail} onClose={() => setDetail(null)} />}
  </section>;
}

function JournalDialog({ detail, onClose }: { detail: FCCSJournalDetailResponse; onClose: () => void }) {
  const columns = [...new Set(detail.line_items.flatMap((item) => Object.keys(item)))];
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="job-dialog fccs-journal-dialog" role="dialog" aria-modal="true" aria-labelledby="fccs-journal-title"><header><div><span className="eyebrow">Consolidation journal</span><h2 id="fccs-journal-title">{detail.journal.label}</h2><div className="job-dialog-meta"><span>{detail.journal.status || "Unknown status"}</span><span>{[detail.journal.scenario, detail.journal.year, detail.journal.period, detail.journal.consolidation].filter(Boolean).join(" · ")}</span></div></div><button aria-label="Close journal details" onClick={onClose}><Icon name="close" /></button></header><div className="job-dialog-body">{detail.journal.description && <p>{detail.journal.description}</p>}{detail.line_items.length ? <div className="fccs-table-wrap"><table className="fccs-table"><thead><tr>{columns.map((column) => <th key={column}>{friendlyLabel(column)}</th>)}</tr></thead><tbody>{detail.line_items.map((line, index) => <tr key={index}>{columns.map((column) => <td key={column}>{formatValue(line[column])}</td>)}</tr>)}</tbody></table></div> : <EmptyState title="No line items returned" message="Oracle returned the journal header without journal line items." />}</div></section></div>;
}

function FCCSHeading({ title, description }: { title: string; description: string }) {
  return <header className="page-intro fccs-intro"><div><span className="eyebrow">FCCS · Read only</span><h1>{title}</h1><p>{description}</p></div><span className="fccs-read-badge"><Icon name="check" /> Supported Oracle REST</span></header>;
}

function Metric({ icon, label, value }: { icon: "check" | "data" | "automation" | "settings"; label: string; value: string }) {
  return <article><span><Icon name={icon} /></span><div><strong>{value}</strong><small>{label}</small></div></article>;
}

function EmptyState({ title, message }: { title: string; message: string }) {
  return <div className="work-empty"><span><Icon name="search" /></span><h2>{title}</h2><p>{message}</p></div>;
}

function friendlyLabel(value: string) {
  return value.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/_/g, " ").replace(/^./, (letter) => letter.toUpperCase());
}

function formatValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : "The FCCS read request could not be completed.";
}

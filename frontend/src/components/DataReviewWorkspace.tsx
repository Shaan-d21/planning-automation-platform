import { useEffect, useMemo, useState, type FormEvent } from "react";

import { api } from "../api/client";
import type {
  DataReviewAxisInput,
  DataReviewCube,
  DataReviewDimension,
  DataReviewResult,
  DataReviewSliceInput,
  DataReviewTaskContextResponse
} from "../api/types";
import { Icon } from "./Icon";
import { FeedbackBanner } from "./Feedback";
import { MemberSelector } from "./MemberSelector";
import { DataValidationWorkbench } from "./DataValidationWorkbench";

type AxisName = "pov" | "rows" | "columns";

interface AxisEntry {
  id: number;
  dimension: string;
  members: string[];
}

interface SelectedCell {
  reference: string;
  value: unknown;
}

let nextEntryId = 1;
const AGENT_DATA_REVIEW_HANDOFF_KEY = "bisp-epm-agent-data-review-handoff";
const AGENT_DATA_REVIEW_CUBE_KEY = "bisp-epm-agent-data-review-cube";

interface DataReviewWorkspaceProps {
  csrfToken: string;
}

export function DataReviewWorkspace({ csrfToken }: DataReviewWorkspaceProps) {
  const planningTaskId = planningTaskIdFromLocation();
  const [cubes, setCubes] = useState<DataReviewCube[]>([]);
  const [cube, setCube] = useState("");
  const [dimensions, setDimensions] = useState<DataReviewDimension[]>([]);
  const [pov, setPov] = useState<AxisEntry[]>([]);
  const [rows, setRows] = useState<AxisEntry[]>([]);
  const [columns, setColumns] = useState<AxisEntry[]>([]);
  const [review, setReview] = useState<DataReviewResult | null>(null);
  const [reviewedSlice, setReviewedSlice] = useState<DataReviewSliceInput | null>(null);
  const [selectedCell, setSelectedCell] = useState<SelectedCell | null>(null);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [loadingCubes, setLoadingCubes] = useState(true);
  const [loadingDimensions, setLoadingDimensions] = useState(false);
  const [loadingGrid, setLoadingGrid] = useState(false);
  const [exportingGrid, setExportingGrid] = useState(false);
  const [layoutOpen, setLayoutOpen] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cubeDiscoveryError, setCubeDiscoveryError] = useState<string | null>(null);
  const [dimensionDiscoveryError, setDimensionDiscoveryError] = useState<string | null>(null);
  const [manualCube, setManualCube] = useState("");
  const [manualMetadata, setManualMetadata] = useState(false);
  const [restoredLayout, setRestoredLayout] = useState(false);
  const [taskContext, setTaskContext] = useState<DataReviewTaskContextResponse | null>(null);
  const [loadingTask, setLoadingTask] = useState(Boolean(planningTaskId));

  useEffect(() => {
    let active = true;
    const agentHandoff = planningTaskId ? null : consumeAgentDataReviewHandoff();
    const agentCube = planningTaskId ? null : consumeAgentDataReviewCube();
    setCubeDiscoveryError(null);
    api.dataReviewCubes()
      .then(async (response) => {
        if (!active) return;
        setCubes(response.cubes);
        if (agentHandoff) await chooseCube(agentHandoff.cube, agentHandoff);
        else if (agentCube) await chooseCube(agentCube);
        else if (response.cubes.length === 1 && !planningTaskId) await chooseCube(response.cubes[0].name);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setCubes([]);
        setCubeDiscoveryError(errorMessage(reason));
      })
      .finally(() => active && setLoadingCubes(false));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!planningTaskId) return;
    let active = true;
    api.dataReviewTaskContext(planningTaskId)
      .then(async (response) => {
        if (!active) return;
        setTaskContext(response);
        if (response.suggested_slice) {
          await chooseCube(response.suggested_slice.cube, response.suggested_slice);
        }
      })
      .catch((reason: unknown) => active && setError(errorMessage(reason)))
      .finally(() => active && setLoadingTask(false));
    return () => { active = false; };
  }, []);

  const assignedDimensions = useMemo(
    () => [...pov, ...rows, ...columns].map((entry) => entry.dimension).filter(Boolean),
    [columns, pov, rows]
  );
  const selectedLayout = [...pov, ...rows, ...columns];
  const layoutReady = Boolean(
    cube
    && rows.length
    && columns.length
    && selectedLayout.every((item) => item.dimension.trim() && item.members.length)
    && (manualMetadata || dimensions.every((dimension) => assignedDimensions.some((name) => name.toLowerCase() === dimension.name.toLowerCase())))
  );

  async function chooseCube(value: string, suggested?: DataReviewSliceInput) {
    const normalized = value.trim();
    setCube(normalized);
    setReview(null);
    setReviewedSlice(null);
    setSelectedCell(null);
    setError(null);
    setDimensionDiscoveryError(null);
    setManualMetadata(false);
    setRestoredLayout(false);
    if (!normalized) {
      setDimensions([]);
      setPov([]);
      setRows([]);
      setColumns([]);
      return;
    }
    setLoadingDimensions(true);
    try {
      const response = await api.dataReviewDimensions(normalized);
      if (!response.dimensions.length) {
        throw new Error(`Oracle returned no discoverable dimensions for cube '${normalized}'.`);
      }
      setDimensions(response.dimensions);
      if (suggested) applySuggestedSlice(suggested);
      else applySmartLayout(response.dimensions);
    } catch (reason) {
      setDimensions([]);
      setDimensionDiscoveryError(errorMessage(reason));
      const saved = suggested ?? loadSavedLayout(normalized);
      if (saved) {
        applySuggestedSlice(saved);
        setManualMetadata(true);
        setRestoredLayout(!suggested);
      } else {
        setRows([]);
        setColumns([]);
        setPov([]);
      }
    } finally {
      setLoadingDimensions(false);
    }
  }

  async function refreshCubes() {
    setLoadingCubes(true);
    setCubeDiscoveryError(null);
    try {
      const response = await api.dataReviewCubes();
      setCubes(response.cubes);
      const currentStillExists = response.cubes.some(
        (item) => item.name.toLowerCase() === cube.toLowerCase()
      );
      if (cube && !currentStillExists) await chooseCube("");
      if (!cube && response.cubes.length === 1) await chooseCube(response.cubes[0].name);
    } catch (reason) {
      setCubes([]);
      setCubeDiscoveryError(errorMessage(reason));
    } finally {
      setLoadingCubes(false);
    }
  }

  function applySuggestedSlice(selection: DataReviewSliceInput) {
    setPov(Object.entries(selection.pov).map(([dimension, member]) => entry(dimension, [member])));
    setRows(selection.rows.map((item) => entry(item.dimension, item.members)));
    setColumns(selection.columns.map((item) => entry(item.dimension, item.members)));
    setReview(null);
    setReviewedSlice(null);
    setSelectedCell(null);
    setError(null);
  }

  function applySmartLayout(available = dimensions) {
    if (!available.length) {
      setRows([entry("Account")]);
      setColumns([entry("Period")]);
      setPov([]);
      return;
    }
    const period = findDimension(available, "Period");
    const account = findDimension(available, "Account", period?.name);
    const columnDimension = period ?? available[0];
    const rowDimension = account ?? available.find((item) => item.name !== columnDimension.name) ?? available[0];
    const axisNames = new Set([columnDimension.name.toLowerCase(), rowDimension.name.toLowerCase()]);
    setColumns([entry(columnDimension.name)]);
    setRows([entry(rowDimension.name)]);
    setPov(available.filter((item) => !axisNames.has(item.name.toLowerCase())).map((item) => entry(item.name)));
    setReview(null);
    setSelectedCell(null);
    setError(null);
  }

  function updateAxis(axis: AxisName, id: number, update: Partial<AxisEntry>) {
    const setter = axis === "pov" ? setPov : axis === "rows" ? setRows : setColumns;
    setter((current) => current.map((item) => item.id === id ? { ...item, ...update } : item));
  }

  function addAxis(axis: AxisName) {
    const setter = axis === "pov" ? setPov : axis === "rows" ? setRows : setColumns;
    const firstAvailable = dimensions.find((dimension) => !assignedDimensions.some((name) => name.toLowerCase() === dimension.name.toLowerCase()));
    setter((current) => [...current, entry(firstAvailable?.name ?? "")]);
  }

  function removeAxis(axis: AxisName, id: number) {
    const setter = axis === "pov" ? setPov : axis === "rows" ? setRows : setColumns;
    setter((current) => current.filter((item) => item.id !== id));
  }

  function placeDimension(dimension: string, axis: AxisName) {
    const existing = [...pov, ...rows, ...columns].find(
      (item) => item.dimension.toLowerCase() === dimension.toLowerCase()
    ) ?? entry(dimension);
    const moved = {
      ...existing,
      dimension,
      members: axis === "pov" ? existing.members.slice(0, 1) : existing.members
    };
    setPov((current) => axis === "pov" ? [...current.filter((item) => item.dimension.toLowerCase() !== dimension.toLowerCase()), moved] : current.filter((item) => item.dimension.toLowerCase() !== dimension.toLowerCase()));
    setRows((current) => axis === "rows" ? [...current.filter((item) => item.dimension.toLowerCase() !== dimension.toLowerCase()), moved] : current.filter((item) => item.dimension.toLowerCase() !== dimension.toLowerCase()));
    setColumns((current) => axis === "columns" ? [...current.filter((item) => item.dimension.toLowerCase() !== dimension.toLowerCase()), moved] : current.filter((item) => item.dimension.toLowerCase() !== dimension.toLowerCase()));
  }

  function enableManualMetadata() {
    setManualMetadata(true);
    setRows([entry("Account")]);
    setColumns([entry("Period")]);
    setPov([]);
  }

  async function loadGrid(event?: FormEvent) {
    event?.preventDefault();
    setError(null);
    try {
      const payload = buildPayload(cube, pov, rows, columns);
      setLoadingGrid(true);
      const response = await api.dataReviewGrid(payload, csrfToken);
      setReview(response.review);
      setReviewedSlice(payload);
      saveLayout(payload);
      setSelectedCell(null);
      setPage(1);
      setQuery("");
      setLayoutOpen(false);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setLoadingGrid(false);
    }
  }

  async function exportGrid() {
    setError(null);
    try {
      const payload = reviewedSlice ?? buildPayload(cube, pov, rows, columns);
      setExportingGrid(true);
      const blob = await api.exportDataReviewGrid(payload, csrfToken);
      downloadBlob(blob, `${safeFilename(payload.cube)}-data-review.xlsx`);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setExportingGrid(false);
    }
  }

  return <section className="data-review-workspace">
    <header className="page-intro data-review-intro">
      <div><span className="eyebrow">{taskContext ? "Assigned Planning validation" : "Read-only Planning analysis"}</span><h1>{taskContext?.task.title ?? "Data Review"}</h1><p>{taskContext?.task.description || "Build an ad-hoc Planning grid by choosing a cube, POV, rows, and columns—similar to Smart View, without changing Oracle data."}</p></div>
      <div className="data-review-intro__actions"><span className="read-only-badge"><Icon name="check" /> Read-only</span></div>
    </header>

    {loadingTask && <div className="task-validation-context"><span className="spinner" /><div><strong>Preparing your assigned validation</strong><small>Loading the approved cube, POV, and validation rules.</small></div></div>}
    {taskContext && <div className="task-validation-context task-validation-context--ready"><Icon name="tasks" /><div><strong>{taskContext.task.cycle_name} · {taskContext.task.stage_name}</strong><small>{taskContext.suggested_slice ? "The assigned data intersection has been restored. Run the validation below to complete this responsibility." : "No fixed layout was configured. Your first validated layout becomes the reusable context for this responsibility."}</small></div></div>}

    {error && <FeedbackBanner tone="error" title="Review the layout" message={error} />}

    <form className={`panel slice-designer${layoutOpen ? " is-open" : ""}`} onSubmit={(event) => void loadGrid(event)}>
      <header className="slice-designer__header">
        <div><span className="eyebrow">Grid definition</span><h2>{review && !layoutOpen ? "Current data intersection" : "Choose the data intersection"}</h2><p>Every dimension belongs to exactly one location. POV uses one member; rows and columns can use many.</p></div>
        {review && <button type="button" className="button button--quiet" onClick={() => setLayoutOpen((current) => !current)}>{layoutOpen ? "Hide layout" : "Edit layout"}</button>}
      </header>

      {layoutOpen && <div className="slice-designer__body">
        <section className="cube-picker">
          <label className="runner-field"><span>Live cube / plan type *</span><select aria-label="Cube or plan type" value={cube} disabled={loadingCubes || cubes.length === 0} onChange={(event) => void chooseCube(event.target.value)}><option value="">{loadingCubes ? "Loading cubes from Oracle..." : cubes.length ? "Select a cube" : "No live cubes available"}</option>{cubes.map((item) => <option value={item.name} key={item.name}>{cubeLabel(item)}</option>)}</select><small>{cubes.length ? `${cubes.length} current cube${cubes.length === 1 ? "" : "s"} fetched from Oracle.` : "The list is never populated from registered reports."}</small></label>
          <button type="button" className="button button--secondary" disabled={loadingCubes} onClick={() => void refreshCubes()}>{loadingCubes ? <><span className="spinner" /> Refreshing...</> : <>Refresh from Oracle <Icon name="refresh" /></>}</button>
          {dimensions.length > 0 && <button type="button" className="button button--quiet" onClick={() => applySmartLayout()}><Icon name="sparkle" /> Smart layout</button>}
        </section>

        {cubeDiscoveryError && <section className="metadata-compatibility"><div><Icon name="alert" /><span><strong>Live cube discovery is unavailable</strong><small>{cubeDiscoveryError}</small></span></div><details><summary>Advanced compatibility fallback</summary><p>Use this only for an older on-premise environment that does not expose live plan-type discovery.</p><label className="runner-field"><span>Exact cube name</span><input value={manualCube} onChange={(event) => setManualCube(event.target.value)} placeholder="Plan1" /></label><button type="button" className="button button--quiet" disabled={!manualCube.trim()} onClick={() => void chooseCube(manualCube)}>Try exact cube</button></details></section>}

        {cube && dimensions.length > 0 && <>
          <div className="dimension-coverage"><span><strong>{dimensions.length || assignedDimensions.length}</strong> dimensions</span><span><strong>{pov.length}</strong> POV</span><span><strong>{rows.length}</strong> row axis</span><span><strong>{columns.length}</strong> column axis</span></div>
          <DimensionPlacement dimensions={dimensions} pov={pov} rows={rows} columns={columns} onPlace={placeDimension} />
          <div className="slice-axis-layout">
            <AxisBuilder cube={cube} axis="pov" title="Point of view" description="One fixed member per dimension." entries={pov} dimensions={dimensions} assigned={assignedDimensions} onAdd={addAxis} onUpdate={updateAxis} onRemove={removeAxis} />
            <AxisBuilder cube={cube} axis="columns" title="Columns" description="Periods and other dimensions across the sheet." entries={columns} dimensions={dimensions} assigned={assignedDimensions} onAdd={addAxis} onUpdate={updateAxis} onRemove={removeAxis} required />
            <AxisBuilder cube={cube} axis="rows" title="Rows" description="Accounts, products, entities, or other members." entries={rows} dimensions={dimensions} assigned={assignedDimensions} onAdd={addAxis} onUpdate={updateAxis} onRemove={removeAxis} required />
          </div>
          <aside className="slice-tip"><Icon name="sparkle" /><span><strong>Mouse-first member selection</strong><small>Click Browse on any dimension, select Oracle members, and use Load more for large hierarchies. Search remains optional.</small></span></aside>
        </>}

        {cube && !loadingDimensions && dimensionDiscoveryError && !manualMetadata && <section className="metadata-compatibility"><div><Icon name="alert" /><span><strong>This Oracle release does not expose live dimensions</strong><small>{dimensionDiscoveryError}</small></span></div><p>The platform uses Oracle's supported plan-type metadata API when it is available. You can retry after an Oracle update, or continue with exact names; the cube-slice request itself remains live and read-only.</p><div className="metadata-compatibility__actions"><button type="button" className="button button--secondary" onClick={() => void chooseCube(cube)}>Retry Oracle</button><button type="button" className="button button--quiet" onClick={enableManualMetadata}>Use exact names</button></div></section>}

        {cube && manualMetadata && <>{restoredLayout && <aside className="slice-tip"><Icon name="check" /><span><strong>Last successful layout restored</strong><small>Oracle metadata discovery is unavailable, so this browser restored the dimensions and members from your last successful review of this cube.</small></span></aside>}<div className="slice-axis-layout"><AxisBuilder cube={cube} axis="pov" title="Point of view" description="One fixed member per dimension." entries={pov} dimensions={[]} assigned={assignedDimensions} onAdd={addAxis} onUpdate={updateAxis} onRemove={removeAxis} /><AxisBuilder cube={cube} axis="columns" title="Columns" description="Periods and other dimensions across the sheet." entries={columns} dimensions={[]} assigned={assignedDimensions} onAdd={addAxis} onUpdate={updateAxis} onRemove={removeAxis} required /><AxisBuilder cube={cube} axis="rows" title="Rows" description="Accounts, products, entities, or other members." entries={rows} dimensions={[]} assigned={assignedDimensions} onAdd={addAxis} onUpdate={updateAxis} onRemove={removeAxis} required /></div></>}
      </div>}

      {layoutOpen && <footer className="runner-actions"><span className="slice-safety"><Icon name={layoutReady ? "check" : "alert"} /> {layoutReady ? "Layout ready · maximum 250,000 requested cells" : "Choose one or more members for every dimension"}</span><button className="button button--primary" disabled={!layoutReady || loadingGrid}>{loadingGrid ? <><span className="spinner" /> Loading Oracle data...</> : <>Load live data <Icon name="arrow" /></>}</button></footer>}
    </form>

    {review && <SpreadsheetReview review={review} selectedCell={selectedCell} query={query} page={page} pageSize={pageSize} exporting={exportingGrid} onSelectedCell={setSelectedCell} onQuery={(value) => { setQuery(value); setPage(1); }} onPage={setPage} onPageSize={(value) => { setPageSize(value); setPage(1); }} onRefresh={() => void loadGrid()} onExport={() => void exportGrid()} />}
    {reviewedSlice && <DataValidationWorkbench slice={reviewedSlice} cubes={cubes} csrfToken={csrfToken} planningTaskId={planningTaskId} configuration={taskContext?.configuration ?? null} initialEvidence={taskContext?.task.latest_validation ?? null} />}
  </section>;
}

function DimensionPlacement({ dimensions, pov, rows, columns, onPlace }: { dimensions: DataReviewDimension[]; pov: AxisEntry[]; rows: AxisEntry[]; columns: AxisEntry[]; onPlace: (dimension: string, axis: AxisName) => void }) {
  const placements = new Map<string, AxisName>();
  pov.forEach((item) => placements.set(item.dimension.toLowerCase(), "pov"));
  rows.forEach((item) => placements.set(item.dimension.toLowerCase(), "rows"));
  columns.forEach((item) => placements.set(item.dimension.toLowerCase(), "columns"));
  return <section className="dimension-placement"><header><div><span className="eyebrow">Dimension layout</span><h3>Place every dimension</h3><p>Oracle supplied these dimensions for the selected cube. Move each one with a dropdown; no names need to be typed.</p></div><span className="live-metadata-badge"><Icon name="refresh" /> Live metadata</span></header><div className="dimension-placement__grid">{dimensions.map((dimension) => <label key={dimension.name}><span><strong>{dimension.name}</strong><small>{dimension.dimension_type || "Planning dimension"}</small></span><select aria-label={`Placement for ${dimension.name}`} value={placements.get(dimension.name.toLowerCase()) ?? "pov"} onChange={(event) => onPlace(dimension.name, event.target.value as AxisName)}><option value="pov">Point of view</option><option value="rows">Rows</option><option value="columns">Columns</option></select></label>)}</div></section>;
}

function AxisBuilder({ cube, axis, title, description, entries, dimensions, assigned, required = false, onAdd, onUpdate, onRemove }: { cube: string; axis: AxisName; title: string; description: string; entries: AxisEntry[]; dimensions: DataReviewDimension[]; assigned: string[]; required?: boolean; onAdd: (axis: AxisName) => void; onUpdate: (axis: AxisName, id: number, update: Partial<AxisEntry>) => void; onRemove: (axis: AxisName, id: number) => void }) {
  return <section className={`sheet-axis sheet-axis--${axis}`}><header><div><span className="sheet-axis__icon"><Icon name={axis === "pov" ? "settings" : axis === "rows" ? "tasks" : "calendar"} /></span><div><h3>{title}{required ? " *" : ""}</h3><p>{description}</p></div></div><button type="button" aria-label={`Add ${title} dimension`} onClick={() => onAdd(axis)}>+ Add</button></header>
    <div className="sheet-axis__entries">{entries.length ? entries.map((item) => <AxisCard cube={cube} axis={axis} item={item} dimensions={dimensions} assigned={assigned} onUpdate={onUpdate} onRemove={onRemove} key={item.id} />) : <button type="button" className="axis-empty" onClick={() => onAdd(axis)}><strong>{axis === "pov" ? "No fixed dimensions" : `Add a ${axis === "rows" ? "row" : "column"} dimension`}</strong><small>{axis === "pov" ? "Add only dimensions that need a fixed member." : "At least one is required."}</small></button>}</div>
  </section>;
}

function AxisCard({ cube, axis, item, dimensions, assigned, onUpdate, onRemove }: { cube: string; axis: AxisName; item: AxisEntry; dimensions: DataReviewDimension[]; assigned: string[]; onUpdate: (axis: AxisName, id: number, update: Partial<AxisEntry>) => void; onRemove: (axis: AxisName, id: number) => void }) {
  const label = axis === "pov" ? "POV" : axis === "rows" ? "Row" : "Column";
  return <article className="axis-card">
    <div className="axis-card__top"><label><span>Dimension</span>{dimensions.length ? <select aria-label={`${label} dimension`} value={item.dimension} onChange={(event) => onUpdate(axis, item.id, { dimension: event.target.value, members: [] })}><option value="">Choose dimension</option>{dimensions.map((dimension) => { const usedElsewhere = assigned.some((name) => name.toLowerCase() === dimension.name.toLowerCase()) && dimension.name.toLowerCase() !== item.dimension.toLowerCase(); return <option value={dimension.name} disabled={usedElsewhere} key={dimension.name}>{dimension.name}</option>; })}</select> : <input aria-label={`${label} dimension`} value={item.dimension} onChange={(event) => onUpdate(axis, item.id, { dimension: event.target.value })} placeholder={axis === "pov" ? "Scenario" : axis === "columns" ? "Period" : "Account"} />}</label><button type="button" aria-label={`Remove ${label} dimension`} onClick={() => onRemove(axis, item.id)}><Icon name="close" /></button></div>
    <MemberSelector cube={cube} dimension={item.dimension} members={item.members} multiple={axis !== "pov"} ariaLabel={`${label}${axis === "pov" ? " member" : " members"} for ${item.dimension || "dimension"}`} onChange={(members) => onUpdate(axis, item.id, { members })} />
  </article>;
}

function SpreadsheetReview({ review, selectedCell, query, page, pageSize, exporting, onSelectedCell, onQuery, onPage, onPageSize, onRefresh, onExport }: { review: DataReviewResult; selectedCell: SelectedCell | null; query: string; page: number; pageSize: number; exporting: boolean; onSelectedCell: (cell: SelectedCell) => void; onQuery: (value: string) => void; onPage: (page: number) => void; onPageSize: (size: number) => void; onRefresh: () => void; onExport: () => void }) {
  const filtered = review.grid.rows.filter((row) => !query.trim() || row.headers.join(" ").toLowerCase().includes(query.trim().toLowerCase()));
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const start = (safePage - 1) * pageSize;
  const visible = filtered.slice(start, start + pageSize);
  return <section className="panel spreadsheet-panel">
    <header className="spreadsheet-header"><div><span className="eyebrow">Live Oracle data</span><h2>{review.cube} planning grid</h2><p>{review.row_count.toLocaleString()} rows and {review.column_count.toLocaleString()} columns returned from the selected intersection.</p></div><div><span className="read-only-badge"><Icon name="check" /> Read-only</span><button type="button" className="button button--quiet" disabled={exporting} onClick={onExport}>{exporting ? <><span className="spinner" /> Creating Excel...</> : <><Icon name="reports" /> Export Excel</>}</button><button type="button" className="button button--quiet" onClick={onRefresh}><Icon name="refresh" /> Refresh</button></div></header>
    <div className="sheet-pov">{review.grid.pov.length ? review.grid.pov.map(([dimension, member]) => <span key={dimension}><small>{dimension}</small><strong>{member}</strong></span>) : <span><small>Point of view</small><strong>No fixed POV returned</strong></span>}</div>
    <div className="sheet-metrics"><span><small>Rows</small><strong>{review.row_count.toLocaleString()}</strong></span><span><small>Columns</small><strong>{review.column_count.toLocaleString()}</strong></span><span><small>Data cells</small><strong>{review.cell_count.toLocaleString()}</strong></span><span className={review.missing_cell_count ? "has-warning" : ""}><small>Missing</small><strong>{review.missing_cell_count.toLocaleString()}</strong></span></div>
    <div className="sheet-toolbar"><label className="search-field"><Icon name="search" /><span className="sr-only">Search row members</span><input value={query} onChange={(event) => onQuery(event.target.value)} placeholder="Search row members" /></label><label><span>Rows per page</span><select value={pageSize} onChange={(event) => onPageSize(Number(event.target.value))}><option value="25">25</option><option value="50">50</option><option value="100">100</option></select></label></div>
    <div className="sheet-formula"><span>fx</span><strong>{selectedCell?.reference || "Select a data cell"}</strong><output>{selectedCell ? displayValue(selectedCell.value) : "Cell value appears here"}</output></div>
    <div className="spreadsheet-wrap"><table className="spreadsheet-grid"><thead>{review.grid.column_dimensions.map((dimension, level) => <tr key={dimension}><th className="sheet-corner" style={{ top: level * 35 }} colSpan={Math.max(review.grid.row_dimensions.length, 1)}>{dimension}</th>{columnGroups(review.grid.columns, level).map((group, index) => <th style={{ top: level * 35 }} colSpan={group.span} key={`${group.value}-${index}`}>{group.value}</th>)}</tr>)}{!review.grid.column_dimensions.length && <tr><th className="sheet-corner" colSpan={Math.max(review.grid.row_dimensions.length, 1)}>Rows</th>{review.grid.columns.map((_, index) => <th key={index}>Column {index + 1}</th>)}</tr>}<tr className="sheet-dimension-row">{review.grid.row_dimensions.length ? review.grid.row_dimensions.map((dimension, index) => <th style={{ top: review.grid.column_dimensions.length * 35, left: index * 155 }} key={dimension}>{dimension}</th>) : <th style={{ top: review.grid.column_dimensions.length * 35 }}>Row</th>}{review.grid.columns.map((column, index) => <th style={{ top: review.grid.column_dimensions.length * 35 }} key={index}>{column[column.length - 1] ?? `Column ${index + 1}`}</th>)}</tr></thead><tbody>{visible.map((row, rowIndex) => <tr key={`${row.headers.join("|")}-${rowIndex}`}>{row.headers.length ? row.headers.map((header, index) => <th style={{ left: index * 155 }} key={`${header}-${index}`}>{header}</th>) : <th>{start + rowIndex + 1}</th>}{row.data.map((value, columnIndex) => { const reference = `${row.headers.join(" · ")} × ${(review.grid.columns[columnIndex] ?? []).join(" · ")}`; const missing = isMissing(value); const active = selectedCell?.reference === reference; return <td className={`${missing ? "is-missing" : ""}${active ? " is-selected" : ""}`} tabIndex={0} onClick={() => onSelectedCell({ reference, value })} onFocus={() => onSelectedCell({ reference, value })} key={columnIndex}>{displayValue(value)}</td>; })}</tr>)}</tbody></table></div>
    {!visible.length && <div className="sheet-empty"><Icon name="search" /><strong>No rows match this search</strong><small>Clear the search to return to the complete grid.</small></div>}
    <footer className="sheet-pagination"><span>{filtered.length ? `Showing ${start + 1}–${Math.min(start + pageSize, filtered.length)} of ${filtered.length} rows` : "0 rows"}</span><div><button className="button button--quiet" disabled={safePage <= 1} onClick={() => onPage(safePage - 1)}>Previous</button><strong>Page {safePage} of {totalPages}</strong><button className="button button--quiet" disabled={safePage >= totalPages} onClick={() => onPage(safePage + 1)}>Next</button></div></footer>
  </section>;
}

function buildPayload(cube: string, pov: AxisEntry[], rows: AxisEntry[], columns: AxisEntry[]): DataReviewSliceInput {
  const normalizedCube = cube.trim();
  if (!normalizedCube) throw new Error("Select a Planning cube.");
  if (!rows.length) throw new Error("Add at least one row dimension.");
  if (!columns.length) throw new Error("Add at least one column dimension.");
  const all = [...pov, ...rows, ...columns];
  const seen = new Set<string>();
  for (const item of all) {
    const dimension = item.dimension.trim();
    if (!dimension) throw new Error("Choose a dimension for every layout card.");
    const key = dimension.toLowerCase();
    if (seen.has(key)) throw new Error(`Dimension '${dimension}' is assigned more than once.`);
    seen.add(key);
    if (!item.members.length || item.members.some((member) => !member.trim())) throw new Error(`Add at least one member for '${dimension}'.`);
  }
  return {
    cube: normalizedCube,
    pov: Object.fromEntries(pov.map((item) => [item.dimension.trim(), item.members[0].trim()])),
    rows: axisPayload(rows),
    columns: axisPayload(columns)
  };
}

function axisPayload(entries: AxisEntry[]): DataReviewAxisInput[] {
  return entries.map((item) => ({ dimension: item.dimension.trim(), members: item.members.map((member) => member.trim()) }));
}

function entry(dimension = "", members: string[] = []): AxisEntry {
  return { id: nextEntryId++, dimension, members: [...members] };
}

function planningTaskIdFromLocation() {
  const value = new URLSearchParams(window.location.search).get("planning_task_id");
  if (!value || !/^\d+$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

function findDimension(dimensions: DataReviewDimension[], preferred: string, excluded?: string) {
  return dimensions.find((item) => item.name.toLowerCase() === preferred.toLowerCase() && item.name !== excluded)
    ?? dimensions.find((item) => item.dimension_type?.toLowerCase().includes(preferred.toLowerCase()) && item.name !== excluded);
}

function columnGroups(columns: string[][], level: number) {
  const groups: { value: string; span: number }[] = [];
  columns.forEach((column) => {
    const value = column[level] ?? "";
    const previous = groups.at(-1);
    if (previous?.value === value) previous.span += 1;
    else groups.push({ value, span: 1 });
  });
  return groups;
}

function isMissing(value: unknown) {
  return value == null || ["", "#missing", "missing", "none", "null"].includes(String(value).trim().toLowerCase());
}

function displayValue(value: unknown) {
  if (isMissing(value)) return "—";
  if (typeof value === "number") {
    if (value !== 0 && Math.abs(value) < 1e-9) return value.toExponential(6);
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: 12 }).format(value);
  }
  return String(value);
}

function cubeLabel(cube: DataReviewCube) {
  return cube.dimension_count == null ? cube.name : `${cube.name} · ${cube.dimension_count} dimensions`;
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

function saveLayout(payload: DataReviewSliceInput) {
  try {
    window.localStorage.setItem(layoutStorageKey(payload.cube), JSON.stringify(payload));
  } catch {
    // Browser privacy settings may disable local storage; live review still works.
  }
}

function loadSavedLayout(cube: string): DataReviewSliceInput | null {
  try {
    const raw = window.localStorage.getItem(layoutStorageKey(cube));
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<DataReviewSliceInput>;
    if (
      value.cube?.toLowerCase() !== cube.toLowerCase()
      || !value.pov
      || !Array.isArray(value.rows)
      || !value.rows.length
      || !Array.isArray(value.columns)
      || !value.columns.length
    ) return null;
    return value as DataReviewSliceInput;
  } catch {
    return null;
  }
}

function layoutStorageKey(cube: string) {
  return `bisp-epm-data-review:${window.location.host}:${cube.trim().toLowerCase()}`;
}

function consumeAgentDataReviewHandoff(): DataReviewSliceInput | null {
  try {
    const raw = window.sessionStorage.getItem(AGENT_DATA_REVIEW_HANDOFF_KEY);
    window.sessionStorage.removeItem(AGENT_DATA_REVIEW_HANDOFF_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<DataReviewSliceInput>;
    if (!value.cube || !value.pov || !value.rows?.length || !value.columns?.length) return null;
    return value as DataReviewSliceInput;
  } catch {
    return null;
  }
}

function consumeAgentDataReviewCube(): string | null {
  try {
    const cube = window.sessionStorage.getItem(AGENT_DATA_REVIEW_CUBE_KEY)?.trim();
    window.sessionStorage.removeItem(AGENT_DATA_REVIEW_CUBE_KEY);
    return cube || null;
  } catch {
    return null;
  }
}

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : "Planning data could not be loaded.";
}

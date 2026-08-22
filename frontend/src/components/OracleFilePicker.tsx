import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import type { OracleFilePurpose, OracleRepositoryFile } from "../api/types";
import { Icon } from "./Icon";

interface OracleFilePickerProps {
  purpose: OracleFilePurpose;
  value: string;
  onChange: (value: string) => void;
  label?: string;
}

export function OracleFilePicker({
  purpose,
  value,
  onChange,
  label = "Oracle Inbox file"
}: OracleFilePickerProps) {
  const [files, setFiles] = useState<OracleRepositoryFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [manualOpen, setManualOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selected = useMemo(
    () => files.find((file) => file.name === value) ?? null,
    [files, value]
  );

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const catalog = await api.oracleFileCatalog(purpose);
      setFiles(catalog.files);
      if (!value && catalog.files.length === 1) {
        onChange(catalog.files[0].name);
      }
    } catch (reason) {
      setFiles([]);
      setManualOpen(true);
      setError(errorMessage(reason));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [purpose]);

  return <section className="oracle-file-picker">
    <header>
      <div><span>{label} *</span><small>Live files from the connected Oracle environment, newest first.</small></div>
      <button type="button" className="button button--quiet" disabled={loading} onClick={() => void load()}>{loading ? <span className="spinner spinner--dark" /> : <Icon name="refresh" />} Refresh files</button>
    </header>

    {error && <div className="oracle-file-picker__notice"><Icon name="alert" /><div><strong>Live file discovery is unavailable</strong><p>{error} You can still enter an exact reference below.</p></div></div>}

    <label className="runner-field">
      <span className="sr-only">{label}</span>
      <select aria-label={label} value={selected ? value : ""} disabled={loading || !files.length} onChange={(event) => onChange(event.target.value)}>
        <option value="">{loading ? "Reading Oracle Inbox..." : files.length ? "Choose an available file" : "No compatible Inbox files found"}</option>
        {files.map((file) => <option value={file.name} key={file.name}>{optionLabel(file)}</option>)}
      </select>
      <small>{loading ? "Connecting to Oracle." : `${files.length} compatible file${files.length === 1 ? "" : "s"} available.`}</small>
    </label>

    {selected && <div className="oracle-file-picker__selected"><span><Icon name="check" /></span><div><strong>{selected.name}</strong><small>{selected.folder} · {formatFileSize(selected.size_bytes)} · {formatModified(selected.last_modified_epoch_ms)}</small></div></div>}

    <button type="button" className="oracle-file-picker__manual-toggle" aria-expanded={manualOpen} onClick={() => setManualOpen((current) => !current)}>{manualOpen ? "Hide manual reference" : "Advanced: enter an exact reference"} <Icon name="chevron" /></button>
    {manualOpen && <label className="runner-field oracle-file-picker__manual"><span>Exact Inbox filename or reference *</span><input aria-label="Exact Inbox filename or reference" value={value} onChange={(event) => onChange(event.target.value)} placeholder="Forecast_Data.csv" maxLength={500} /><small>Use only when the required file is not returned by Oracle file discovery.</small></label>}
  </section>;
}

function optionLabel(file: OracleRepositoryFile) {
  return `${file.name} — ${formatModified(file.last_modified_epoch_ms)}`;
}

function formatFileSize(bytes: number | null) {
  if (bytes === null) return "Size unavailable";
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatModified(epochMs: number | null) {
  if (epochMs === null) return "Modified time unavailable";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(epochMs));
}

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : "Oracle files could not be loaded.";
}

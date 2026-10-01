import type { OracleRecordStatistics } from "../api/types";

export function OracleLoadStatistics({
  statistics
}: {
  statistics: OracleRecordStatistics;
}) {
  const hasBreakdown = statistics.details.length > 1
    || statistics.details.some((item) => item.dimension_name || item.load_type);
  const isDataIntegration = statistics.source.startsWith(
    "ORACLE_DATA_INTEGRATION"
  );
  const evidenceSource = statistics.source === "ORACLE_DATA_INTEGRATION_LOG"
    ? "Parsed from the Oracle Data Integration log"
    : statistics.source === "ORACLE_DATA_INTEGRATION_STATUS"
      ? "Reported by Oracle Data Integration"
      : statistics.source === "ORACLE_COMBINED_EVIDENCE"
        ? "Combined from Oracle execution evidence"
        : "Reported by Oracle Planning";
  return <section className="oracle-load-statistics" aria-label="Oracle load results">
    <header>
      <div><span className="eyebrow">Oracle job details</span><h3>Load results</h3></div>
      <small>{evidenceSource}</small>
    </header>
    <div className="oracle-load-metrics">
      <LoadMetric label="Records read" value={statistics.records_read} />
      <LoadMetric label={isDataIntegration ? "Records processed / loaded" : "Records processed"} value={statistics.records_processed} />
      <LoadMetric label="Records rejected" value={statistics.records_rejected} warning={statistics.records_rejected > 0} />
    </div>
    {hasBreakdown && <div className="oracle-load-breakdown"><table><thead><tr><th>Dimension / load</th><th>Read</th><th>Processed</th><th>Rejected</th></tr></thead><tbody>{statistics.details.map((item, index) => <tr key={`${item.dimension_name || item.load_type || "load"}-${index}`}><td>{item.dimension_name || item.load_type || "Load"}</td><td>{formatCount(item.records_read)}</td><td>{formatCount(item.records_processed)}</td><td className={item.records_rejected ? "is-rejected" : ""}>{formatCount(item.records_rejected)}</td></tr>)}</tbody></table></div>}
    <p>{isDataIntegration ? "Processed / loaded is the explicit completion counter found in Oracle's Data Integration status or log; it does not necessarily mean every record changed an existing value." : "Processed is Oracle's load counter; it does not necessarily mean every record changed an existing value."}</p>
  </section>;
}

function LoadMetric({ label, value, warning = false }: { label: string; value: number; warning?: boolean }) {
  return <span className={warning ? "is-warning" : ""}><small>{label}</small><strong>{formatCount(value)}</strong></span>;
}

function formatCount(value: number) {
  return new Intl.NumberFormat().format(value);
}

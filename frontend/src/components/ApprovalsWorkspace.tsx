import { useState } from "react";

import type { PlanningApproval } from "../api/types";
import { Icon } from "./Icon";

interface ApprovalsWorkspaceProps {
  approvals: PlanningApproval[];
  busyApprovalId: number | null;
  onDecision: (approvalId: number, decision: "APPROVED" | "RETURNED", comment: string) => Promise<void>;
}

export function ApprovalsWorkspace({ approvals, busyApprovalId, onDecision }: ApprovalsWorkspaceProps) {
  const [filter, setFilter] = useState<"PENDING" | "ALL">("PENDING");
  const [comments, setComments] = useState<Record<number, string>>({});
  const [validation, setValidation] = useState<Record<number, string>>({});
  const visible = approvals.filter((item) => filter === "ALL" || item.status === filter);
  const pending = approvals.filter((item) => item.status === "PENDING").length;

  async function decide(item: PlanningApproval, decision: "APPROVED" | "RETURNED") {
    const comment = String(comments[item.approval_id] ?? "").trim();
    if (decision === "RETURNED" && !comment) {
      setValidation((current) => ({ ...current, [item.approval_id]: "Explain what the planner must change before returning the submission." }));
      return;
    }
    setValidation((current) => ({ ...current, [item.approval_id]: "" }));
    await onDecision(item.approval_id, decision, comment);
  }

  return <section className="approvals-workspace">
    <header className="page-intro">
      <div><span className="eyebrow">Management by exception</span><h1>Approvals</h1><p>Review submitted Planning work in business context, then approve it or return it with clear guidance.</p></div>
      <span className="live-badge"><span />{pending} awaiting your decision</span>
    </header>
    <div className="attention-strip">
      <ApprovalMetric label="Awaiting review" value={pending} tone="warning" icon="tasks" />
      <ApprovalMetric label="Approved" value={approvals.filter((item) => item.status === "APPROVED").length} tone="success" icon="check" />
      <ApprovalMetric label="Returned" value={approvals.filter((item) => item.status === "RETURNED").length} tone="danger" icon="alert" />
      <ApprovalMetric label="Total decisions" value={approvals.length} tone="neutral" icon="activity" />
    </div>
    <section className="panel approval-queue">
      <div className="work-list-heading"><div><span className="eyebrow">Review queue</span><h2>{filter === "PENDING" ? "Requires your review" : "Approval history"}</h2></div><div className="approval-filter" role="group" aria-label="Approval filter"><button className={filter === "PENDING" ? "is-active" : ""} onClick={() => setFilter("PENDING")}>Pending</button><button className={filter === "ALL" ? "is-active" : ""} onClick={() => setFilter("ALL")}>All</button></div></div>
      {visible.length ? <div className="approval-list">{visible.map((item) => <article className={`approval-card approval-card--${item.status.toLowerCase()}`} key={item.approval_id}>
        <div className="approval-card__top"><div><span className={`status-badge status-${item.status.toLowerCase()}`}>{item.status.toLowerCase()}</span><h3>{item.approval_task_title}</h3><p>{item.submitted_task_title}</p></div><div className="approval-submitter"><small>Submitted by</small><strong>{item.submitted_by_name}</strong><span>{formatDateTime(item.submitted_at)}</span></div></div>
        <div className="approval-context"><span><small>Planning cycle</small><strong>{item.cycle_name}</strong></span><span><small>Entity</small><strong>{item.entity || "All assigned entities"}</strong></span><span><small>Scenario / period</small><strong>{[item.scenario, item.period].filter(Boolean).join(" · ") || "Cycle context"}</strong></span></div>
        {item.validation && <div className={`approval-validation approval-validation--${item.validation.status.toLowerCase()}`}><span><Icon name={item.validation.status === "PASS" ? "check" : "alert"} /></span><div><small>Submitted validation evidence</small><strong>{item.validation.validation_type === "COMPARISON" ? `${item.validation.source_cube} to ${item.validation.target_cube}` : `${item.validation.source_cube} quality checks`} · {item.validation.status}</strong><p>{item.validation.checked_cells.toLocaleString()} cells checked · {item.validation.exception_count.toLocaleString()} exceptions · {formatDateTime(item.validation.performed_at)}</p></div><a className="button button--quiet" href={`/?planning_task_id=${item.validation.task_id}#data-review`}>Open evidence <Icon name="arrow" /></a></div>}
        {item.status === "PENDING" ? <>
          <label className="approval-comment"><span>Review comment</span><textarea value={comments[item.approval_id] ?? ""} onChange={(event) => setComments((current) => ({ ...current, [item.approval_id]: event.target.value }))} placeholder="Optional when approving; required when returning for changes." /></label>
          {validation[item.approval_id] && <div className="inline-error"><Icon name="alert" />{validation[item.approval_id]}</div>}
          <div className="approval-actions">{!item.validation && <a className="button button--quiet" href="/app/data-review">Review data <Icon name="arrow" /></a>}<button className="button approval-return" disabled={busyApprovalId === item.approval_id} onClick={() => decide(item, "RETURNED")}>Return for changes</button><button className="button button--primary" disabled={busyApprovalId === item.approval_id} onClick={() => decide(item, "APPROVED")}>{busyApprovalId === item.approval_id ? <span className="spinner" /> : <Icon name="check" />} Approve</button></div>
        </> : <div className="approval-decision"><Icon name={item.status === "APPROVED" ? "check" : "alert"} /><div><strong>{item.status === "APPROVED" ? "Approved" : "Returned for changes"}</strong><p>{item.decision_comment || "No decision comment was recorded."}</p>{item.decided_at && <small>{formatDateTime(item.decided_at)}</small>}</div></div>}
      </article>)}</div> : <ApprovalEmpty history={filter === "ALL"} />}
    </section>
  </section>;
}

function ApprovalMetric({ label, value, tone, icon }: { label: string; value: number; tone: string; icon: "tasks" | "check" | "alert" | "activity" }) {
  return <article className={`summary-metric summary-metric--${tone}`}><span className="summary-icon"><Icon name={icon} /></span><span><strong>{value}</strong><small>{label}</small></span></article>;
}

function ApprovalEmpty({ history }: { history: boolean }) {
  return <div className="work-empty work-empty--compact"><span><Icon name="check" /></span><h2>{history ? "No approval history" : "You are all caught up"}</h2><p>{history ? "Decisions assigned to you will be retained here." : "New submissions appear automatically when a planner completes a configured Submit for approval responsibility."}</p></div>;
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

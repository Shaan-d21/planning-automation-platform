import { Fragment, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import { api } from "../api/client";
import type {
  AgentActionDraft,
  AgentActionInputField,
  AgentApprovedExecution,
  AgentApprovalRequest,
  AgentClarificationRequest,
  AgentInputRequest,
  AgentConversation,
  AgentDataReviewContext,
  AgentMessage,
  AgentStatusResponse,
  AgentToolActivity,
  DataComparisonMismatch,
  DataReviewGrid,
  DataReviewSliceInput,
  PipelineFilePreview,
  PipelineStagePreview,
  PipelineVariablePreview,
  OracleRecordStatistics,
  StandaloneFlowProgress,
  StandaloneFlowRecoveryPlan
} from "../api/types";
import { Icon } from "./Icon";
import { OracleFilePicker } from "./OracleFilePicker";
import { ConfirmationDialog, FeedbackBanner, WorkspaceLoading } from "./Feedback";
import { useOperationMonitor } from "./useOperationMonitor";

const suggestedPrompts = [
  ["Explore", "What automation operations are available in this platform?"],
  ["Review data", "Show me Planning data for a cube and help me choose the POV, rows, and columns."],
  ["Activity", "Summarize the five most recent process executions."],
  ["Environment", "Which cubes are available in the connected Planning application?"],
  ["Prepare", "Prepare a governed draft to run a business rule. Ask me for any missing details."],
  ["Quality", "How should I validate Planning data before publishing it?"],
  ["Guidance", "Help me choose the safest operation for my Planning task."]
] as const;
const AGENT_DATA_REVIEW_HANDOFF_KEY = "bisp-epm-agent-data-review-handoff";

export function EpmAssistantWorkspace({ csrfToken }: { csrfToken: string }) {
  const [status, setStatus] = useState<AgentStatusResponse | null>(null);
  const [conversations, setConversations] = useState<AgentConversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [drafts, setDrafts] = useState<AgentActionDraft[]>([]);
  const [approval, setApproval] = useState<AgentApprovalRequest | null>(null);
  const [clarification, setClarification] = useState<AgentClarificationRequest | null>(null);
  const [inputRequest, setInputRequest] = useState<AgentInputRequest | null>(null);
  const [approvedExecution, setApprovedExecution] = useState<AgentApprovedExecution | null>(null);
  const [toolActivity, setToolActivity] = useState<AgentToolActivity[]>([]);
  const [reviewActivity, setReviewActivity] = useState<Record<number, AgentToolActivity[]>>({});
  const [reviewContext, setReviewContext] = useState<AgentDataReviewContext | null>(null);
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [sending, setSending] = useState(false);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deciding, setDeciding] = useState<"approve" | "reject" | null>(null);
  const [selecting, setSelecting] = useState(false);
  const [savingInputs, setSavingInputs] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const messageEnd = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let active = true;
    Promise.all([api.agentStatus(), api.agentConversations()])
      .then(async ([nextStatus, response]) => {
        if (!active) return;
        setStatus(nextStatus);
        setConversations(response.conversations);
        if (response.conversations.length) await openConversation(response.conversations[0].conversation_id);
      })
      .catch((reason: unknown) => active && setError(errorMessage(reason)))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (typeof messageEnd.current?.scrollIntoView === "function") {
      messageEnd.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [drafts, messages, sending]);

  async function refreshConversations() {
    const response = await api.agentConversations();
    setConversations(response.conversations);
  }

  async function openConversation(conversationId: string) {
    setActiveId(conversationId);
    setLoadingMessages(true);
    setError(null);
    setToolActivity([]);
    setReviewActivity({});
    setReviewContext(null);
    try {
      const response = await api.agentMessages(conversationId);
      setMessages(response.messages);
      setDrafts(response.action_drafts);
      setApproval(response.approval_request);
      setClarification(response.clarification_request);
      setInputRequest(response.input_request);
      setReviewContext(response.data_review_context ?? null);
      setApprovedExecution(null);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setLoadingMessages(false);
    }
  }

  async function createConversation() {
    setCreating(true);
    setError(null);
    try {
      const response = await api.createAgentConversation(csrfToken);
      setConversations((current) => [response.conversation, ...current]);
      setActiveId(response.conversation.conversation_id);
      setMessages([]);
      setDrafts([]);
      setApproval(null);
      setClarification(null);
      setInputRequest(null);
      setApprovedExecution(null);
      setToolActivity([]);
      setReviewActivity({});
      setReviewContext(null);
      return response.conversation.conversation_id;
    } catch (reason) {
      setError(errorMessage(reason));
      return null;
    } finally {
      setCreating(false);
    }
  }

  async function deleteConversation() {
    if (!activeId) return;
    setDeleting(true);
    setError(null);
    try {
      await api.deleteAgentConversation(activeId, csrfToken);
      const remaining = conversations.filter((item) => item.conversation_id !== activeId);
      setConversations(remaining);
      if (remaining.length) await openConversation(remaining[0].conversation_id);
      else {
        setActiveId(null);
        setMessages([]);
        setDrafts([]);
        setApproval(null);
        setClarification(null);
        setInputRequest(null);
        setApprovedExecution(null);
        setToolActivity([]);
        setReviewActivity({});
        setReviewContext(null);
      }
      setConfirmDelete(false);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setDeleting(false);
    }
  }

  async function sendMessage(event?: FormEvent, preparedPrompt?: string) {
    event?.preventDefault();
    const prompt = preparedPrompt?.trim() || content.trim();
    if (!prompt || sending || approval || clarification || inputRequest || !status?.enabled) return;
    setApprovedExecution(null);
    setSending(true);
    setError(null);
    setToolActivity([]);
    let conversationId = activeId;
    if (!conversationId) conversationId = await createConversation();
    if (!conversationId) {
      setSending(false);
      return;
    }
    const optimistic: AgentMessage = {
      message_id: -Date.now(),
      conversation_id: conversationId,
      role: "user",
      content: prompt,
      created_at: new Date().toISOString()
    };
    setMessages((current) => [...current, optimistic]);
    setContent("");
    try {
      const response = await api.sendAgentMessage(conversationId, prompt, csrfToken);
      setMessages((current) => [...current, response.message]);
      rememberReviewActivity(response.message, response.tool_activity);
      if (response.tool_activity.some((item) => ["review_data_slice", "compare_data_slices"].includes(item.name) && item.status === "SUCCESS")) setReviewContext(null);
      setDrafts((current) => [...current, ...response.action_drafts]);
      setApproval(response.approval_request);
      setClarification(response.clarification_request);
      setInputRequest(response.input_request);
      setToolActivity(response.tool_activity);
      await refreshConversations();
    } catch (reason) {
      setError(errorMessage(reason));
      const response = await api.agentMessages(conversationId).catch(() => null);
      if (response) {
        setMessages(response.messages);
        setDrafts(response.action_drafts);
        setApproval(response.approval_request);
        setClarification(response.clarification_request);
        setInputRequest(response.input_request);
      }
    } finally {
      setSending(false);
    }
  }

  async function resolveApproval(decision: "approve" | "reject") {
    if (!activeId || !approval || deciding) return;
    setApprovedExecution(null);
    setDeciding(decision);
    setError(null);
    try {
      const response = await api.resolveAgentApproval(
        activeId,
        approval.request_id,
        decision,
        csrfToken
      );
      setMessages((current) => [...current, response.message]);
      rememberReviewActivity(response.message, response.tool_activity);
      setDrafts((current) => [...current, ...response.action_drafts]);
      setToolActivity(response.tool_activity);
      setApproval(response.approval_request);
      setClarification(response.clarification_request);
      setInputRequest(response.input_request);
      setApprovedExecution(response.execution ?? null);
      await refreshConversations();
    } catch (reason) {
      setError(errorMessage(reason));
      const response = await api.agentMessages(activeId).catch(() => null);
      if (response) {
        setMessages(response.messages);
        setDrafts(response.action_drafts);
        setApproval(response.approval_request);
        setClarification(response.clarification_request);
        setInputRequest(response.input_request);
      }
    } finally {
      setDeciding(null);
    }
  }

  async function resolveClarification(value: string | null) {
    if (!activeId || !clarification || selecting) return;
    setApprovedExecution(null);
    setSelecting(true);
    setError(null);
    try {
      const response = await api.resolveAgentClarification(
        activeId,
        clarification.request_id,
        value,
        csrfToken
      );
      setMessages((current) => [...current, response.message]);
      rememberReviewActivity(response.message, response.tool_activity);
      setDrafts((current) => [...current, ...response.action_drafts]);
      setToolActivity(response.tool_activity);
      setApproval(response.approval_request);
      setClarification(response.clarification_request);
      setInputRequest(response.input_request);
      await refreshConversations();
    } catch (reason) {
      setError(errorMessage(reason));
      const response = await api.agentMessages(activeId).catch(() => null);
      if (response) {
        setMessages(response.messages);
        setDrafts(response.action_drafts);
        setApproval(response.approval_request);
        setClarification(response.clarification_request);
        setInputRequest(response.input_request);
      }
    } finally {
      setSelecting(false);
    }
  }

  async function synchronizeClarificationArtifacts() {
    if (!activeId || !clarification || selecting) return false;
    setApprovedExecution(null);
    setSelecting(true);
    setError(null);
    try {
      const response = await api.synchronizeAgentArtifacts(
        activeId,
        clarification.request_id,
        csrfToken
      );
      setClarification(response.clarification_request);
      return true;
    } catch (reason) {
      setError(errorMessage(reason));
      return false;
    } finally {
      setSelecting(false);
    }
  }

  async function registerClarificationArtifact(identifier: string) {
    if (!activeId || !clarification || selecting) return false;
    setApprovedExecution(null);
    setSelecting(true);
    setError(null);
    try {
      const response = await api.registerAgentArtifact(
        activeId,
        clarification.request_id,
        identifier,
        csrfToken
      );
      setMessages((current) => [...current, response.message]);
      rememberReviewActivity(response.message, response.tool_activity);
      setDrafts((current) => [...current, ...response.action_drafts]);
      setToolActivity(response.tool_activity);
      setApproval(response.approval_request);
      setClarification(response.clarification_request);
      setInputRequest(response.input_request);
      await refreshConversations();
      return true;
    } catch (reason) {
      setError(errorMessage(reason));
      return false;
    } finally {
      setSelecting(false);
    }
  }

  async function resolveInput(values: Record<string, unknown> | null) {
    if (!activeId || !inputRequest || savingInputs) return;
    setApprovedExecution(null);
    setSavingInputs(true);
    setError(null);
    try {
      const response = await api.resolveAgentInput(
        activeId,
        inputRequest.request_id,
        values,
        csrfToken
      );
      setMessages((current) => [...current, response.message]);
      rememberReviewActivity(response.message, response.tool_activity);
      setDrafts((current) => [...current, ...response.action_drafts]);
      setToolActivity(response.tool_activity);
      setApproval(response.approval_request);
      setClarification(response.clarification_request);
      setInputRequest(response.input_request);
      await refreshConversations();
    } catch (reason) {
      setError(errorMessage(reason));
      const response = await api.agentMessages(activeId).catch(() => null);
      if (response) {
        setMessages(response.messages);
        setDrafts(response.action_drafts);
        setApproval(response.approval_request);
        setClarification(response.clarification_request);
        setInputRequest(response.input_request);
      }
    } finally {
      setSavingInputs(false);
    }
  }

  function updateDraft(updated: AgentActionDraft) {
    setDrafts((current) => current.map((item) => item.draft_id === updated.draft_id ? updated : item));
  }

  function preparePrompt(prompt: string) {
    setContent(prompt);
    window.requestAnimationFrame(() => composer.current?.focus());
  }

  function rememberReviewActivity(
    message: AgentMessage,
    activities: AgentToolActivity[]
  ) {
    const reviewTools = [
        "list_planning_cubes",
        "list_cube_dimensions",
        "search_dimension_members",
        "review_data_slice",
        "compare_data_slices",
        "plan_multi_step_request"
    ];
    const reviews = activities.filter((item) => {
      if (!Boolean(item.result) || !reviewTools.includes(item.name)) {
        return false;
      }
      return item.status === "SUCCESS"
        || (["list_cube_dimensions", "review_data_slice"].includes(item.name) && item.status === "FAILED");
    });
    if (!reviews.length) return;
    setReviewActivity((current) => ({
      ...current,
      [message.message_id]: reviews
    }));
  }

  const activeConversation = conversations.find((item) => item.conversation_id === activeId) ?? null;
  if (loading) return <WorkspaceLoading label="EPM Assistant" message="Preparing your governed conversations…" />;

  return <section className="assistant-workspace">
    <header className="page-intro assistant-intro">
      <div><span className="eyebrow">Governed intelligence</span><h1>EPM Assistant</h1><p>Understand the connected Planning environment, investigate platform activity, and prepare safe actions for governed review.</p></div>
      <div className="assistant-intro__status"><span className={`assistant-status${status?.enabled ? " is-ready" : " is-warning"}`}><i />{status?.enabled ? "Assistant ready" : "Configuration required"}</span><span>{status?.provider ?? "Provider"} · {status?.model ?? "Model"}</span></div>
    </header>

    {error && <FeedbackBanner tone="error" title="The assistant could not complete that request" message={error} onDismiss={() => setError(null)} />}
    {!status?.enabled && <div className="assistant-configuration"><Icon name="settings" /><div><strong>EPM Assistant needs a model provider</strong><p>{status?.message} All other Planning workspaces remain available.</p></div></div>}

    <div className="assistant-layout">
      <aside className="panel assistant-history" aria-label="Assistant conversations">
        <header><div><span className="eyebrow">Your workspace</span><h2>Conversations</h2></div><button type="button" className="button button--primary" disabled={creating || !status?.enabled} onClick={() => void createConversation()}>{creating ? <span className="spinner" /> : "+ New"}</button></header>
        <div className="assistant-history__list">{conversations.length ? conversations.map((conversation) => <button type="button" className={conversation.conversation_id === activeId ? "is-active" : ""} onClick={() => void openConversation(conversation.conversation_id)} key={conversation.conversation_id}><span className="assistant-history__icon"><Icon name="assistant" /></span><span><strong>{conversation.title || "New conversation"}</strong><small>{formatConversationDate(conversation.updated_at)}</small></span></button>) : <div className="assistant-history__empty"><Icon name="assistant" /><strong>No conversations yet</strong><p>Start with one of the suggested questions.</p></div>}</div>
        <div className="assistant-privacy"><Icon name="check" /><div><strong>Protected by design</strong><p>Credentials and uploaded file contents are never exposed. Live Planning grids are read-only and available only to users with Data Review access.</p></div></div>
      </aside>

      <section className="panel assistant-chat">
        <header className="assistant-chat__header"><div><span className="eyebrow">Governed copilot</span><h2>{activeConversation?.title || "Start a conversation"}</h2><p>{activeConversation ? `${activeConversation.provider} · ${activeConversation.model}` : status?.message}</p></div>{activeId && <button type="button" className="icon-button" aria-label="Delete conversation" onClick={() => setConfirmDelete(true)}><Icon name="close" /></button>}</header>

        <div className={`assistant-messages${loadingMessages ? " is-loading" : ""}`} aria-live="polite">
          {loadingMessages ? <AssistantMessageSkeleton /> : messages.length ? messages.map((message) => <Fragment key={message.message_id}><MessageBubble message={message} />{(reviewActivity[message.message_id] ?? []).map((activity, index) => <AgentDataReviewCard activity={activity} csrfToken={csrfToken} onPrompt={preparePrompt} onPrepare={(prompt) => void sendMessage(undefined, prompt)} busy={sending} key={`${activity.name}-${index}`} />)}{drafts.filter((draft) => draft.message_id === message.message_id).map((draft) => <ActionDraftCard draft={draft} csrfToken={csrfToken} onUpdate={updateDraft} key={draft.draft_id} />)}</Fragment>) : <AssistantEmpty onPrompt={preparePrompt} />}
          {reviewContext && !Object.values(reviewActivity).some((items) => items.length) && <AgentReviewResumeCard context={reviewContext} onPrompt={preparePrompt} />}
          {clarification && <ClarificationCard clarification={clarification} busy={selecting} onSubmit={resolveClarification} onSynchronize={synchronizeClarificationArtifacts} onRegister={registerClarificationArtifact} />}
          {inputRequest && <GuidedInputCard request={inputRequest} busy={savingInputs} csrfToken={csrfToken} onSubmit={resolveInput} />}
          {approval && <ApprovalCard approval={approval} deciding={deciding} onDecision={resolveApproval} />}
          {approvedExecution && <AgentExecutionCard approved={approvedExecution} csrfToken={csrfToken} onRecoveryStarted={setApprovedExecution} onDismiss={() => setApprovedExecution(null)} />}
          {sending && <article className="assistant-message assistant-message--assistant is-thinking"><span className="assistant-avatar"><Icon name="assistant" /></span><div><span className="eyebrow">EPM Assistant</span><p><span className="spinner" /> Inspecting the permitted platform context…</p></div></article>}
          <div ref={messageEnd} />
        </div>

        {toolActivity.length > 0 && <div className="assistant-evidence"><Icon name="check" /><div><strong>Verified with platform tools</strong><p>{[...new Set(toolActivity.map((item) => friendlyName(item.name)))].join(" · ")}</p></div></div>}

        <form className="assistant-composer" onSubmit={(event) => void sendMessage(event)}>
          <label><span className="sr-only">Message the EPM Assistant</span><textarea ref={composer} rows={2} maxLength={4000} value={content} disabled={!status?.enabled || sending || Boolean(approval) || Boolean(clarification) || Boolean(inputRequest)} onChange={(event) => setContent(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void sendMessage(); } }} placeholder={inputRequest ? "Complete the guided inputs above to continue." : clarification ? "Choose an Oracle artifact above to continue." : approval ? "Approve or reject the proposal above to continue." : "Ask about Planning, recent activity, or prepare a governed action…"} /></label>
          <button type="submit" className="button button--primary" disabled={!content.trim() || sending || Boolean(approval) || Boolean(clarification) || Boolean(inputRequest) || !status?.enabled}>{sending ? <><span className="spinner" /> Thinking…</> : <>Send <Icon name="arrow" /></>}</button>
        </form>
        <p className="assistant-disclaimer">AI responses can be inaccurate. Verify the exact Oracle artifact and run inputs before approving any operation.</p>
      </section>
    </div>
    {confirmDelete && <ConfirmationDialog title="Delete this conversation?" description="The conversation and its prepared action drafts will be removed from your workspace." warning="This cannot be undone. Oracle EPM data and completed execution history are not affected." confirmLabel="Delete conversation" tone="danger" busy={deleting} error={error} onConfirm={deleteConversation} onClose={() => { if (!deleting) setConfirmDelete(false); }} />}
  </section>;
}

function MessageBubble({ message }: { message: AgentMessage }) {
  const assistant = message.role === "assistant";
  return <article className={`assistant-message assistant-message--${message.role}`}><span className="assistant-avatar">{assistant ? <Icon name="assistant" /> : "You"}</span><div><span className="eyebrow">{assistant ? "EPM Assistant" : "You"}</span>{assistant ? <MarkdownContent content={message.content} /> : <p>{message.content}</p>}<time>{formatMessageTime(message.created_at)}</time></div></article>;
}

interface AgentGridReviewResult {
  cube: string;
  name: string;
  row_count: number;
  column_count: number;
  cell_count: number;
  missing_cell_count: number;
  returned_row_count: number;
  truncated: boolean;
  request: DataReviewSliceInput;
  grid: DataReviewGrid;
}

interface AgentComparisonResult {
  source_cube: string;
  target_cube: string;
  source_request: DataReviewSliceInput;
  target_request: DataReviewSliceInput;
  result: {
    source_form: string;
    target_form: string;
    compared_cells: number;
    matched_cells: number;
    mismatches: DataComparisonMismatch[];
    tolerance: number | string;
  };
}

function AgentDataReviewCard({ activity, csrfToken, onPrompt, onPrepare, busy }: {
  activity: AgentToolActivity;
  csrfToken: string;
  onPrompt: (prompt: string) => void;
  onPrepare: (prompt: string) => void;
  busy: boolean;
}) {
  if (activity.name === "plan_multi_step_request" && isAgentMultiStepPlan(activity.result)) {
    return <AgentMultiStepPlanCard plan={activity.result} busy={busy} onPrepare={onPrepare} />;
  }
  if (activity.name === "list_cube_dimensions" && activity.status === "FAILED") {
    return <AgentDimensionCompatibilityCard activity={activity} onPrompt={onPrompt} />;
  }
  if (activity.name === "review_data_slice" && activity.status === "FAILED") {
    return <AgentDataReviewFailureCard activity={activity} />;
  }
  if (["list_planning_cubes", "list_cube_dimensions", "search_dimension_members"].includes(activity.name) && activity.result) {
    return <AgentMetadataChoiceCard activity={activity} onPrompt={onPrompt} />;
  }
  if (activity.name === "review_data_slice" && isAgentGridReview(activity.result)) {
    return <AgentGridReviewCard review={activity.result} csrfToken={csrfToken} onPrompt={onPrompt} />;
  }
  if (activity.name === "compare_data_slices" && isAgentComparison(activity.result)) {
    return <AgentComparisonCard comparison={activity.result} csrfToken={csrfToken} onPrompt={onPrompt} />;
  }
  return null;
}

interface AgentMultiStepPlanStep {
  sequence: number;
  code: string;
  display_name: string;
  category: string;
}

interface AgentMultiStepPipeline {
  code: string;
  display_name: string;
  coverage: number;
  stages: PipelineStagePreview[];
  variables: PipelineVariablePreview[];
  file_requirements: PipelineFilePreview[];
}

interface AgentMultiStepPlan {
  objective: string;
  requested_steps: AgentMultiStepPlanStep[];
  resolution: "ORACLE_PIPELINE" | "STANDALONE_FLOW_DRAFT" | "NON_EXECUTABLE_PLAN";
  executable: boolean;
  pipeline: AgentMultiStepPipeline | null;
  confidence: number;
  candidate_count: number;
  inspection_errors?: string[];
  message: string;
}

function AgentMultiStepPlanCard({ plan, busy, onPrepare }: {
  plan: AgentMultiStepPlan;
  busy: boolean;
  onPrepare: (prompt: string) => void;
}) {
  const pipeline = plan.pipeline;
  const canContinue = plan.executable && Boolean(pipeline);
  const standaloneDraft = plan.resolution === "STANDALONE_FLOW_DRAFT";
  const preparationPrompt = pipeline
    ? `Prepare and run Oracle Pipeline ${pipeline.code} for this multi-step objective: ${plan.objective}`
    : "Show me the registered Oracle Pipelines so I can identify one for this multi-step request.";
  const standalonePrompt = `Configure and execute a standalone flow without an Oracle Pipeline for these operations: ${plan.requested_steps.map((step) => step.display_name).join(" -> ")}. Objective: ${plan.objective}`;
  return <article className={`assistant-multi-plan${canContinue ? " is-matched" : " is-design-only"}`} aria-label="Multi-step Planning process">
    <header><span className="assistant-guided-input__icon"><Icon name={canContinue || standaloneDraft ? "check" : "alert"} /></span><div><span className="eyebrow">{canContinue ? "Oracle Pipeline match" : standaloneDraft ? "Standalone flow draft" : "Design review only"}</span><h3>{pipeline ? pipeline.display_name : standaloneDraft ? "Platform-managed sequence" : "No safe executable match"}</h3><p>{plan.message}</p></div>{canContinue && <span className="assistant-multi-plan__badge">One governed run</span>}</header>
    <section className="assistant-multi-plan__request"><strong>Requested business outcome</strong><p>{plan.objective}</p></section>
    <div className="assistant-multi-plan__flow">
      {plan.requested_steps.map((step) => <div key={`${step.sequence}-${step.code}`}><span>{String(step.sequence).padStart(2, "0")}</span><div><strong>{step.display_name}</strong><small>{step.category}</small></div></div>)}
    </div>
    {pipeline ? <>
      <section className="assistant-multi-plan__pipeline"><header><div><span className="eyebrow">Live Oracle definition</span><h4>{pipeline.display_name} <small>{pipeline.code}</small></h4></div><span>{Math.round(plan.confidence * 100)}% match</span></header><div>{pipeline.stages.length ? pipeline.stages.map((stage, index) => <div key={`${stage.name}-${index}`}><span>{index + 1}</span><div><strong>{stage.display_name}</strong><small>{stage.job_count} configured job{stage.job_count === 1 ? "" : "s"}{stage.runs_in_parallel ? " · Parallel" : ""}</small></div></div>) : <p>Oracle returned no visible stage labels. The governed preflight will verify the definition again.</p>}</div></section>
      <div className="assistant-multi-plan__facts"><span><small>Runtime variables</small><strong>{pipeline.variables.length}</strong></span><span><small>File requirements</small><strong>{pipeline.file_requirements.length}</strong></span><span><small>Registered candidates checked</small><strong>{plan.candidate_count}</strong></span></div>
      <p className="assistant-multi-plan__guard"><Icon name="check" /> Oracle owns these stages. The platform will not recreate or execute them individually.</p>
    </> : <p className="assistant-multi-plan__guard is-warning"><Icon name="alert" /> Nothing can run from this plan. Configure the lifecycle in Oracle Pipeline, or identify its exact registered Pipeline code.</p>}
    <footer>{pipeline ? <><button type="button" className="button button--secondary" disabled={busy} onClick={() => onPrepare(standalonePrompt)}>Configure standalone flow</button><button type="button" className="button button--primary" disabled={busy} onClick={() => onPrepare(preparationPrompt)}>{busy ? <><span className="spinner" /> Preparing…</> : <>Review Pipeline inputs <Icon name="arrow" /></>}</button></> : standaloneDraft ? <button type="button" className="button button--primary" disabled={busy} onClick={() => onPrepare(standalonePrompt)}>{busy ? <><span className="spinner" /> Configuring…</> : <>Configure flow inputs <Icon name="arrow" /></>}</button> : <><button type="button" className="button button--secondary" disabled={busy} onClick={() => onPrepare(standalonePrompt)}>Configure standalone flow</button><button type="button" className="button button--quiet" disabled={busy} onClick={() => onPrepare(preparationPrompt)}>Review registered Pipelines</button></>}</footer>
  </article>;
}

function isAgentMultiStepPlan(value: unknown): value is AgentMultiStepPlan {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return typeof item.objective === "string"
    && Array.isArray(item.requested_steps)
    && typeof item.executable === "boolean"
    && typeof item.message === "string";
}

function AgentDimensionCompatibilityCard({ activity, onPrompt }: {
  activity: AgentToolActivity;
  onPrompt: (prompt: string) => void;
}) {
  const cube = String(activity.arguments?.cube ?? "the selected cube");
  const [pov, setPov] = useState("");
  const [rows, setRows] = useState("");
  const [columns, setColumns] = useState("");
  const canContinue = exactReviewMappingsValid(pov, false)
    && exactReviewMappingsValid(rows, true)
    && exactReviewMappingsValid(columns, true);

  function continueWithExactLayout() {
    if (!canContinue) return;
    onPrompt(exactDataReviewPrompt(cube, pov, rows, columns));
  }

  function openDataReview() {
    window.sessionStorage.setItem(
      "bisp-epm-agent-data-review-cube",
      cube
    );
    window.location.hash = "#data-review";
  }

  return <article className="assistant-data-choice assistant-dimension-fallback">
    <header><span className="assistant-guided-input__icon"><Icon name="settings" /></span><div><span className="eyebrow">Exact-layout compatibility</span><h3>{cube} remains selected</h3><p>Oracle did not expose its member catalog for this cube. Map every exact dimension to the member or members you want. The platform will send this structure directly to the read-only Data Review tool without asking the AI to reinterpret it.</p></div></header>
    <div className="assistant-dimension-fallback__example"><Icon name="data" /><p><strong>Use Dimension=Member.</strong> The left side is the dimension, such as <code>Scenario</code>; the right side is its member, such as <code>Actual</code>. Separate multiple row or column members with <code>|</code>.</p></div>
    <div className="assistant-dimension-fallback__fields">
      <label className="is-pov"><span>POV mappings *</span><textarea aria-label="POV mappings" rows={5} value={pov} onChange={(event) => setPov(event.target.value)} placeholder={"Scenario=Actual\nVersion=Working\nEntity=No Entity\nYear=FY24"} /><small>One exact Dimension=Member mapping per line. POV dimensions accept one member each.</small></label>
      <label><span>Row mappings *</span><textarea aria-label="Row mappings" rows={4} value={rows} onChange={(event) => setRows(event.target.value)} placeholder="Account=Units|Average_Selling_Price|Total_Revenue" /><small>One dimension per line; separate multiple members with |.</small></label>
      <label><span>Column mappings *</span><textarea aria-label="Column mappings" rows={4} value={columns} onChange={(event) => setColumns(event.target.value)} placeholder="Period=Jan|Feb|Mar" /><small>One dimension per line; separate multiple members with |.</small></label>
    </div>
    {!canContinue && (pov || rows || columns) && <p className="assistant-dimension-fallback__validation"><Icon name="alert" /> Every line must use Dimension=Member. Rows and columns may use Member1|Member2.</p>}
    <footer><button type="button" className="button button--quiet" onClick={openDataReview}>Open Data Review</button><button type="button" className="button button--primary" disabled={!canContinue} onClick={continueWithExactLayout}>Use this exact layout <Icon name="arrow" /></button></footer>
  </article>;
}

function AgentDataReviewFailureCard({ activity }: { activity: AgentToolActivity }) {
  const cube = String(activity.arguments?.cube ?? "the selected cube");
  const error = String(activity.result?.error ?? "Oracle could not load this exact slice.");

  function openDataReview() {
    window.sessionStorage.setItem("bisp-epm-agent-data-review-cube", cube);
    window.location.hash = "#data-review";
  }

  return <article className="assistant-data-choice assistant-data-review-failure">
    <header><span className="assistant-guided-input__icon"><Icon name="alert" /></span><div><span className="eyebrow">Exact slice needs correction</span><h3>{cube} layout was not accepted</h3><p>The request reached the read-only Data Review service. Correct the exact Oracle dimension or member named in the error, then try the mapping card again.</p></div></header>
    <div className="assistant-draft__error"><Icon name="alert" />{error}</div>
    <footer><button type="button" className="button button--secondary" onClick={openDataReview}>Open Data Review</button></footer>
  </article>;
}

function exactReviewMappingsValid(value: string, multipleMembers: boolean) {
  const lines = value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  return Boolean(lines.length) && lines.every((line) => {
    const separator = line.indexOf("=");
    if (separator <= 0 || separator === line.length - 1) return false;
    const dimension = line.slice(0, separator).trim();
    const members = line.slice(separator + 1).trim();
    if (!dimension || !members) return false;
    if (!multipleMembers) return !members.includes("|");
    return members.split("|").every((member) => Boolean(member.trim()));
  });
}

function exactDataReviewPrompt(cube: string, pov: string, rows: string, columns: string) {
  return [
    "Run an exact Data Review using this validated layout.",
    `Cube: ${cube}`,
    "POV:",
    pov.trim(),
    "Rows:",
    rows.trim(),
    "Columns:",
    columns.trim()
  ].join("\n");
}

function AgentMetadataChoiceCard({ activity, onPrompt }: {
  activity: AgentToolActivity;
  onPrompt: (prompt: string) => void;
}) {
  const result = activity.result ?? {};
  if (activity.name === "list_planning_cubes") {
    const cubes = recordList(result.cubes);
    if (!cubes.length) return null;
    return <article className="assistant-data-choice"><header><span className="assistant-guided-input__icon"><Icon name="data" /></span><div><span className="eyebrow">Choose a live cube</span><h3>Where should I review data?</h3><p>These plan types were retrieved from the connected Planning application.</p></div></header><div className="assistant-data-choice__options">{cubes.map((cube) => { const name = String(cube.name ?? cube.cube_name ?? ""); return <button type="button" onClick={() => onPrompt(`Use cube ${name} for this data review and help me choose the remaining POV, rows, and columns.`)} key={name}><Icon name="data" /><span><strong>{name}</strong><small>{cube.dimension_count ? `${cube.dimension_count} dimensions` : "Live Planning cube"}</small></span><Icon name="arrow" /></button>; })}</div></article>;
  }
  if (activity.name === "list_cube_dimensions") {
    const cube = String(result.cube ?? "the selected cube");
    const dimensions = recordList(result.dimensions);
    if (!dimensions.length) return null;
    return <article className="assistant-data-choice"><header><span className="assistant-guided-input__icon"><Icon name="settings" /></span><div><span className="eyebrow">Build the grid</span><h3>Place a dimension</h3><p>Choose where a live {cube} dimension belongs. The agent will preserve the choice and continue collecting the layout.</p></div></header><div className="assistant-dimension-choices">{dimensions.map((dimension) => { const name = String(dimension.name ?? ""); return <div key={name}><strong>{name}</strong><span><button type="button" onClick={() => onPrompt(`Place ${name} in the POV for the ${cube} data review.`)}>POV</button><button type="button" onClick={() => onPrompt(`Place ${name} on rows for the ${cube} data review.`)}>Rows</button><button type="button" onClick={() => onPrompt(`Place ${name} on columns for the ${cube} data review.`)}>Columns</button></span></div>; })}</div></article>;
  }
  const cube = String(result.cube ?? "the selected cube");
  const dimension = String(result.dimension ?? "dimension");
  const members = recordList(result.members);
  if (!members.length) return null;
  return <article className="assistant-data-choice"><header><span className="assistant-guided-input__icon"><Icon name="search" /></span><div><span className="eyebrow">Choose live members</span><h3>{dimension}</h3><p>Select a member retrieved from {cube}; you can continue adding members conversationally.</p></div></header><div className="assistant-data-choice__options">{members.slice(0, 40).map((member) => { const name = String(member.name ?? ""); const alias = String(member.alias ?? ""); return <button type="button" onClick={() => onPrompt(`Use member ${name} for dimension ${dimension} in the previous ${cube} data review.`)} key={name}><Icon name="check" /><span><strong>{alias || name}</strong>{alias && alias !== name && <small>{name}</small>}</span><Icon name="arrow" /></button>; })}</div></article>;
}

function AgentReviewResumeCard({ context, onPrompt }: {
  context: AgentDataReviewContext;
  onPrompt: (prompt: string) => void;
}) {
  const slice = dataReviewSliceFromContext(context);
  if (!slice) return null;
  const pov = Object.entries(slice.pov);
  const comparison = context.tool === "compare_data_slices";

  function openWorkspace() {
    window.sessionStorage.setItem(
      AGENT_DATA_REVIEW_HANDOFF_KEY,
      JSON.stringify(slice)
    );
    window.location.hash = "#data-review";
  }

  return <article className="assistant-review-resume"><span className="assistant-review-resume__icon"><Icon name="data" /></span><div><span className="eyebrow">Validated review context</span><h3>Continue {comparison ? "the comparison" : `${slice.cube} data review`}</h3><p>The prior financial values were not stored. The exact cube intersection is available to query live again.</p><div>{pov.slice(0, 5).map(([dimension, member]) => <span key={dimension}>{dimension}: <strong>{member}</strong></span>)}<span>Rows: <strong>{slice.rows.map((item) => item.dimension).join(", ")}</strong></span><span>Columns: <strong>{slice.columns.map((item) => item.dimension).join(", ")}</strong></span></div></div><footer><button type="button" className="button button--secondary" onClick={() => onPrompt("Show the previous data review again using live Oracle data.")}>Reload live data</button><button type="button" className="button button--quiet" onClick={() => onPrompt("Compare the previous data review with ")}>Compare</button><button type="button" className="button button--quiet" onClick={openWorkspace}>Open Data Review <Icon name="arrow" /></button></footer></article>;
}

function AgentGridReviewCard({ review, csrfToken, onPrompt }: {
  review: AgentGridReviewResult;
  csrfToken: string;
  onPrompt: (prompt: string) => void;
}) {
  const [page, setPage] = useState(1);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pageSize = 20;
  const totalPages = Math.max(1, Math.ceil(review.grid.rows.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const rows = review.grid.rows.slice((safePage - 1) * pageSize, safePage * pageSize);

  async function exportExcel() {
    setExporting(true);
    setError(null);
    try {
      const blob = await api.exportDataReviewGrid(review.request, csrfToken);
      downloadAgentBlob(blob, `${safeAgentFilename(review.cube)}-data-review.xlsx`);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setExporting(false);
    }
  }

  function openWorkspace() {
    window.sessionStorage.setItem(
      AGENT_DATA_REVIEW_HANDOFF_KEY,
      JSON.stringify(review.request)
    );
    window.location.hash = "#data-review";
  }

  return <article className="assistant-data-review" aria-label="Live Planning data review">
    <header><div><span className="eyebrow">Live Oracle data</span><h3>{review.cube} data review</h3><p>The grid below is read-only and uses the exact validated intersection selected in this conversation.</p></div><span className="read-only-badge"><Icon name="check" /> Read-only</span></header>
    <div className="assistant-data-review__pov">{review.grid.pov.map(([dimension, member]) => <span key={dimension}><small>{dimension}</small><strong>{member}</strong></span>)}</div>
    <div className="assistant-data-review__metrics"><span><small>Rows</small><strong>{review.row_count.toLocaleString()}</strong></span><span><small>Columns</small><strong>{review.column_count.toLocaleString()}</strong></span><span><small>Cells</small><strong>{review.cell_count.toLocaleString()}</strong></span><span className={review.missing_cell_count ? "has-warning" : ""}><small>Missing</small><strong>{review.missing_cell_count.toLocaleString()}</strong></span></div>
    <div className="assistant-data-review__table"><table><thead><tr>{review.grid.row_dimensions.map((dimension) => <th key={dimension}>{dimension}</th>)}{review.grid.columns.map((column, index) => <th key={index}>{column.join(" · ") || `Column ${index + 1}`}</th>)}</tr></thead><tbody>{rows.map((row, rowIndex) => <tr key={`${row.headers.join("|")}-${rowIndex}`}>{row.headers.map((header, index) => <th key={`${header}-${index}`}>{header}</th>)}{row.data.map((value, index) => <td className={isAgentMissing(value) ? "is-missing" : ""} key={index}>{formatAgentValue(value)}</td>)}</tr>)}</tbody></table></div>
    {review.truncated && <p className="assistant-data-review__notice"><Icon name="alert" /> The conversation preview is limited to {review.returned_row_count.toLocaleString()} rows. Excel export and the full Data Review workspace use the complete live slice.</p>}
    {review.grid.rows.length > pageSize && <div className="assistant-data-review__pagination"><button type="button" className="button button--quiet" disabled={safePage === 1} onClick={() => setPage(safePage - 1)}>Previous</button><span>Page {safePage} of {totalPages}</span><button type="button" className="button button--quiet" disabled={safePage === totalPages} onClick={() => setPage(safePage + 1)}>Next</button></div>}
    {error && <div className="assistant-draft__error"><Icon name="alert" />{error}</div>}
    <div className="assistant-data-review__quick"><span>Continue with this intersection</span><button type="button" onClick={() => onPrompt("Change the previous data review POV to ")}>Change POV</button><button type="button" onClick={() => onPrompt("Add the following members to the previous data review: ")}>Add members</button><button type="button" onClick={() => onPrompt("Compare the previous data review with ")}>Compare this</button></div>
    <footer><button type="button" className="button button--quiet" onClick={openWorkspace}>Open Data Review <Icon name="arrow" /></button><button type="button" className="button button--primary" disabled={exporting} onClick={() => void exportExcel()}>{exporting ? <><span className="spinner" /> Creating Excel...</> : <><Icon name="reports" /> Export Excel</>}</button></footer>
  </article>;
}

function AgentComparisonCard({ comparison, csrfToken, onPrompt }: {
  comparison: AgentComparisonResult;
  csrfToken: string;
  onPrompt: (prompt: string) => void;
}) {
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mismatches = comparison.result.mismatches ?? [];
  const mismatchCount = Math.max(0, comparison.result.compared_cells - comparison.result.matched_cells);

  async function exportExcel() {
    setExporting(true);
    setError(null);
    try {
      const blob = await api.exportDataReviewComparison({
        source: comparison.source_request,
        target: comparison.target_request,
        tolerance: Number(comparison.result.tolerance),
        max_mismatches: Math.min(500, Math.max(100, mismatches.length)),
        include_cells: false
      }, csrfToken);
      downloadAgentBlob(blob, `${safeAgentFilename(comparison.source_cube)}-to-${safeAgentFilename(comparison.target_cube)}.xlsx`);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setExporting(false);
    }
  }

  return <article className="assistant-data-review assistant-data-comparison" aria-label="Planning source target comparison">
    <header><div><span className="eyebrow">Live reconciliation</span><h3>{comparison.source_cube} compared with {comparison.target_cube}</h3><p>Source and target were read from Oracle using the reviewed intersections.</p></div><span className={`assistant-comparison-status${mismatchCount ? " has-warning" : " is-match"}`}>{mismatchCount ? "Review required" : "All matched"}</span></header>
    <div className="assistant-data-review__metrics"><span><small>Compared</small><strong>{comparison.result.compared_cells.toLocaleString()}</strong></span><span><small>Matched</small><strong>{comparison.result.matched_cells.toLocaleString()}</strong></span><span className={mismatchCount ? "has-warning" : ""}><small>Different</small><strong>{mismatchCount.toLocaleString()}</strong></span><span><small>Tolerance</small><strong>{formatAgentValue(comparison.result.tolerance)}</strong></span></div>
    {mismatches.length ? <div className="assistant-data-review__table"><table><thead><tr><th>Row intersection</th><th>Column intersection</th><th>Source</th><th>Target</th><th>Difference</th></tr></thead><tbody>{mismatches.slice(0, 100).map((item, index) => <tr key={`${item.row_headers.join("|")}-${item.column_headers.join("|")}-${index}`}><th>{item.row_headers.join(" · ")}</th><th>{item.column_headers.join(" · ")}</th><td>{formatAgentValue(item.source_value)}</td><td>{formatAgentValue(item.target_value)}</td><td className="is-difference">{formatAgentValue(item.difference)}</td></tr>)}</tbody></table></div> : <div className="assistant-data-review__matched"><Icon name="check" /><div><strong>No differences found</strong><p>Every compared cell matched within the selected tolerance.</p></div></div>}
    {error && <div className="assistant-draft__error"><Icon name="alert" />{error}</div>}
    <footer><button type="button" className="button button--secondary" onClick={() => onPrompt("Refine the previous source-target comparison by changing ")}>Refine comparison</button><button type="button" className="button button--primary" disabled={exporting} onClick={() => void exportExcel()}>{exporting ? <><span className="spinner" /> Creating Excel...</> : <><Icon name="reports" /> Export comparison</>}</button></footer>
  </article>;
}

function ClarificationCard({ clarification, busy, onSubmit, onSynchronize, onRegister }: {
  clarification: AgentClarificationRequest;
  busy: boolean;
  onSubmit: (value: string | null) => Promise<void>;
  onSynchronize: () => Promise<boolean>;
  onRegister: (identifier: string) => Promise<boolean>;
}) {
  const [selected, setSelected] = useState("");
  const [search, setSearch] = useState("");
  const [showRegistration, setShowRegistration] = useState(false);
  const [identifier, setIdentifier] = useState("");
  const [synchronized, setSynchronized] = useState(false);
  useEffect(() => {
    setSelected("");
    setSearch("");
    setShowRegistration(false);
    setIdentifier("");
    setSynchronized(false);
  }, [clarification.request_id]);
  const optionLabels = clarification.option_labels ?? {};
  const currentOptions = new Set(clarification.options);
  const recommendations = (clarification.recommendations ?? []).filter((item) =>
    currentOptions.has(item.name)
  );
  const recovery = clarification.catalog_recovery?.enabled ? clarification.catalog_recovery : null;
  const matchingOptions = clarification.options.filter((option) =>
    `${option} ${optionLabels[option] ?? ""}`.toLowerCase().includes(search.trim().toLowerCase())
  );
  const visibleOptions = selected && currentOptions.has(selected) && !matchingOptions.includes(selected)
    ? [selected, ...matchingOptions]
    : matchingOptions;
  return <article className="assistant-clarification" aria-label="Choose an Oracle artifact">
    <header><span className="assistant-clarification__icon"><Icon name="assistant" /></span><div><span className="eyebrow">One choice needed</span><h3>{recommendations.length ? "Matches found for your task" : recovery && !clarification.options.length ? "No registered match was found" : clarification.prompt}</h3><p>{recommendations.length ? `I compared your request with ${clarification.options.length} current Oracle ${clarification.display_name}. Select a recommendation or choose from the complete list.` : recovery && !clarification.options.length ? "Synchronize the current environment or register an exact Oracle identifier to continue." : "These options were retrieved for the connected Planning application."}</p></div></header>
    {recommendations.length > 0 && <section className="assistant-rule-matches" aria-label={`Recommended ${clarification.display_name}`}><div className="assistant-rule-matches__heading"><strong>Recommended matches</strong><small>Suggestions are based on matching words in your task and Oracle artifact names.</small></div><div>{recommendations.map((item) => <button type="button" className={selected === item.name ? "is-selected" : ""} disabled={busy} onClick={() => { setSearch(""); setSelected(item.name); }} key={item.name}><span><strong>{item.display_name || optionLabels[item.name] || item.name}</strong>{(item.display_name || optionLabels[item.name]) && (item.display_name || optionLabels[item.name]) !== item.name && <em className="artifact-code">{item.name}</em>}<small>{item.reason}</small></span><em className={item.confidence === "Strong match" ? "is-strong" : "is-possible"}>{item.confidence}</em></button>)}</div></section>}
    {clarification.options.length > 0 && <section className="assistant-all-artifacts"><div><strong>Current registered {clarification.display_name}</strong><small>Only artifacts valid for the connected Planning environment are shown.</small></div>{clarification.options.length > 12 && <label><span>Search artifacts</span><input type="search" value={search} disabled={busy} onChange={(event) => setSearch(event.target.value)} placeholder={`Search current ${clarification.display_name}`} /></label>}<label><span>{clarification.display_name} artifact</span><select aria-label={`${clarification.display_name} artifact`} value={selected} disabled={busy} onChange={(event) => setSelected(event.target.value)}><option value="">Select from {visibleOptions.length} available option{visibleOptions.length === 1 ? "" : "s"}</option>{visibleOptions.map((option) => <option value={option} key={option}>{optionLabels[option] && optionLabels[option] !== option ? `${optionLabels[option]} · ${option}` : option}</option>)}</select></label></section>}
    {recovery && <section className="assistant-catalog-recovery">
      <div className="assistant-catalog-recovery__heading"><div><strong>Can't find the right artifact?</strong><small>First synchronize what Oracle can safely expose. If it is still missing, use its exact identifier.</small></div>{recovery.can_manage !== false && <button type="button" className="button button--secondary" disabled={busy} onClick={async () => setSynchronized(await onSynchronize())}>{busy ? <span className="spinner" /> : <Icon name="refresh" />} Synchronize with Oracle</button>}</div>
      {synchronized && <p className="assistant-catalog-recovery__success"><Icon name="check" /> Catalog synchronized. Review the refreshed matches and list above.</p>}
      {recovery.can_manage === false ? <p className="assistant-catalog-recovery__notice"><Icon name="alert" /> Ask a Service Administrator to synchronize or register this artifact. You can still choose any current option above.</p> : <><button type="button" className="assistant-catalog-recovery__toggle" aria-expanded={showRegistration} onClick={() => setShowRegistration((current) => !current)}>{showRegistration ? "Hide exact registration" : "Register an exact identifier"} <Icon name="arrow" /></button>{showRegistration && <div className="assistant-catalog-registration"><label><span>{recovery.identifier_label}</span><input value={identifier} disabled={busy} maxLength={500} onChange={(event) => setIdentifier(event.target.value)} placeholder={recovery.identifier_placeholder} /></label><p>{recovery.help}</p><button type="button" className="button button--primary" disabled={busy || !identifier.trim()} onClick={() => void onRegister(identifier.trim())}>{busy ? <><span className="spinner" /> Checking…</> : recovery.registration_mode === "verified" ? <>Verify and continue <Icon name="arrow" /></> : <>Register pending and continue <Icon name="arrow" /></>}</button></div>}</>}
    </section>}
    <footer>{clarification.allows_cancel && <button type="button" className="button button--secondary" disabled={busy} onClick={() => void onSubmit(null)}>Cancel proposal</button>}<button type="button" className="button button--primary" disabled={busy || !selected} onClick={() => void onSubmit(selected)}>{busy ? <><span className="spinner" /> Continuing…</> : <>Continue <Icon name="arrow" /></>}</button></footer>
  </article>;
}

interface GuidedPair { id: number; name: string; value: string }

function GuidedInputCard({ request, busy, csrfToken, onSubmit }: {
  request: AgentInputRequest;
  busy: boolean;
  csrfToken: string;
  onSubmit: (values: Record<string, unknown> | null) => Promise<void>;
}) {
  const flowStep = request.context?.flow_step as { sequence?: number; total?: number } | undefined;
  let card: ReactNode;
  if (request.operation_code === "substitution-variables") card = <SubstitutionVariableGuidedInputCard request={request} busy={busy} onSubmit={onSubmit} />;
  else if (request.operation_code === "user-variables") card = <UserVariableGuidedInputCard request={request} busy={busy} onSubmit={onSubmit} />;
  else if (request.operation_code === "data-maps") card = <DataMapGuidedInputCard request={request} busy={busy} onSubmit={onSubmit} />;
  else if (request.operation_code === "pipelines") card = <PipelineGuidedInputCard request={request} busy={busy} csrfToken={csrfToken} onSubmit={onSubmit} />;
  else if (request.operation_code === "data-integrations") card = <DataIntegrationGuidedInputCard request={request} busy={busy} csrfToken={csrfToken} onSubmit={onSubmit} />;
  else if (request.operation_code === "data-import") card = <DataImportGuidedInputCard request={request} busy={busy} csrfToken={csrfToken} onSubmit={onSubmit} />;
  else if (request.operation_code === "metadata-import") card = <MetadataImportGuidedInputCard request={request} busy={busy} csrfToken={csrfToken} onSubmit={onSubmit} />;
  else card = <BusinessRuleGuidedInputCard request={request} busy={busy} onSubmit={onSubmit} />;
  return <>{flowStep?.sequence && flowStep?.total && <div className="assistant-flow-progress"><span>Standalone flow</span><strong>Configure step {flowStep.sequence} of {flowStep.total}</strong><progress value={flowStep.sequence} max={flowStep.total} /></div>}{card}</>;
}

function UserVariableGuidedInputCard({ request, busy, onSubmit }: {
  request: AgentInputRequest;
  busy: boolean;
  onSubmit: (values: Record<string, unknown> | null) => Promise<void>;
}) {
  const context = request.context as {
    variable_name?: string;
    dimension?: string;
    default_user?: string;
    can_manage_users?: boolean;
    prefill?: { new_member?: string };
  };
  const [userName, setUserName] = useState(context.default_user ?? "");
  const [member, setMember] = useState(context.prefill?.new_member ?? "");
  useEffect(() => {
    setUserName(context.default_user ?? "");
    setMember(context.prefill?.new_member ?? "");
  }, [request.request_id]);
  return <article className="assistant-guided-input assistant-variable-input" aria-label="User Variable inputs">
    <header><span className="assistant-guided-input__icon"><Icon name="settings" /></span><div><span className="eyebrow">Review live Oracle context</span><h3>{request.title}</h3><p>{request.description}</p></div></header>
    <section className="assistant-variable-input__current"><div><small>User variable</small><strong>{context.variable_name || request.artifact_name}</strong></div><div><small>Dimension</small><strong>{context.dimension}</strong></div></section>
    <section className="assistant-variable-input__fields"><label className="runner-field"><span>Oracle user *</span><input aria-label="Agent Oracle user" value={userName} disabled={busy || !context.can_manage_users} onChange={(event) => setUserName(event.target.value)} /><small>{context.can_manage_users ? "You may target another exact Oracle user." : "Your role can update only your own assignment."}</small></label><label className="runner-field"><span>New {context.dimension || "dimension"} member *</span><input aria-label="Agent user variable new member" value={member} disabled={busy} maxLength={255} onChange={(event) => setMember(event.target.value)} placeholder="Enter the exact Planning member" /></label></section>
    <div className="assistant-variable-input__safety"><Icon name="check" /><p><strong>Protected update.</strong> The current Oracle assignment is read after you continue and checked again during execution.</p></div>
    <footer><button type="button" className="button button--secondary" disabled={busy} onClick={() => void onSubmit(null)}>Cancel</button><button type="button" className="button button--primary" disabled={busy || !userName.trim() || !member.trim()} onClick={() => void onSubmit({ user_name: userName.trim(), new_member: member.trim() })}>Continue to approval <Icon name="arrow" /></button></footer>
  </article>;
}

function SubstitutionVariableGuidedInputCard({ request, busy, onSubmit }: {
  request: AgentInputRequest;
  busy: boolean;
  onSubmit: (values: Record<string, unknown> | null) => Promise<void>;
}) {
  const context = request.context as {
    action?: "UPDATE" | "CREATE";
    scope?: string;
    variable_name?: string;
    current_value?: string;
    scopes?: string[];
    prefill?: { scope?: string; variable_name?: string; new_value?: string };
  };
  const creating = context.action === "CREATE";
  const scopes = context.scopes?.length ? context.scopes : ["ALL"];
  const [scope, setScope] = useState(context.prefill?.scope ?? scopes[0] ?? "ALL");
  const [variableName, setVariableName] = useState(context.prefill?.variable_name ?? "");
  const [newValue, setNewValue] = useState(context.prefill?.new_value ?? "");
  useEffect(() => {
    setScope(context.prefill?.scope ?? scopes[0] ?? "ALL");
    setVariableName(context.prefill?.variable_name ?? "");
    setNewValue(context.prefill?.new_value ?? "");
  }, [request.request_id]);
  const ready = Boolean(newValue.trim() && (!creating || variableName.trim() && scope));
  return <article className="assistant-guided-input assistant-variable-input" aria-label="Substitution Variable inputs">
    <header><span className="assistant-guided-input__icon"><Icon name="settings" /></span><div><span className="eyebrow">Review live Oracle context</span><h3>{request.title}</h3><p>{request.description}</p></div></header>
    {!creating && <section className="assistant-variable-input__current"><div><small>Variable</small><strong>{context.variable_name}</strong></div><div><small>Scope</small><strong>{context.scope}</strong></div><div><small>Current Oracle value</small><strong>{context.current_value || "Empty"}</strong></div></section>}
    {creating && <section className="assistant-variable-input__fields"><label className="runner-field"><span>Scope *</span><select aria-label="Agent variable scope" value={scope} disabled={busy} onChange={(event) => setScope(event.target.value)}>{scopes.map((item) => <option key={item}>{item}</option>)}</select><small>ALL is application-wide; other choices are live Planning cubes.</small></label><label className="runner-field"><span>Variable name *</span><input aria-label="Agent variable name" value={variableName} disabled={busy} maxLength={80} onChange={(event) => setVariableName(event.target.value)} placeholder="CurYr" /><small>Use the exact name expected by Oracle artifacts.</small></label></section>}
    <label className="runner-field"><span>{creating ? "Initial value" : "New value"} *</span><input aria-label="Agent substitution variable new value" value={newValue} disabled={busy} maxLength={255} onChange={(event) => setNewValue(event.target.value)} placeholder={creating ? "FY27" : context.current_value || "Enter the replacement value"} /><small>Enter the exact Planning member or text value.</small></label>
    {!creating && <div className="assistant-variable-input__safety"><Icon name="check" /><p><strong>Protected update.</strong> The assistant captured the current Oracle value. Approval will fail safely if another user changes it first.</p></div>}
    <footer><button type="button" className="button button--secondary" disabled={busy} onClick={() => void onSubmit(null)}>Cancel</button><button type="button" className="button button--primary" disabled={busy || !ready} onClick={() => void onSubmit(creating ? { scope, variable_name: variableName.trim(), new_value: newValue.trim() } : { new_value: newValue.trim() })}>{busy ? <><span className="spinner" /> Validating…</> : <>Continue to approval <Icon name="arrow" /></>}</button></footer>
  </article>;
}

type AgentIntegrationPeriodMode = "mapped" | "planning" | "exact";
type AgentIntegrationFileSource = "configured" | "upload" | "inbox";
const AGENT_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const AGENT_YEARS = Array.from({ length: 21 }, (_, index) => `FY${String(20 + index).padStart(2, "0")}`);

function DataImportGuidedInputCard({ request, busy, csrfToken, onSubmit }: {
  request: AgentInputRequest;
  busy: boolean;
  csrfToken: string;
  onSubmit: (values: Record<string, unknown> | null) => Promise<void>;
}) {
  const context = request.context as { allowed_extensions?: string[] };
  const extensions = context.allowed_extensions ?? [".csv", ".txt", ".zip"];
  const [fileSource, setFileSource] = useState<AgentIntegrationFileSource>("configured");
  const [file, setFile] = useState<File | null>(null);
  const [inboxReference, setInboxReference] = useState("");
  const [errorFileName, setErrorFileName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setFileSource("configured");
    setFile(null);
    setInboxReference("");
    setErrorFileName("");
    setSubmitting(false);
    setError(null);
  }, [request.request_id]);
  const disabled = busy || submitting;

  async function submit() {
    setError(null);
    try {
      if (fileSource === "upload" && !file) throw new Error("Choose a local Planning data file.");
      if (file && !extensions.some((extension) => file.name.toLowerCase().endsWith(extension.toLowerCase()))) throw new Error(`Supported files: ${extensions.join(", ")}.`);
      if (fileSource === "inbox" && !inboxReference.trim()) throw new Error("Choose an Oracle Inbox data file.");
      if (errorFileName.includes("/") || errorFileName.includes("\\")) throw new Error("Error output must be a filename, not a folder path.");
      setSubmitting(true);
      let fileChoice: Record<string, string> = { source: fileSource };
      if (fileSource === "upload" && file) {
        const receipt = await api.uploadOperationFile(file, csrfToken);
        fileChoice = { source: "upload", upload_token: receipt.upload.token, filename: receipt.upload.filename };
      } else if (fileSource === "inbox") {
        fileChoice = { source: "inbox", inbox_reference: inboxReference.trim() };
      }
      await onSubmit({ file_choice: fileChoice, error_file_name: errorFileName.trim() });
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setSubmitting(false);
    }
  }

  return <article className="assistant-guided-input assistant-pipeline-input" aria-label="Planning Data Import run inputs">
    <header><span className="assistant-guided-input__icon"><Icon name="data" /></span><div><span className="eyebrow">Guided Planning Data Import</span><h3>{request.title}</h3><p>{request.description}</p></div></header>
    <div className="assistant-guided-input__artifact"><small>Saved Import Data job</small><strong>{request.artifact_name}</strong></div>
    <section className="assistant-pipeline-input__section"><header><div><strong>Data source</strong><p>Use the job's configured file, select a compatible file already in Oracle, or upload a replacement.</p></div></header><div className="file-source-choice" role="radiogroup" aria-label="Planning Data Import file source">
      <label className={fileSource === "configured" ? "is-selected" : ""}><input type="radio" name="agent-data-import-source" checked={fileSource === "configured"} disabled={disabled} onChange={() => { setFileSource("configured"); setFile(null); setInboxReference(""); }} /><Icon name="settings" /><span><strong>Use configured file</strong><small>Use the filename saved in the Oracle job.</small></span></label>
      <label className={fileSource === "inbox" ? "is-selected" : ""}><input type="radio" name="agent-data-import-source" checked={fileSource === "inbox"} disabled={disabled} onChange={() => { setFileSource("inbox"); setFile(null); }} /><Icon name="automation" /><span><strong>Choose from Oracle Inbox</strong><small>Select a current compatible file.</small></span></label>
      <label className={fileSource === "upload" ? "is-selected" : ""}><input type="radio" name="agent-data-import-source" checked={fileSource === "upload"} disabled={disabled} onChange={() => { setFileSource("upload"); setInboxReference(""); }} /><Icon name="data" /><span><strong>Upload local file</strong><small>{extensions.join(", ")}</small></span></label>
    </div>
      {fileSource === "configured" && <div className="configured-file"><Icon name="check" /><span><strong>Use the file saved in Oracle</strong><small>The saved Import Data job controls its configured filename.</small></span></div>}
      {fileSource === "inbox" && <OracleFilePicker purpose="data-import" value={inboxReference} onChange={setInboxReference} label="Planning Data Import Oracle Inbox file" />}
      {fileSource === "upload" && <label className={`integration-upload${file ? " has-file" : ""}`}><input type="file" aria-label="Planning Data Import local file" disabled={disabled} accept={extensions.join(",")} onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><Icon name={file ? "check" : "data"} /><span><strong>{file?.name || "Choose a local data file"}</strong><small>{file ? `${Math.max(1, Math.round(file.size / 1024))} KB` : extensions.join(", ")}</small></span><em>{file ? "Change" : "Browse"}</em></label>}
    </section>
    <section className="assistant-pipeline-input__section"><header><div><strong>Error output</strong><p>Optional. Oracle writes rejected-row details to this Inbox filename when the job supports it.</p></div></header><label className="runner-field"><span>Error output filename</span><input aria-label="Planning Data Import error output filename" value={errorFileName} disabled={disabled} maxLength={250} onChange={(event) => setErrorFileName(event.target.value)} placeholder="Leave blank to use the Oracle default" /><small>Example: DataImportErrors.log</small></label></section>
    {error && <div className="assistant-draft__error"><Icon name="alert" />{error}</div>}
    <footer><button type="button" className="button button--secondary" disabled={disabled} onClick={() => void onSubmit(null)}>Cancel proposal</button><button type="button" className="button button--primary" disabled={disabled} onClick={() => void submit()}>{disabled ? <><span className="spinner" /> Preparing…</> : <>Continue to approval <Icon name="arrow" /></>}</button></footer>
  </article>;
}

function MetadataImportGuidedInputCard({ request, busy, csrfToken, onSubmit }: {
  request: AgentInputRequest;
  busy: boolean;
  csrfToken: string;
  onSubmit: (values: Record<string, unknown> | null) => Promise<void>;
}) {
  const context = request.context as { allowed_extensions?: string[]; refresh_jobs?: string[] };
  const extensions = context.allowed_extensions ?? [".csv", ".zip"];
  const refreshJobs = context.refresh_jobs ?? [];
  const [fileSource, setFileSource] = useState<AgentIntegrationFileSource>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [inboxReference, setInboxReference] = useState("");
  const [errorFileName, setErrorFileName] = useState("");
  const [refreshAfterImport, setRefreshAfterImport] = useState(false);
  const [refreshJobName, setRefreshJobName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setFileSource("upload");
    setFile(null);
    setInboxReference("");
    setErrorFileName("");
    setRefreshAfterImport(false);
    setRefreshJobName("");
    setSubmitting(false);
    setError(null);
  }, [request.request_id]);
  const disabled = busy || submitting;

  async function submit() {
    setError(null);
    try {
      if (fileSource === "upload" && !file) throw new Error("Choose a local metadata CSV or ZIP file.");
      if (file && !extensions.some((extension) => file.name.toLowerCase().endsWith(extension.toLowerCase()))) throw new Error(`Supported files: ${extensions.join(", ")}.`);
      if (fileSource === "inbox" && !inboxReference.trim()) throw new Error("Choose an Oracle Inbox metadata file.");
      if (errorFileName.includes("/") || errorFileName.includes("\\")) throw new Error("Error output must be a filename, not a folder path.");
      if (refreshAfterImport && !refreshJobName) throw new Error("Choose the saved Cube Refresh job.");
      setSubmitting(true);
      let fileChoice: Record<string, string> = { source: fileSource };
      if (fileSource === "upload" && file) {
        const receipt = await api.uploadOperationFile(file, csrfToken);
        fileChoice = { source: "upload", upload_token: receipt.upload.token, filename: receipt.upload.filename };
      } else if (fileSource === "inbox") {
        fileChoice = { source: "inbox", inbox_reference: inboxReference.trim() };
      }
      await onSubmit({
        file_choice: fileChoice,
        error_file_name: errorFileName.trim(),
        refresh_after_import: refreshAfterImport,
        refresh_job_name: refreshAfterImport ? refreshJobName : ""
      });
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setSubmitting(false);
    }
  }

  return <article className="assistant-guided-input assistant-pipeline-input" aria-label="Metadata Import run inputs">
    <header><span className="assistant-guided-input__icon"><Icon name="tasks" /></span><div><span className="eyebrow">Guided Metadata Import</span><h3>{request.title}</h3><p>{request.description}</p></div></header>
    <div className="assistant-guided-input__artifact"><small>Saved Import Metadata job</small><strong>{request.artifact_name}</strong></div>
    <section className="assistant-pipeline-input__section"><header><div><strong>Metadata source</strong><p>Use files configured in the saved job, choose a compatible live Inbox file, or upload the current hierarchy file.</p></div></header><div className="file-source-choice" role="radiogroup" aria-label="Metadata Import file source">
      <label className={fileSource === "configured" ? "is-selected" : ""}><input type="radio" name="agent-metadata-import-source" checked={fileSource === "configured"} disabled={disabled} onChange={() => { setFileSource("configured"); setFile(null); setInboxReference(""); }} /><Icon name="settings" /><span><strong>Use configured job files</strong><small>No runtime filename override.</small></span></label>
      <label className={fileSource === "inbox" ? "is-selected" : ""}><input type="radio" name="agent-metadata-import-source" checked={fileSource === "inbox"} disabled={disabled} onChange={() => { setFileSource("inbox"); setFile(null); }} /><Icon name="automation" /><span><strong>Choose from Oracle Inbox</strong><small>Select a current compatible file.</small></span></label>
      <label className={fileSource === "upload" ? "is-selected" : ""}><input type="radio" name="agent-metadata-import-source" checked={fileSource === "upload"} disabled={disabled} onChange={() => { setFileSource("upload"); setInboxReference(""); }} /><Icon name="data" /><span><strong>Upload local file</strong><small>{extensions.join(", ")}</small></span></label>
    </div>
      {fileSource === "configured" && <div className="configured-file"><Icon name="check" /><span><strong>Use the saved Oracle job configuration</strong><small>Oracle will use the dimension files already assigned to this job.</small></span></div>}
      {fileSource === "inbox" && <OracleFilePicker purpose="metadata-import" value={inboxReference} onChange={setInboxReference} label="Metadata Import Oracle Inbox file" />}
      {fileSource === "upload" && <label className={`integration-upload${file ? " has-file" : ""}`}><input type="file" aria-label="Metadata Import local file" disabled={disabled} accept={extensions.join(",")} onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><Icon name={file ? "check" : "data"} /><span><strong>{file?.name || "Choose a metadata file"}</strong><small>{file ? `${Math.max(1, Math.round(file.size / 1024))} KB` : extensions.join(", ")}</small></span><em>{file ? "Change" : "Browse"}</em></label>}
    </section>
    <section className="assistant-pipeline-input__section"><header><div><strong>After the import</strong><p>Optionally synchronize successful metadata changes with the Planning cube.</p></div></header>
      <label className="runner-approval metadata-refresh-choice"><input type="checkbox" aria-label="Refresh cube after Metadata Import" checked={refreshAfterImport} disabled={disabled || !refreshJobs.length} onChange={(event) => { setRefreshAfterImport(event.target.checked); if (!event.target.checked) setRefreshJobName(""); }} /><span><strong>Refresh the cube after a successful import</strong><small>{refreshJobs.length ? "The refresh is skipped automatically if the import fails." : "No saved Cube Refresh job is currently visible in Oracle."}</small></span></label>
      {refreshAfterImport && <label className="runner-field"><span>Saved Cube Refresh job *</span><select aria-label="Metadata Import Cube Refresh job" value={refreshJobName} disabled={disabled} onChange={(event) => setRefreshJobName(event.target.value)}><option value="">Select a saved Cube Refresh job</option>{refreshJobs.map((job) => <option value={job} key={job}>{job}</option>)}</select><small>{refreshJobs.length} current Oracle job{refreshJobs.length === 1 ? "" : "s"} available.</small></label>}
    </section>
    <section className="assistant-pipeline-input__section"><header><div><strong>Error output</strong><p>Optional. Oracle writes rejected metadata records to this Inbox filename when supported.</p></div></header><label className="runner-field"><span>Error output filename</span><input aria-label="Metadata Import error output filename" value={errorFileName} disabled={disabled} maxLength={250} onChange={(event) => setErrorFileName(event.target.value)} placeholder="Leave blank to use the Oracle default" /><small>Example: Metadata_Errors.csv</small></label></section>
    {error && <div className="assistant-draft__error"><Icon name="alert" />{error}</div>}
    <footer><button type="button" className="button button--secondary" disabled={disabled} onClick={() => void onSubmit(null)}>Cancel proposal</button><button type="button" className="button button--primary" disabled={disabled} onClick={() => void submit()}>{disabled ? <><span className="spinner" /> Preparing…</> : <>Continue to approval <Icon name="arrow" /></>}</button></footer>
  </article>;
}

function DataIntegrationGuidedInputCard({ request, busy, csrfToken, onSubmit }: {
  request: AgentInputRequest;
  busy: boolean;
  csrfToken: string;
  onSubmit: (values: Record<string, unknown> | null) => Promise<void>;
}) {
  const context = request.context as { import_modes?: string[]; export_modes?: string[]; allowed_extensions?: string[] };
  const importModes = context.import_modes ?? ["Replace", "Append", "Map and Validate", "No Import"];
  const exportModes = context.export_modes ?? ["Merge", "Replace", "Accumulate", "Subtract", "No Export", "Check"];
  const extensions = context.allowed_extensions ?? [".csv", ".txt", ".zip", ".dat"];
  const [periodMode, setPeriodMode] = useState<AgentIntegrationPeriodMode>("mapped");
  const [year, setYear] = useState("");
  const [startMonth, setStartMonth] = useState("");
  const [endMonth, setEndMonth] = useState("");
  const [exactStart, setExactStart] = useState("");
  const [exactEnd, setExactEnd] = useState("");
  const [importMode, setImportMode] = useState(importModes[0] ?? "Replace");
  const [exportMode, setExportMode] = useState(exportModes[0] ?? "Merge");
  const [fileSource, setFileSource] = useState<AgentIntegrationFileSource>("configured");
  const [file, setFile] = useState<File | null>(null);
  const [inboxReference, setInboxReference] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setPeriodMode("mapped"); setYear(""); setStartMonth(""); setEndMonth("");
    setExactStart(""); setExactEnd(""); setImportMode(importModes[0] ?? "Replace");
    setExportMode(exportModes[0] ?? "Merge"); setFileSource("configured");
    setFile(null); setInboxReference(""); setSubmitting(false); setError(null);
  }, [request.request_id]);
  const disabled = busy || submitting;

  function periods() {
    if (periodMode === "exact") return { start: exactStart.trim(), end: exactEnd.trim() };
    const suffix = year.replace(/^FY/i, "");
    if (!/^\d{2}$/.test(suffix) || !startMonth || !endMonth) return { start: "", end: "" };
    return periodMode === "planning"
      ? { start: `${startMonth}#FY${suffix}`, end: `${endMonth}#FY${suffix}` }
      : { start: `${startMonth}-${suffix}`, end: `${endMonth}-${suffix}` };
  }

  async function submit() {
    setError(null);
    try {
      const range = periods();
      if (!range.start || !range.end) throw new Error(periodMode === "exact" ? "Enter both exact Oracle period names." : "Select the Planning year, start month, and end month.");
      if (fileSource === "upload" && !file) throw new Error("Choose a local Data Integration file.");
      if (file && !extensions.some((extension) => file.name.toLowerCase().endsWith(extension.toLowerCase()))) throw new Error(`Supported files: ${extensions.join(", ")}.`);
      if (fileSource === "inbox" && !inboxReference.trim()) throw new Error("Choose an Oracle Inbox file.");
      setSubmitting(true);
      let fileChoice: Record<string, string> = { source: fileSource };
      if (fileSource === "upload" && file) {
        const receipt = await api.uploadOperationFile(file, csrfToken);
        fileChoice = { source: "upload", upload_token: receipt.upload.token, filename: receipt.upload.filename };
      } else if (fileSource === "inbox") {
        fileChoice = { source: "inbox", inbox_reference: inboxReference.trim() };
      }
      await onSubmit({ start_period: range.start, end_period: range.end, import_mode: importMode, export_mode: exportMode, file_choice: fileChoice });
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setSubmitting(false);
    }
  }

  return <article className="assistant-guided-input assistant-pipeline-input" aria-label="Data Integration run inputs">
    <header><span className="assistant-guided-input__icon"><Icon name="data" /></span><div><span className="eyebrow">Guided Data Integration setup</span><h3>{request.title}</h3><p>{request.description}</p></div></header>
    <div className="assistant-guided-input__artifact"><small>Selected Data Integration</small><strong>{request.artifact_name}</strong></div>
    <section className="assistant-pipeline-input__section"><header><div><strong>Period range</strong><p>Use dropdowns for standard mappings, or exact names only when your Oracle configuration is different.</p></div></header><div className="assistant-pipeline-input__variables">
      <label className="runner-field"><span>Period naming *</span><select aria-label="Agent Data Integration period naming" value={periodMode} disabled={disabled} onChange={(event) => setPeriodMode(event.target.value as AgentIntegrationPeriodMode)}><option value="mapped">Mapped periods (Jan-27)</option><option value="planning">Planning members (Jan#FY27)</option><option value="exact">Advanced: exact Oracle names</option></select></label>
      {periodMode !== "exact" ? <><label className="runner-field"><span>Planning year *</span><select aria-label="Agent Data Integration planning year" value={year} disabled={disabled} onChange={(event) => setYear(event.target.value)}><option value="">Select year</option>{AGENT_YEARS.map((item) => <option key={item}>{item}</option>)}</select></label><label className="runner-field"><span>Start month *</span><select aria-label="Agent Data Integration start month" value={startMonth} disabled={disabled} onChange={(event) => setStartMonth(event.target.value)}><option value="">Select month</option>{AGENT_MONTHS.map((item) => <option key={item}>{item}</option>)}</select></label><label className="runner-field"><span>End month *</span><select aria-label="Agent Data Integration end month" value={endMonth} disabled={disabled} onChange={(event) => setEndMonth(event.target.value)}><option value="">Select month</option>{AGENT_MONTHS.map((item) => <option key={item}>{item}</option>)}</select></label></> : <><label className="runner-field"><span>Exact start period *</span><input aria-label="Agent Data Integration exact start period" value={exactStart} disabled={disabled} onChange={(event) => setExactStart(event.target.value)} placeholder="Jan-27" /></label><label className="runner-field"><span>Exact end period *</span><input aria-label="Agent Data Integration exact end period" value={exactEnd} disabled={disabled} onChange={(event) => setExactEnd(event.target.value)} placeholder="Mar-27" /></label></>}
      <label className="runner-field"><span>Import mode *</span><select aria-label="Agent Data Integration import mode" value={importMode} disabled={disabled} onChange={(event) => setImportMode(event.target.value)}>{importModes.map((item) => <option key={item}>{item}</option>)}</select></label><label className="runner-field"><span>Export mode *</span><select aria-label="Agent Data Integration export mode" value={exportMode} disabled={disabled} onChange={(event) => setExportMode(event.target.value)}>{exportModes.map((item) => <option key={item}>{item}</option>)}</select></label>
    </div></section>
    <section className="assistant-pipeline-input__section"><header><div><strong>Source file</strong><p>Use the Integration configuration, select a compatible live Inbox file, or upload a replacement.</p></div></header><div className="file-source-choice" role="radiogroup" aria-label="Agent Data Integration file source"><label className={fileSource === "configured" ? "is-selected" : ""}><input type="radio" name="agent-integration-source" checked={fileSource === "configured"} disabled={disabled} onChange={() => { setFileSource("configured"); setFile(null); setInboxReference(""); }} /><Icon name="settings" /><span><strong>Use configured file</strong><small>No runtime filename override.</small></span></label><label className={fileSource === "inbox" ? "is-selected" : ""}><input type="radio" name="agent-integration-source" checked={fileSource === "inbox"} disabled={disabled} onChange={() => { setFileSource("inbox"); setFile(null); }} /><Icon name="automation" /><span><strong>Choose from Oracle Inbox</strong><small>Mouse-only live file selection.</small></span></label><label className={fileSource === "upload" ? "is-selected" : ""}><input type="radio" name="agent-integration-source" checked={fileSource === "upload"} disabled={disabled} onChange={() => { setFileSource("upload"); setInboxReference(""); }} /><Icon name="data" /><span><strong>Upload local file</strong><small>{extensions.join(", ")}</small></span></label></div>
      {fileSource === "configured" && <div className="configured-file"><Icon name="check" /><span><strong>Use the file saved in Oracle</strong><small>The Integration controls its configured filename.</small></span></div>}
      {fileSource === "inbox" && <OracleFilePicker purpose="data-integration" value={inboxReference} onChange={setInboxReference} label="Agent Data Integration Oracle Inbox file" />}
      {fileSource === "upload" && <label className={`integration-upload${file ? " has-file" : ""}`}><input type="file" aria-label="Agent Data Integration local file" disabled={disabled} accept={extensions.join(",")} onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><Icon name={file ? "check" : "data"} /><span><strong>{file?.name || "Choose a local file"}</strong><small>{file ? `${Math.max(1, Math.round(file.size / 1024))} KB` : extensions.join(", ")}</small></span><em>{file ? "Change" : "Browse"}</em></label>}
    </section>
    {error && <div className="assistant-draft__error"><Icon name="alert" />{error}</div>}
    <footer><button type="button" className="button button--secondary" disabled={disabled} onClick={() => void onSubmit(null)}>Cancel proposal</button><button type="button" className="button button--primary" disabled={disabled} onClick={() => void submit()}>{disabled ? <><span className="spinner" /> Preparing…</> : <>Continue to approval <Icon name="arrow" /></>}</button></footer>
  </article>;
}

type PipelineFileSource = "configured" | "upload" | "inbox" | "none";
interface AgentPipelineFileChoice { source: PipelineFileSource; file: File | null; inboxReference: string }
interface AgentPipelineContext {
  code: string;
  display_name: string;
  variables: PipelineVariablePreview[];
  file_requirements: PipelineFilePreview[];
  stages: PipelineStagePreview[];
}

function PipelineGuidedInputCard({ request, busy, csrfToken, onSubmit }: {
  request: AgentInputRequest;
  busy: boolean;
  csrfToken: string;
  onSubmit: (values: Record<string, unknown> | null) => Promise<void>;
}) {
  const context = request.context as unknown as AgentPipelineContext;
  const variables = context.variables ?? [];
  const requirements = context.file_requirements ?? [];
  const stages = context.stages ?? [];
  const [values, setValues] = useState<Record<string, string>>({});
  const [files, setFiles] = useState<Record<string, AgentPipelineFileChoice>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setValues(Object.fromEntries(variables.map((variable) => [variable.name, variable.default_value ?? ""])));
    setFiles(Object.fromEntries(requirements.map((requirement) => [requirement.key, {
      source: requirement.configured_reference ? "configured" : requirement.required ? "upload" : "none",
      file: null,
      inboxReference: ""
    }])));
    setSubmitting(false);
    setError(null);
  }, [request.request_id]);
  const disabled = busy || submitting;

  function updateFile(key: string, update: Partial<AgentPipelineFileChoice>) {
    setFiles((current) => ({ ...current, [key]: { ...current[key], ...update } }));
  }

  async function submit() {
    setError(null);
    try {
      for (const variable of variables) {
        if (variable.required && !(values[variable.name] ?? "").trim()) throw new Error(`Choose a value for ${variable.display_name}.`);
      }
      for (const requirement of requirements) {
        const choice = files[requirement.key];
        if (!choice) throw new Error(`Choose a file source for ${requirement.display_name}.`);
        if (choice.source === "configured" && !requirement.configured_reference) throw new Error(`${requirement.display_name} has no configured Oracle file.`);
        if (choice.source === "upload" && !choice.file) throw new Error(`Choose a local file for ${requirement.display_name}.`);
        if (choice.source === "inbox" && !choice.inboxReference.trim()) throw new Error(`Choose an Oracle Inbox file for ${requirement.display_name}.`);
        if (choice.source === "none" && requirement.required) throw new Error(`${requirement.display_name} is required.`);
      }
      setSubmitting(true);
      const resolvedFiles: Record<string, Record<string, string>> = {};
      for (const requirement of requirements) {
        const choice = files[requirement.key];
        if (choice.source === "upload" && choice.file) {
          const receipt = await api.uploadOperationFile(choice.file, csrfToken);
          resolvedFiles[requirement.key] = { source: "upload", upload_token: receipt.upload.token, filename: receipt.upload.filename };
        } else if (choice.source === "inbox") {
          resolvedFiles[requirement.key] = { source: "inbox", inbox_reference: choice.inboxReference.trim() };
        } else {
          resolvedFiles[requirement.key] = { source: choice.source };
        }
      }
      await onSubmit({
        runtime_variables: Object.fromEntries(Object.entries(values).map(([name, value]) => [name, value.trim()])),
        file_choices: resolvedFiles
      });
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setSubmitting(false);
    }
  }

  return <article className="assistant-guided-input assistant-pipeline-input" aria-label="Oracle Pipeline run inputs">
    <header><span className="assistant-guided-input__icon"><Icon name="automation" /></span><div><span className="eyebrow">Live Oracle Pipeline setup</span><h3>{request.title}</h3><p>{request.description}</p></div></header>
    <div className="assistant-guided-input__artifact"><small>Selected Pipeline</small><strong>{context.display_name || request.artifact_name} · {context.code || request.artifact_name}</strong></div>
    <section className="assistant-pipeline-input__stages"><header><strong>Stages configured in Oracle</strong><span>{stages.length} stage{stages.length === 1 ? "" : "s"}</span></header>{stages.length ? <ol>{stages.map((stage, index) => <li key={`${stage.name}-${index}`}><span>{index + 1}</span><div><strong>{stage.display_name}</strong><small>{stage.job_count} job{stage.job_count === 1 ? "" : "s"}{stage.runs_in_parallel ? " · Parallel" : ""}</small></div></li>)}</ol> : <p>No stage summary was returned, but Oracle will still own the Pipeline execution.</p>}</section>
    {variables.length > 0 && <section className="assistant-pipeline-input__section"><header><div><strong>Runtime values</strong><p>Oracle defaults are prefilled. Standard year and period values use dropdowns.</p></div></header><div className="assistant-pipeline-input__variables">{variables.map((variable) => <AgentPipelineVariable key={variable.name} variable={variable} value={values[variable.name] ?? ""} disabled={disabled} onChange={(value) => setValues((current) => ({ ...current, [variable.name]: value }))} />)}</div></section>}
    {requirements.length > 0 && <section className="assistant-pipeline-input__section"><header><div><strong>Files required by Pipeline stages</strong><p>Use the Oracle configured file, choose from the live Inbox, or upload a replacement.</p></div></header><div className="assistant-pipeline-input__files">{requirements.map((requirement) => <AgentPipelineFile key={requirement.key} requirement={requirement} choice={files[requirement.key]} disabled={disabled} onChange={(update) => updateFile(requirement.key, update)} />)}</div></section>}
    {!variables.length && !requirements.length && <div className="assistant-pipeline-input__ready"><Icon name="check" /><div><strong>No additional inputs are required</strong><p>The selected Pipeline can run with its Oracle configuration.</p></div></div>}
    {error && <div className="assistant-draft__error"><Icon name="alert" />{error}</div>}
    <footer><button type="button" className="button button--secondary" disabled={disabled} onClick={() => void onSubmit(null)}>Cancel proposal</button><button type="button" className="button button--primary" disabled={disabled} onClick={() => void submit()}>{disabled ? <><span className="spinner" /> Preparing…</> : <>Continue to approval <Icon name="arrow" /></>}</button></footer>
  </article>;
}

function AgentPipelineVariable({ variable, value, disabled, onChange }: { variable: PipelineVariablePreview; value: string; disabled: boolean; onChange: (value: string) => void }) {
  const normalized = variable.name.replaceAll("_", "").replaceAll(" ", "").toUpperCase();
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const years = Array.from({ length: 21 }, (_, index) => `FY${String(20 + index).padStart(2, "0")}`);
  let options: string[] | null = null;
  if (normalized === "STARTPERIOD" || normalized === "ENDPERIOD") options = ["GLOBAL_POV", ...years.flatMap((year) => months.map((month) => `${month}-${year.slice(2)}`))];
  else if (normalized === "YEAR" || normalized.endsWith("YEAR")) options = years;
  else if (normalized.endsWith("MONTH")) options = months;
  if (options && value && !options.includes(value)) options = [value, ...options];
  return <label className="runner-field"><span>{variable.display_name}{variable.required ? " *" : ""}</span>{options ? <select aria-label={`Pipeline variable ${variable.display_name}`} value={value} disabled={disabled || !variable.editable} onChange={(event) => onChange(event.target.value)}><option value="">Select a value</option>{options.map((option) => <option value={option} key={option}>{option}</option>)}</select> : <input aria-label={`Pipeline variable ${variable.display_name}`} value={value} disabled={disabled || !variable.editable} onChange={(event) => onChange(event.target.value)} />}<small>{variable.name}{variable.default_value ? " · Oracle default prefilled" : ""}</small></label>;
}

function AgentPipelineFile({ requirement, choice, disabled, onChange }: { requirement: PipelineFilePreview; choice: AgentPipelineFileChoice | undefined; disabled: boolean; onChange: (update: Partial<AgentPipelineFileChoice>) => void }) {
  if (!choice) return null;
  return <article className="assistant-pipeline-input__file"><header><div><strong>{requirement.display_name}</strong><small>{requirement.consumers.join(" · ") || requirement.key}</small></div><span>{requirement.required ? "Required" : "Optional"}</span></header><label className="runner-field"><span>File source</span><select aria-label={`${requirement.display_name} file source`} value={choice.source} disabled={disabled} onChange={(event) => onChange({ source: event.target.value as PipelineFileSource, file: null, inboxReference: "" })}>{requirement.configured_reference && <option value="configured">Use file configured in Oracle</option>}<option value="inbox">Choose an Oracle Inbox file</option><option value="upload">Upload a local replacement</option>{!requirement.required && <option value="none">No file for this run</option>}</select></label>{choice.source === "configured" && <div className="assistant-pipeline-input__configured"><Icon name="check" /><span><strong>Configured file</strong><small>{requirement.configured_reference}</small></span></div>}{choice.source === "inbox" && <OracleFilePicker purpose="pipeline" value={choice.inboxReference} onChange={(value) => onChange({ inboxReference: value })} label={`${requirement.display_name} Oracle Inbox file`} />}{choice.source === "upload" && <label className="integration-upload"><input type="file" aria-label={`${requirement.display_name} local file`} disabled={disabled} accept={requirement.allowed_extensions.join(",")} onChange={(event) => onChange({ file: event.target.files?.[0] ?? null })} /><Icon name={choice.file ? "check" : "data"} /><span><strong>{choice.file?.name || "Choose a local file"}</strong><small>{choice.file ? `${Math.max(1, Math.round(choice.file.size / 1024))} KB` : requirement.allowed_extensions.join(", ") || "Oracle-compatible file"}</small></span><em>{choice.file ? "Change" : "Browse"}</em></label>}</article>;
}

function BusinessRuleGuidedInputCard({ request, busy, onSubmit }: {
  request: AgentInputRequest;
  busy: boolean;
  onSubmit: (values: Record<string, unknown> | null) => Promise<void>;
}) {
  const [mode, setMode] = useState("");
  const [pairs, setPairs] = useState<GuidedPair[]>([]);
  const [error, setError] = useState<string | null>(null);
  const nextId = useRef(1);
  useEffect(() => {
    setMode("");
    setPairs([]);
    setError(null);
    nextId.current = 1;
  }, [request.request_id]);
  const usesOverrides = mode === "Provide runtime prompt values";

  function addPair() {
    setPairs((current) => [...current, { id: nextId.current++, name: "", value: "" }]);
  }

  function updatePair(id: number, key: "name" | "value", value: string) {
    setPairs((current) => current.map((item) => item.id === id ? { ...item, [key]: value } : item));
  }

  function submit() {
    setError(null);
    if (!mode) {
      setError("Choose how Oracle should obtain runtime prompt values.");
      return;
    }
    const runtimePrompts: Record<string, string> = {};
    if (usesOverrides) {
      if (!pairs.length) {
        setError("Add at least one runtime prompt name and value.");
        return;
      }
      const seen = new Set<string>();
      for (const pair of pairs) {
        const name = pair.name.trim();
        const value = pair.value.trim();
        if (!name || !value) {
          setError("Every runtime prompt requires both an exact name and value.");
          return;
        }
        if (seen.has(name.toLowerCase())) {
          setError(`Runtime prompt '${name}' was entered more than once.`);
          return;
        }
        seen.add(name.toLowerCase());
        runtimePrompts[name] = value;
      }
    }
    void onSubmit({ runtime_prompt_mode: mode, runtime_prompts: runtimePrompts });
  }

  return <article className="assistant-guided-input" aria-label="Business Rule runtime prompts">
    <header><span className="assistant-guided-input__icon"><Icon name="settings" /></span><div><span className="eyebrow">Guided Business Rule setup</span><h3>{request.title}</h3><p>{request.description}</p></div></header>
    <div className="assistant-guided-input__artifact"><small>Selected Business Rule</small><strong>{request.artifact_name}</strong></div>
    <label className="assistant-guided-input__mode"><span>Runtime prompt source *</span><select value={mode} disabled={busy} onChange={(event) => { setMode(event.target.value); setError(null); if (event.target.value !== "Provide runtime prompt values") setPairs([]); }}><option value="">Select how to continue</option><option value="Use Calculation Manager defaults">Use Calculation Manager defaults</option><option value="Provide runtime prompt values">Provide runtime prompt values</option></select><small>Defaults require no typing and use the values deployed with the rule.</small></label>
    {usesOverrides && <section className="assistant-guided-input__prompts"><header><div><strong>Runtime prompt overrides</strong><p>Oracle does not publish RTP definitions through the supported REST API. Enter only exact names configured for this rule.</p></div><button type="button" className="button button--quiet" disabled={busy} onClick={addPair}>+ Add prompt</button></header>{pairs.length ? <div>{pairs.map((pair) => <div className="assistant-guided-input__pair" key={pair.id}><label><span>Exact RTP name</span><input value={pair.name} disabled={busy} onChange={(event) => updatePair(pair.id, "name", event.target.value)} /></label><label><span>Value</span><input value={pair.value} disabled={busy} onChange={(event) => updatePair(pair.id, "value", event.target.value)} /></label><button type="button" aria-label="Remove runtime prompt" disabled={busy} onClick={() => setPairs((current) => current.filter((item) => item.id !== pair.id))}><Icon name="close" /></button></div>)}</div> : <button type="button" className="assistant-guided-input__empty" disabled={busy} onClick={addPair}><Icon name="settings" /><span><strong>Add the first runtime prompt</strong><small>Name and value are validated before the draft is created.</small></span></button>}</section>}
    {error && <div className="assistant-draft__error"><Icon name="alert" />{error}</div>}
    <footer><button type="button" className="button button--secondary" disabled={busy} onClick={() => void onSubmit(null)}>Cancel proposal</button><button type="button" className="button button--primary" disabled={busy || !mode} onClick={submit}>{busy ? <><span className="spinner" /> Validating…</> : <>Continue to approval <Icon name="arrow" /></>}</button></footer>
  </article>;
}

function DataMapGuidedInputCard({ request, busy, onSubmit }: {
  request: AgentInputRequest;
  busy: boolean;
  onSubmit: (values: Record<string, unknown> | null) => Promise<void>;
}) {
  const [clearTarget, setClearTarget] = useState("");
  const [memberOverrides, setMemberOverrides] = useState<GuidedPair[]>([]);
  const [exclusionOverrides, setExclusionOverrides] = useState<GuidedPair[]>([]);
  const [error, setError] = useState<string | null>(null);
  const nextId = useRef(1);
  useEffect(() => {
    setClearTarget("");
    setMemberOverrides([]);
    setExclusionOverrides([]);
    setError(null);
    nextId.current = 1;
  }, [request.request_id]);

  function addPair(kind: "member" | "exclusion") {
    const pair = { id: nextId.current++, name: "", value: "" };
    if (kind === "member") setMemberOverrides((current) => [...current, pair]);
    else setExclusionOverrides((current) => [...current, pair]);
  }

  function toRecord(pairs: GuidedPair[], label: string) {
    const result: Record<string, string> = {};
    const seen = new Set<string>();
    for (const pair of pairs) {
      const name = pair.name.trim();
      const value = pair.value.trim();
      if (!name || !value) throw new Error(`Every ${label} requires both a dimension and member selection.`);
      if (seen.has(name.toLowerCase())) throw new Error(`Dimension '${name}' was entered more than once in ${label}s.`);
      seen.add(name.toLowerCase());
      result[name] = value;
    }
    return result;
  }

  function submit() {
    setError(null);
    if (!clearTarget) {
      setError("Choose whether Oracle should clear the target before publishing data.");
      return;
    }
    try {
      void onSubmit({
        clear_target: clearTarget === "true",
        member_overrides: toRecord(memberOverrides, "member override"),
        exclusion_overrides: toRecord(exclusionOverrides, "exclusion override")
      });
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }

  return <article className="assistant-guided-input" aria-label="Data Map run options">
    <header><span className="assistant-guided-input__icon"><Icon name="automation" /></span><div><span className="eyebrow">Guided Data Map setup</span><h3>{request.title}</h3><p>{request.description}</p></div></header>
    <div className="assistant-guided-input__artifact"><small>Selected Data Map</small><strong>{request.artifact_name}</strong></div>
    <label className="assistant-guided-input__mode"><span>Clear target before push *</span><select value={clearTarget} disabled={busy} onChange={(event) => { setClearTarget(event.target.value); setError(null); }}><option value="">Select an option</option><option value="false">No — preserve existing target data</option><option value="true">Yes — clear the mapped target region first</option></select><small>Choose Yes only when the reviewed target slice should be cleared before publishing.</small></label>
    <GuidedPairSection title="Member overrides" description="Optional. Narrow the configured Data Map using exact Oracle dimension and member selections." addLabel="+ Add override" nameLabel="Exact dimension" valueLabel="Member selection" pairs={memberOverrides} busy={busy} onAdd={() => addPair("member")} onChange={setMemberOverrides} />
    <GuidedPairSection title="Exclusion overrides" description="Optional. Exclude exact member selections from the Data Map run." addLabel="+ Add exclusion" nameLabel="Exact dimension" valueLabel="Excluded member selection" pairs={exclusionOverrides} busy={busy} onAdd={() => addPair("exclusion")} onChange={setExclusionOverrides} />
    {error && <div className="assistant-draft__error"><Icon name="alert" />{error}</div>}
    <footer><button type="button" className="button button--secondary" disabled={busy} onClick={() => void onSubmit(null)}>Cancel proposal</button><button type="button" className="button button--primary" disabled={busy || !clearTarget} onClick={submit}>{busy ? <><span className="spinner" /> Validating…</> : <>Continue to approval <Icon name="arrow" /></>}</button></footer>
  </article>;
}

function GuidedPairSection({ title, description, addLabel, nameLabel, valueLabel, pairs, busy, onAdd, onChange }: {
  title: string;
  description: string;
  addLabel: string;
  nameLabel: string;
  valueLabel: string;
  pairs: GuidedPair[];
  busy: boolean;
  onAdd: () => void;
  onChange: (pairs: GuidedPair[]) => void;
}) {
  return <section className="assistant-guided-input__prompts"><header><div><strong>{title}</strong><p>{description}</p></div><button type="button" className="button button--quiet" disabled={busy} onClick={onAdd}>{addLabel}</button></header>{pairs.length ? <div>{pairs.map((pair) => <div className="assistant-guided-input__pair" key={pair.id}><label><span>{nameLabel}</span><input value={pair.name} disabled={busy} onChange={(event) => onChange(pairs.map((item) => item.id === pair.id ? { ...item, name: event.target.value } : item))} /></label><label><span>{valueLabel}</span><input value={pair.value} disabled={busy} onChange={(event) => onChange(pairs.map((item) => item.id === pair.id ? { ...item, value: event.target.value } : item))} /></label><button type="button" aria-label={`Remove ${title.toLowerCase()}`} disabled={busy} onClick={() => onChange(pairs.filter((item) => item.id !== pair.id))}><Icon name="close" /></button></div>)}</div> : <button type="button" className="assistant-guided-input__empty" disabled={busy} onClick={onAdd}><Icon name="settings" /><span><strong>No {title.toLowerCase()}</strong><small>The configured Data Map selections will be used as-is.</small></span></button>}</section>;
}

function ApprovalCard({ approval, deciding, onDecision }: {
  approval: AgentApprovalRequest;
  deciding: "approve" | "reject" | null;
  onDecision: (decision: "approve" | "reject") => Promise<void>;
}) {
  const prompts = stringRecord(approval.input_values?.runtime_prompts);
  const memberOverrides = stringRecord(approval.input_values?.member_overrides);
  const exclusionOverrides = stringRecord(approval.input_values?.exclusion_overrides);
  const pipelineVariables = stringRecord(approval.input_values?.runtime_variables);
  const pipelineInbox = stringRecord(approval.input_values?.inbox_files);
  const pipelineUploads = stringRecord(approval.input_values?.upload_names);
  const pipelineConfigured = stringRecord(approval.input_values?.configured_files);
  const isDataMap = approval.operation_code === "data-maps";
  const isPipeline = approval.operation_code === "pipelines";
  const isDataIntegration = approval.operation_code === "data-integrations";
  const isDataImport = approval.operation_code === "data-import";
  const isMetadataImport = approval.operation_code === "metadata-import";
  const isCubeRefresh = approval.operation_code === "cube-refresh";
  const isSubstitutionVariable = approval.operation_code === "substitution-variables";
  const isUserVariable = approval.operation_code === "user-variables";
  const isStandaloneFlow = approval.operation_code === "standalone-flow";
  const flowSteps = recordList(approval.input_values?.steps);
  const runsDirectly = ["business-rules", "data-maps", "pipelines", "data-integrations", "data-import", "metadata-import", "cube-refresh", "substitution-variables", "user-variables", "standalone-flow"].includes(approval.operation_code);
  return <article className="assistant-approval" aria-label="Action preparation approval">
    <header>
      <span className="assistant-approval__icon"><Icon name="alert" /></span>
      <div><span className="eyebrow">Your approval is required</span><h3>{runsDirectly ? "Run" : "Prepare"} {approval.display_name}?</h3><p>{approval.objective}</p></div>
      <span className="assistant-approval__risk">{approval.risk_level}</span>
    </header>
    <dl>
      <div><dt>Operation</dt><dd>{approval.category}</dd></div>
      <div><dt>Artifact</dt><dd>{approval.artifact_name || "Choose on governed screen"}</dd></div>
      <div><dt>What approval does</dt><dd>{approval.effect}</dd></div>
    </dl>
    {isStandaloneFlow
      ? <section className="assistant-approval__inputs assistant-approval__flow"><strong>Execution order</strong><p>Each operation must finish successfully before the next one starts.</p><ol>{flowSteps.map((step, index) => <li key={`${String(step.operation_code)}-${index}`}><span>{index + 1}</span><div><strong>{String(step.display_name || step.operation_code || "Operation")}</strong><small>{String(step.artifact_name || "")}</small></div><em>{String(step.risk_level || "Controlled")}</em></li>)}</ol></section>
      : isUserVariable
      ? <section className="assistant-approval__inputs"><strong>User Variable assignment</strong><dl><div><dt>Oracle user</dt><dd>{String(approval.input_values?.user_name || "")}</dd></div><div><dt>Variable</dt><dd>{String(approval.input_values?.variable_name || "")}</dd></div><div><dt>Dimension</dt><dd>{String(approval.input_values?.dimension || "")}</dd></div><div><dt>Current member</dt><dd>{String(approval.input_values?.expected_current_member ?? "Not assigned")}</dd></div><div><dt>New member</dt><dd>{String(approval.input_values?.new_member || "")}</dd></div></dl></section>
      : isSubstitutionVariable
      ? <section className="assistant-approval__inputs"><strong>Substitution Variable change</strong><dl><div><dt>Action</dt><dd>{String(approval.input_values?.action || "") === "CREATE" ? "Create new" : "Update existing"}</dd></div><div><dt>Variable</dt><dd>{String(approval.input_values?.scope || "")}.{String(approval.input_values?.variable_name || "")}</dd></div>{String(approval.input_values?.action || "") === "UPDATE" && <div><dt>Current value</dt><dd>{String(approval.input_values?.expected_current_value || "Empty")}</dd></div>}<div><dt>New value</dt><dd>{String(approval.input_values?.new_value || "")}</dd></div></dl></section>
      : isCubeRefresh
      ? <section className="assistant-approval__inputs"><strong>Application-wide impact</strong><dl><div><dt>Saved refresh job</dt><dd>{approval.artifact_name}</dd></div><div><dt>Configuration</dt><dd>Use the cube selections and refresh controls saved in Oracle</dd></div><div><dt>User impact</dt><dd>Planning availability or performance may be affected while the refresh runs</dd></div></dl></section>
      : isMetadataImport
      ? <section className="assistant-approval__inputs"><strong>Metadata Import controls</strong><dl><div><dt>Source file</dt><dd>{String(approval.input_values?.upload_name || approval.input_values?.inbox_file || (approval.input_values?.file_source === "Use file configured in Oracle" ? "Configured in Oracle" : ""))}</dd></div><div><dt>Error output</dt><dd>{String(approval.input_values?.error_file_name || "Oracle default")}</dd></div><div><dt>After success</dt><dd>{approval.input_values?.refresh_after_import === true ? `Run ${String(approval.input_values?.refresh_job_name || "Cube Refresh")}` : "No Cube Refresh"}</dd></div></dl></section>
      : isDataImport
      ? <section className="assistant-approval__inputs"><strong>Planning Data Import controls</strong><dl><div><dt>Source file</dt><dd>{String(approval.input_values?.upload_name || approval.input_values?.inbox_file || (approval.input_values?.file_source === "Use file configured in Oracle" ? "Configured in Oracle" : ""))}</dd></div><div><dt>Error output</dt><dd>{String(approval.input_values?.error_file_name || "Oracle default")}</dd></div></dl></section>
      : isDataIntegration
      ? <section className="assistant-approval__inputs"><strong>Data Integration controls</strong><dl><div><dt>Period range</dt><dd>{String(approval.input_values?.start_period || "")} to {String(approval.input_values?.end_period || "")}</dd></div><div><dt>Import mode</dt><dd>{String(approval.input_values?.import_mode || "")}</dd></div><div><dt>Export mode</dt><dd>{String(approval.input_values?.export_mode || "")}</dd></div><div><dt>Source file</dt><dd>{String(approval.input_values?.upload_name || approval.input_values?.inbox_file || (approval.input_values?.file_source === "Use file configured in Oracle" ? "Configured in Oracle" : ""))}</dd></div></dl></section>
      : isPipeline
      ? <><section className="assistant-approval__inputs"><strong>Pipeline runtime values</strong>{Object.keys(pipelineVariables).length ? <dl>{Object.entries(pipelineVariables).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl> : <p>Use values and defaults configured in Oracle.</p>}</section><section className="assistant-approval__inputs"><strong>Pipeline files</strong>{Object.keys({ ...pipelineConfigured, ...pipelineInbox, ...pipelineUploads }).length ? <dl>{Object.entries({ ...pipelineConfigured, ...pipelineInbox, ...pipelineUploads }).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl> : <p>No external file is required for this run.</p>}</section></>
      : isDataMap
      ? <section className="assistant-approval__inputs"><strong>Data Map controls</strong><dl><div><dt>Clear target</dt><dd>{approval.input_values?.clear_target === true ? "Yes" : "No"}</dd></div><div><dt>Member overrides</dt><dd>{Object.keys(memberOverrides).length ? Object.entries(memberOverrides).map(([name, value]) => `${name}=${value}`).join(", ") : "Use configured mapping"}</dd></div><div><dt>Exclusions</dt><dd>{Object.keys(exclusionOverrides).length ? Object.entries(exclusionOverrides).map(([name, value]) => `${name}=${value}`).join(", ") : "None"}</dd></div></dl></section>
      : <section className="assistant-approval__inputs"><strong>Runtime prompts</strong>{Object.keys(prompts).length ? <dl>{Object.entries(prompts).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl> : <p>Use defaults configured in Calculation Manager.</p>}</section>}
    <div className="assistant-approval__notice"><Icon name={runsDirectly ? "alert" : "check"} /><p>{runsDirectly ? <><strong>{isStandaloneFlow ? "This approval starts the complete reviewed sequence." : isUserVariable ? "This approval changes one Oracle user-variable assignment." : isSubstitutionVariable ? "This approval changes one Oracle substitution variable." : isCubeRefresh ? "This approval starts an application-wide Cube Refresh." : "This approval starts the Oracle operation."}</strong> {isStandaloneFlow ? "The flow will be queued as one monitored execution and will stop after the first failed operation." : <>The selected {isUserVariable ? "user-variable change" : isSubstitutionVariable ? "variable change" : isCubeRefresh ? "Cube Refresh job" : isPipeline ? "Pipeline" : isDataIntegration ? "Data Integration" : isMetadataImport ? "Metadata Import" : isDataImport ? "Planning Data Import" : isDataMap ? "Data Map" : "Business Rule"} will be queued immediately and monitored in Jobs &amp; Activity.</>}</> : <><strong>Oracle will not run from this approval.</strong> Approval only creates a draft that you validate and continue on the operation's governed screen.</>}</p></div>
    <footer>
      <button type="button" className="button button--secondary" disabled={Boolean(deciding)} onClick={() => void onDecision("reject")}>{deciding === "reject" ? <><span className="spinner" /> Rejecting…</> : "Reject proposal"}</button>
      <button type="button" className="button button--primary" disabled={Boolean(deciding)} onClick={() => void onDecision("approve")}>{deciding === "approve" ? <><span className="spinner" /> {runsDirectly ? "Starting…" : "Preparing…"}</> : <>{runsDirectly ? "Approve and run" : "Approve preparation"} <Icon name="arrow" /></>}</button>
    </footer>
  </article>;
}

export function AgentExecutionCard({ approved, csrfToken, onRecoveryStarted, onDismiss }: {
  approved: AgentApprovedExecution;
  csrfToken: string;
  onRecoveryStarted: (execution: AgentApprovedExecution) => void;
  onDismiss: () => void;
}) {
  const { execution, monitorError } = useOperationMonitor(approved.execution_id);
  const [recoveryPlan, setRecoveryPlan] = useState<StandaloneFlowRecoveryPlan | null>(null);
  const [reviewingRecovery, setReviewingRecovery] = useState(false);
  const [recoveryError, setRecoveryError] = useState<string | null>(null);
  const status = execution?.status ?? approved.status;
  const terminal = execution?.terminal ?? false;
  const success = status === "SUCCESS";
  const failed = ["FAILED", "RECOVERY_REQUIRED"].includes(status);
  const operationName = approved.operation_code === "standalone-flow" ? "Standalone Planning Flow" : approved.operation_code === "user-variables" ? "User Variable change" : approved.operation_code === "substitution-variables" ? "Substitution Variable change" : approved.operation_code === "cube-refresh" ? "Cube Refresh" : approved.operation_code === "pipelines" ? "Pipeline" : approved.operation_code === "data-integrations" ? "Data Integration" : approved.operation_code === "metadata-import" ? "Metadata Import" : approved.operation_code === "data-import" ? "Planning Data Import" : approved.operation_code === "data-maps" ? "Data Map" : "Business Rule";
  const flow = execution?.flow_progress ?? null;
  const currentStep = flow?.current_step;
  const canRecover = approved.operation_code === "standalone-flow" && status === "FAILED";

  async function reviewRecovery() {
    setReviewingRecovery(true);
    setRecoveryError(null);
    try {
      const response = await api.standaloneFlowRecovery(approved.execution_id);
      setRecoveryPlan(response.recovery);
    } catch (reason) {
      setRecoveryError(errorMessage(reason));
    } finally {
      setReviewingRecovery(false);
    }
  }
  const message = monitorError
    || (success
      ? `${operationName} completed successfully.`
      : failed
        ? execution?.error_message || `${operationName} execution failed.`
        : currentStep
          ? `Step ${currentStep.sequence} of ${flow.total_steps}: ${currentStep.display_name} is ${currentStep.status === "RUNNING" ? "running" : "waiting to start"}.`
          : `${operationName} is queued or running in Oracle.`);
  return <article className={`assistant-agent-execution${flow ? " is-flow" : ""}${success ? " is-success" : failed ? " is-failed" : " is-active"}`} aria-label={`Approved ${operationName} execution`}>
    <span className="assistant-agent-execution__icon">{success ? <Icon name="check" /> : failed ? <Icon name="alert" /> : <span className="spinner spinner--dark" />}</span>
    <div><span className="eyebrow">Approved Oracle execution</span><h3>{approved.target_name}</h3><p>{message}</p><small>Execution {approved.execution_id.slice(0, 8)} · {friendlyName(status)}</small></div>
    <footer>{canRecover && <button type="button" className="button button--primary" disabled={reviewingRecovery} onClick={() => void reviewRecovery()}>{reviewingRecovery ? <><span className="spinner" /> Reviewing…</> : <>Review recovery <Icon name="arrow" /></>}</button>}<a className="button button--secondary" href={`/?execution_id=${encodeURIComponent(approved.execution_id)}#jobs`}>{terminal ? "View evidence" : "Open live status"} <Icon name="arrow" /></a><button type="button" className="icon-button" aria-label="Dismiss execution status" title="Dismiss" onClick={onDismiss}><Icon name="close" /></button></footer>
    {recoveryError && <div className="assistant-agent-execution__recovery-error"><Icon name="alert" />{recoveryError}</div>}
    {flow && <StandaloneFlowTimeline flow={flow} />}
    {recoveryPlan && <StandaloneFlowRecoveryDialog plan={recoveryPlan} csrfToken={csrfToken} onClose={() => setRecoveryPlan(null)} onStarted={(accepted) => { setRecoveryPlan(null); onRecoveryStarted({ execution_id: accepted.execution_id, operation_code: "standalone-flow", target_name: `Recovery - ${recoveryPlan.flow_name}`, status: "QUEUED" }); }} />}
  </article>;
}

function StandaloneFlowTimeline({ flow }: { flow: StandaloneFlowProgress }) {
  return <section className="assistant-agent-execution__flow" aria-label="Standalone flow progress">
    <header>
      <div><strong>{flow.completed_steps} of {flow.total_steps} steps completed</strong><small>{flow.successful_steps} successful</small>{flow.recovery && <small>Recovery of {flow.recovery.source_execution_id.slice(0, 8)} from original step {flow.recovery.from_original_step}</small>}</div>
      <span>{flow.progress_percent}%</span>
    </header>
    <div className="assistant-agent-execution__progress" role="progressbar" aria-label="Flow completion" aria-valuemin={0} aria-valuemax={100} aria-valuenow={flow.progress_percent}><span style={{ width: `${flow.progress_percent}%` }} /></div>
    <ol>{flow.steps.map((step) => <li className={`is-${step.status.toLowerCase()}`} key={`${step.sequence}-${step.operation_code}-${step.artifact_name}`}>
      <span className="assistant-agent-execution__step-icon">{step.status === "SUCCESS" ? <Icon name="check" /> : step.status === "FAILED" ? <Icon name="alert" /> : step.status === "RUNNING" ? <span className="spinner spinner--dark" /> : step.sequence}</span>
      <div><strong>{step.display_name || friendlyName(step.operation_code)}</strong><p>{step.artifact_name}</p>{step.error_message && <em>{step.error_message}</em>}</div>
      <aside>
        <b>{friendlyName(step.status)}</b>
        {step.active_stage && <small>{step.active_stage}</small>}
        {step.oracle_job_id != null && <small>Oracle job {String(step.oracle_job_id)}{step.oracle_status ? ` · ${step.oracle_status}` : ""}</small>}
        {step.record_statistics && <small>{recordStatisticsSummary(step.record_statistics)}</small>}
      </aside>
    </li>)}</ol>
  </section>;
}

export function StandaloneFlowRecoveryDialog({ plan, csrfToken, onClose, onStarted }: {
  plan: StandaloneFlowRecoveryPlan;
  csrfToken: string;
  onClose: () => void;
  onStarted: (execution: { execution_id: string }) => void;
}) {
  const [files, setFiles] = useState<Record<string, File>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const filesReady = plan.required_uploads.every((item) => Boolean(files[item.key]));

  async function approveRecovery() {
    if (!plan.retryable || !filesReady || busy) return;
    setBusy(true);
    setError(null);
    try {
      const replacementUploads: Record<string, string> = {};
      for (const requirement of plan.required_uploads) {
        const file = files[requirement.key];
        const receipt = await api.uploadOperationFile(file, csrfToken);
        replacementUploads[requirement.key] = receipt.upload.token;
      }
      const accepted = await api.retryStandaloneFlow(
        plan.source_execution_id,
        {
          failed_step_sequence: plan.failed_step_sequence,
          confirmation: "RETRY_FROM_FAILED_STEP",
          replacement_uploads: replacementUploads
        },
        csrfToken
      );
      onStarted(accepted);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section className="access-dialog assistant-recovery-dialog" role="dialog" aria-modal="true" aria-labelledby="assistant-recovery-title">
      <header><div><span className="eyebrow">Governed failure recovery</span><h2 id="assistant-recovery-title">Retry from failed step</h2><p>Review exactly what will run again. Successful operations are excluded from this execution.</p></div><button type="button" aria-label="Close recovery review" disabled={busy} onClick={onClose}><Icon name="close" /></button></header>
      <div className="assistant-recovery-dialog__body">
        <section className="assistant-recovery-dialog__failure"><Icon name="alert" /><div><strong>Original failure at step {plan.failed_step_sequence}</strong><p>{plan.failure_reason}</p><small>Source execution {plan.source_execution_id}</small></div></section>
        {plan.blocked_reason && <FeedbackBanner tone="warning" title="Recovery is blocked" message={plan.blocked_reason} />}
        <section className="assistant-recovery-dialog__steps"><header><div><strong>Operations included in this retry</strong><p>Completed steps before step {plan.failed_step_sequence} will not run again.</p></div><span>{plan.steps.length} step{plan.steps.length === 1 ? "" : "s"}</span></header><ol>{plan.steps.map((step) => <li key={`${step.sequence}-${step.operation_code}`}><span>{step.sequence}</span><div><strong>{step.display_name}</strong><p>{step.artifact_name}</p></div><em>{step.original_status === "FAILED" ? "Retry" : "Previously skipped"}</em></li>)}</ol></section>
        {plan.required_uploads.length > 0 && <section className="assistant-recovery-dialog__files"><header><strong>Replacement files required</strong><p>Temporary files from the original execution were removed. Select each source file again.</p></header>{plan.required_uploads.map((requirement) => <label key={requirement.key}><span><strong>{requirement.label}</strong><small>Previously: {requirement.original_filename}{requirement.allowed_extensions.length ? ` · ${requirement.allowed_extensions.join(", ")}` : ""}</small></span><input type="file" accept={requirement.allowed_extensions.join(",")} disabled={busy || !plan.retryable} onChange={(event) => { const file = event.target.files?.[0]; setFiles((current) => { const next = { ...current }; if (file) next[requirement.key] = file; else delete next[requirement.key]; return next; }); }} /></label>)}</section>}
        <aside className="dialog-warning"><Icon name="alert" /><span>This creates a new linked execution. It never modifies or removes the original audit record.</span></aside>
        {error && <FeedbackBanner tone="error" title="Recovery could not start" message={error} />}
      </div>
      <footer><button type="button" className="button button--quiet" disabled={busy} onClick={onClose}>Cancel</button><button type="button" className="button button--primary" disabled={busy || !plan.retryable || !filesReady} onClick={() => void approveRecovery()}>{busy ? <><span className="spinner" /> Starting recovery…</> : "Approve and retry failed steps"}</button></footer>
    </section>
  </div>;
}

function AssistantEmpty({ onPrompt }: { onPrompt: (prompt: string) => void }) {
  return <div className="assistant-empty"><span className="assistant-empty__icon"><Icon name="assistant" /></span><h3>How can I help with Planning?</h3><p>Ask a question or prepare a governed action. Oracle operations start only after you explicitly review and approve them.</p><div className="assistant-prompts">{suggestedPrompts.map(([label, prompt]) => <button type="button" onClick={() => onPrompt(prompt)} key={prompt}><span>{label}</span><strong>{prompt}</strong><Icon name="arrow" /></button>)}</div></div>;
}

function ActionDraftCard({ draft, csrfToken, onUpdate }: { draft: AgentActionDraft; csrfToken: string; onUpdate: (draft: AgentActionDraft) => void }) {
  const [values, setValues] = useState<Record<string, unknown>>(draft.input_values);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setValues(draft.input_values), [draft]);
  const ready = draft.preflight_status === "READY_FOR_GOVERNED_REVIEW";

  async function preflight() {
    setBusy(true);
    setError(null);
    try {
      if (draft.input_schema.length) await api.updateAgentDraftInputs(draft.draft_id, values, csrfToken);
      const response = await api.preflightAgentDraft(draft.draft_id, csrfToken);
      onUpdate(response.action_draft);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  }

  return <article className="assistant-draft">
    <header><div><span className="eyebrow">Governed action draft</span><h3>{draft.display_name}</h3></div><span className={`assistant-draft__status is-${draftStatusTone(draft.preflight_status)}`}>{draftStatusLabel(draft.preflight_status)}</span></header>
    <p>{draft.objective}</p>
    <dl><div><dt>Category</dt><dd>{draft.category}</dd></div><div><dt>{draft.action_type === "process" ? "Process" : "Artifact"}</dt><dd>{draft.artifact_name || "Selected on governed screen"}</dd></div><div><dt>Risk</dt><dd>{draft.risk_level}</dd></div></dl>
    {draft.stages.length > 0 && <div className="assistant-draft__stages"><strong>Oracle Pipeline stages</strong><ol>{draft.stages.map((stage, index) => <li key={`${stage}-${index}`}><span>{index + 1}</span>{stage}</li>)}</ol></div>}
    {draft.input_schema.length > 0 && <div className="assistant-draft__inputs"><header><div><strong>Prepare required inputs</strong><p>Saved values are validated only. Nothing runs from this card.</p></div></header><div>{draft.input_schema.map((field) => <DraftField field={field} value={values[field.key]} onChange={(value) => setValues((current) => ({ ...current, [field.key]: value }))} key={field.key} />)}</div></div>}
    {draft.preflight_checks.length > 0 && <div className="assistant-draft__checks">{draft.preflight_checks.map((check) => <div className={`is-${check.status.toLowerCase()}`} key={check.code}><span>{check.status === "PASS" ? <Icon name="check" /> : <Icon name="alert" />}</span><div><strong>{check.label}</strong><p>{check.message}</p></div></div>)}</div>}
    {error && <div className="assistant-draft__error"><Icon name="alert" />{error}</div>}
    <footer><span><Icon name="check" /> Preparation only · no Oracle action has started</span><div>{!ready && <button type="button" className="button button--secondary" disabled={busy} onClick={() => void preflight()}>{busy ? <><span className="spinner" /> Validating…</> : "Validate preparation"}</button>}{ready && <a className="button button--primary" href={handoffHref(draft)}>Continue to governed review <Icon name="arrow" /></a>}</div></footer>
  </article>;
}

function DraftField({ field, value, onChange }: { field: AgentActionInputField; value: unknown; onChange: (value: unknown) => void }) {
  const label = `${field.label}${field.required ? " *" : ""}`;
  if (field.kind === "boolean") return <label><span>{label}</span><select value={typeof value === "boolean" ? String(value) : ""} onChange={(event) => onChange(event.target.value === "true")}><option value="">Select an option</option><option value="true">Yes</option><option value="false">No</option></select><small>{field.description}</small></label>;
  if (field.kind === "choice") return <label><span>{label}</span><select value={String(value ?? "")} onChange={(event) => onChange(event.target.value)}><option value="">Select an option</option>{field.options.map((option) => <option value={option} key={option}>{option}</option>)}</select><small>{field.description}</small></label>;
  if (field.kind === "key_value") return <label className="is-wide"><span>{label}</span><textarea rows={3} value={pairsToText(value)} onChange={(event) => onChange(parsePairs(event.target.value))} placeholder={field.placeholder || "Name=Value"} /><small>{field.description} Use one Name=Value pair per line.</small></label>;
  return <label><span>{label}</span><input value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} placeholder={field.placeholder} /><small>{field.description}</small></label>;
}

function MarkdownContent({ content }: { content: string }) {
  const lines = content.replaceAll("\r\n", "\n").split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index].trim();
    if (!line) { index += 1; continue; }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const Heading = (heading[1].length <= 2 ? "h3" : "h4") as "h3" | "h4";
      blocks.push(<Heading key={`h-${index}`}>{inlineMarkdown(heading[2])}</Heading>);
      index += 1;
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*[-*]\s+/, ""));
        index += 1;
      }
      blocks.push(<ul key={`l-${index}`}>{items.map((item, itemIndex) => <li key={itemIndex}>{inlineMarkdown(item)}</li>)}</ul>);
      continue;
    }
    const paragraph = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !/^(#{1,4})\s+/.test(lines[index].trim()) && !/^\s*[-*]\s+/.test(lines[index])) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push(<p key={`p-${index}`}>{inlineMarkdown(paragraph.join(" "))}</p>);
  }
  return <div className="assistant-markdown">{blocks}</div>;
}

function inlineMarkdown(value: string) {
  return value.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean).map((part, index) => part.startsWith("**") && part.endsWith("**") ? <strong key={index}>{part.slice(2, -2)}</strong> : part.startsWith("`") && part.endsWith("`") ? <code key={index}>{part.slice(1, -1)}</code> : <Fragment key={index}>{part}</Fragment>);
}

function AssistantMessageSkeleton() {
  return <div className="assistant-message-skeleton"><span className="skeleton-line" /><span className="skeleton-line" /><span className="skeleton-line" /></div>;
}

function draftStatusLabel(value: string | null) {
  const labels: Record<string, string> = { READY_FOR_GOVERNED_REVIEW: "Ready for governed review", NEEDS_INPUT: "Input required", VALIDATION_UNAVAILABLE: "Validation unavailable", BLOCKED: "Blocked" };
  return value ? labels[value] ?? friendlyName(value) : "Not validated";
}
function draftStatusTone(value: string | null) { return value === "READY_FOR_GOVERNED_REVIEW" ? "ready" : value === "BLOCKED" ? "blocked" : "pending"; }
function handoffHref(draft: AgentActionDraft) { const url = new URL(draft.route, window.location.origin); url.searchParams.set("agent_draft", draft.draft_id); return `${url.pathname}${url.search}${url.hash}`; }
function pairsToText(value: unknown) { return value && typeof value === "object" && !Array.isArray(value) ? Object.entries(value).map(([name, item]) => `${name}=${String(item)}`).join("\n") : ""; }
function parsePairs(value: string) { return Object.fromEntries(value.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => { const separator = line.indexOf("="); return separator > 0 ? [line.slice(0, separator).trim(), line.slice(separator + 1).trim()] : [line, ""]; })); }
function stringRecord(value: unknown): Record<string, string> { return value && typeof value === "object" && !Array.isArray(value) ? Object.fromEntries(Object.entries(value).map(([name, item]) => [name, String(item)])) : {}; }
function friendlyName(value: string) { return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
function recordStatisticsSummary(value: OracleRecordStatistics) { return `Read ${value.records_read.toLocaleString()} · processed ${value.records_processed.toLocaleString()} · rejected ${value.records_rejected.toLocaleString()}`; }
function formatConversationDate(value: string) { return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(value)); }
function formatMessageTime(value: string) { return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date(value)); }
function isAgentGridReview(value: unknown): value is AgentGridReviewResult {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  const grid = item.grid as DataReviewGrid | undefined;
  return typeof item.cube === "string" && Boolean(item.request) && Boolean(grid) && Array.isArray(grid?.rows);
}
function isAgentComparison(value: unknown): value is AgentComparisonResult {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  return typeof item.source_cube === "string" && typeof item.target_cube === "string" && Boolean(item.source_request) && Boolean(item.target_request) && Boolean(item.result);
}
function isAgentMissing(value: unknown) { return value == null || ["", "#missing", "missing", "none", "null"].includes(String(value).trim().toLowerCase()); }
function recordList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
}
function dataReviewSliceFromContext(context: AgentDataReviewContext): DataReviewSliceInput | null {
  const raw = context.tool === "compare_data_slices"
    ? context.selection.source
    : context.selection;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const selection = raw as Record<string, unknown>;
  const cube = String(selection.cube ?? "").trim();
  const rows = contextAxis(selection.rows);
  const columns = contextAxis(selection.columns);
  if (!cube || !rows.length || !columns.length) return null;
  let pov: Record<string, string> = {};
  if (Array.isArray(selection.pov)) {
    pov = Object.fromEntries(recordList(selection.pov).map((item) => [String(item.dimension ?? "").trim(), String(item.member ?? "").trim()]).filter(([dimension, member]) => dimension && member));
  } else if (selection.pov && typeof selection.pov === "object") {
    pov = Object.fromEntries(Object.entries(selection.pov as Record<string, unknown>).map(([dimension, member]) => [dimension.trim(), String(member).trim()]).filter(([dimension, member]) => dimension && member));
  }
  return { cube, pov, rows, columns };
}
function contextAxis(value: unknown) {
  return recordList(value).map((item) => ({
    dimension: String(item.dimension ?? "").trim(),
    members: Array.isArray(item.members) ? item.members.map((member) => String(member).trim()).filter(Boolean) : []
  })).filter((item) => item.dimension && item.members.length);
}
function formatAgentValue(value: unknown) {
  if (isAgentMissing(value)) return "—";
  const numeric = typeof value === "number" ? value : Number(value);
  if (Number.isFinite(numeric)) {
    if (numeric !== 0 && Math.abs(numeric) < 1e-9) return numeric.toExponential(6);
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: 12 }).format(numeric);
  }
  return String(value);
}
function safeAgentFilename(value: string) { return value.trim().replace(/[^a-z0-9_-]+/gi, "-") || "planning"; }
function downloadAgentBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
function errorMessage(reason: unknown) { return reason instanceof Error ? reason.message : "The EPM Assistant request could not be completed."; }

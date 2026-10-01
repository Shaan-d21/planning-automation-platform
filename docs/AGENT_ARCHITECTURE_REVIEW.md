# EPM Assistant Architecture Review

Date: 2026-09-23  
Branch reviewed: `codex/agent-variable-process-refresh`

## Scope

This review traces the current assistant from the HTTP request through
LangGraph, governed preparation, durable execution, Oracle monitoring, and
completion evidence. It intentionally identifies repair points before adding
new agent architecture.

## Current request lifecycle

1. `app/web/application.py` authenticates the platform session and accepts a
   conversation message.
2. `AgentApplicationService.send_message` persists the user message, loads a
   bounded message history, reads any checkpointed task context, and runs both
   `AgentTaskInterpreter` and `AgentIntentRouter`.
3. `AgentGraphOrchestrator.invoke` uses a stable thread key derived from the
   environment, user, and conversation. PostgreSQL-backed LangGraph
   checkpoints retain interrupts and JSON-safe graph state.
4. `AgentGraphOrchestrator._model_node` first evaluates many deterministic
   operation-specific branches and otherwise delegates language/tool choice to
   Groq or Gemini.
5. `AgentCapabilityGateway` exposes an allow-listed platform tool boundary,
   retrieves real Oracle or registered artifacts, builds guided inputs, and
   normalizes operation values.
6. LangGraph interrupts persist artifact choice, input collection, and explicit
   approval. Chat text cannot approve an operation.
7. `AgentApplicationService` records an immutable approval decision and submits
   the fully resolved operation through `OperationExecutionManager`.
8. `SQLExecutionQueueRepository` persists the job before execution and prevents
   duplicate active work for the same target.
9. `DurableExecutionWorker` leases work, heartbeats the lease, invokes the
   deterministic operation executor, persists workflow evidence, and avoids an
   automatic retry when a worker dies after Oracle submission.
10. `AgentExecutionFollowUpService` publishes one idempotent, evidence-backed
    completion message into the originating conversation.

## Existing design that should remain

- Stable user-owned conversation IDs and PostgreSQL LangGraph checkpoints.
- Platform permission checks before tools, preparation, approval, and
  execution.
- Live and environment-scoped Oracle artifact catalogs.
- Exact artifact validation before execution; model text is never an Oracle
  identifier by itself.
- Guided operation input schemas and operation-specific normalization.
- Explicit human approval with an immutable payload checksum and idempotent
  decision record.
- Credential-free durable queue payloads.
- Worker leases, heartbeat, active-target conflict prevention, workflow
  evidence, and recovery-required behavior.
- Deterministic Oracle REST payload construction and job polling.
- Request correlation IDs and redacted API errors.

## Root causes

### R1 — Multiple competing language routers

Severity: Critical

The same message is interpreted independently by:

- regex-heavy `AgentTaskInterpreter`;
- keyword-based `AgentIntentRouter`;
- numerous operation-specific `_deterministic_*` methods in `graph.py`;
- the provider system prompt and model tool selection.

Equivalent wording can therefore expose the correct tools but fail to create
task state, or create the correct task state but miss the graph's separate
operation phrase check.

Recommended repair: introduce one canonical capability vocabulary and one
structured interpretation result. Keep deterministic safety checks, but make
them consume the canonical task instead of parsing the sentence again.

### R2 — Task state exists, but is not authoritative enough

Severity: Critical

`task_context` persists an intent, phase, parameters, missing fields, and
objective. This is a useful foundation. It does not retain a task identity,
canonical capability, resolved entity, pending slot contract, recent entity
references, or parameter provenance. Context merge is strongest only while the
phase is `COLLECTING_INFORMATION`; corrections after other phases can fall back
to raw-history inference.

Recommended repair: strengthen the existing task payload rather than adding a
parallel workflow. Add canonical capability, entity resolution, explicit
pending slot, parameter deltas/provenance, and lifecycle transitions.

### R3 — Pending chat answers are operation-specific

Severity: High

`_continue_pending_chat_reply` accepts an exact clarification option, cancel,
or a single numeric Business Rule RTP. Answers such as a file name, year,
period, scenario, or a multi-RTP mapping are not handled generically and may be
rejected until the user interacts with the card.

Recommended repair: use the pending input field schema as the answer contract.
Resolve a short answer into the currently expected field, validate it, update
only that field, and retain the same interrupt.

### R4 — Entity resolution is safe but fragmented

Severity: High

`AgentCapabilityGateway.resolve_artifact_choice` provides strong exact
resolution against current catalogs, and `recommend_artifacts` provides useful
lexical ranking. Graph branches still independently infer operation types and
artifact mentions. There is no single result type representing exact match,
one safe candidate, ambiguity, absence, or unavailable catalog.

Recommended repair: create one entity resolver returning observable candidate
facts. Execution remains allowed only for a canonical catalog identifier.

### R5 — Canonical operation codes are presentation-oriented, not a complete
language contract

Severity: High

The platform has stable operation codes such as `business-rules` and
`data-integrations`, but it does not yet have a unified action vocabulary such
as list, inspect, execute, update, status, or explain. Business goals such as
forecast seeding are handled in separate code paths.

Recommended repair: add canonical capabilities that map to the existing
operation codes and executors. Do not rename working executor contracts.

### R6 — Long-conversation continuity has no compact durable summary

Severity: Medium

Messages are loaded with a configurable limit. Groq additionally trims input
to a token budget. LangGraph task context protects some active-task data, but
there is no compact conversation summary or explicit recent entity/file/job
memory. Once old messages are outside the window, unstructured references may
be lost.

Recommended repair: keep structured task/entity/execution context as the source
of truth and later add a bounded non-authoritative dialogue summary.

### R7 — Lifecycle names are present but transitions are mostly recomputed

Severity: Medium

`AgentTaskPhase` already includes collection, approval, execution, Oracle wait,
completion, failure, and cancellation states. Most phases are inferred again
from intent and missing parameters rather than transitioned by explicit events.

Recommended repair: define allowed transitions and update them on artifact
resolution, input validation, approval, queue submission, worker completion,
and cancellation.

### R8 — Agent-facing error classification is incomplete

Severity: Medium

The backend has domain exception classes, but graph tools commonly catch a
generic exception and return its message, while the HTTP layer returns one
generic agent heading. There is no stable error category carried from
interpretation through execution evidence.

Recommended repair: add a safe error category/code and correlation metadata
without exposing traces or credentials.

### R9 — Observability records outcomes, not the complete decision lifecycle

Severity: Medium

The application logs request IDs, selected intent, task phase, missing fields,
tools, approvals, execution IDs, and worker results. It does not emit one
structured decision record containing context source, canonical capability,
parameter delta, entity candidates, validation outcome, and transition.

Recommended repair: introduce structured decision events keyed by request,
conversation, task, and execution IDs.

### R10 — Compatibility fallback is incomplete for dimensions and members

Severity: High

An environment-scoped artifact registry already exists for executable Oracle
objects. Dimension/member validation currently prefers live APIs and safely
blocks when old Planning versions cannot expose the required metadata. There
is no equivalent verified metadata snapshot or administrator-defined variable
schema fallback yet.

Recommended repair: reuse the registry pattern for metadata snapshots and
variable definitions. Unsupported endpoints may use a fresh verified snapshot;
authentication and permission failures must not silently fall back.

### R11 — Evaluation is deterministic but too narrow

Severity: High

The release suite protects curated routing, task extraction, and artifact
ranking. It does not yet measure false execution, negation, informational
questions, ordinal references, repeated execution, multilingual/Hinglish
paraphrases, pending-slot continuation, or recovery after correction.

Recommended repair: evolve the suite around canonical interpretation and
multi-turn state transitions. Keep false execution and incorrect parameters as
hard release blockers.

## Incremental implementation sequence

1. Add a canonical capability registry that maps to existing operation codes,
   permissions, risk, artifact type, and required slots.
2. Add a strict structured interpretation model. The model may interpret
   language, but it cannot produce executable Oracle payloads.
3. Strengthen the existing checkpointed task context with canonical
   capability, action mode, resolved entity, pending slot, parameter delta,
   and provenance.
4. Add a context resolver with deterministic priority: current explicit value,
   active task, recent resolved context, configured defaults, clarification.
5. Add one reusable entity resolver over `AgentCapabilityGateway` catalogs.
6. Make the graph consume canonical task state and gradually retire duplicate
   sentence parsing after equivalent regression coverage exists.
7. Add safe error categories and structured decision tracing.
8. Add metadata snapshot/variable-schema providers for older environments.
9. Expand evaluation metrics and adversarial/multilingual cases.

No execution, queue, Oracle client, authentication, or frontend interface
should be replaced merely to complete this refactor.

## Implemented foundation

The first incremental hardening slice is now implemented without changing the
existing Oracle executors, approval endpoints, queue schema, worker, scheduler,
or frontend contracts.

### Canonical language contract

- `app/agent/canonical.py` defines one capability and action-mode vocabulary.
- Executable capabilities translate to existing operation codes; they do not
  introduce a second execution framework.
- Explicit product concepts such as Business Rule, Data Map, Pipeline, and
  Metadata Import are recognized centrally. Implicit business wording uses the
  structured semantic interpreter instead of additional operation-specific
  sentence branches.
- The Pydantic interpretation schema rejects unknown fields, including an
  attempted low-level Oracle payload.

### Structured task context

- `app/agent/context.py` adds a stable task ID, canonical capability, action
  mode, parameter provenance, one pending-slot contract, entity-resolution
  status, and recent-reference boundary to the existing checkpointed
  `task_context`.
- The legacy intent remains in the payload during migration so existing graph
  behavior stays compatible.
- Explicit current values override compatible active-task values. Different
  capabilities start a new task and cannot inherit unrelated parameters.
- While a task is waiting for user information, an unclear short response
  retains the active checkpoint rather than creating a new task or making an
  unnecessary model request.

### Structured semantic fallback

- `app/agent/semantic_interpreter.py` is invoked only when deterministic task
  understanding and explicit canonical recognition cannot resolve the turn.
- Groq and Gemini are configured to require the single structured
  interpretation function when this fallback is used.
- Output is non-executable and schema-validated. Entity names and parameter
  values remain unverified candidates.
- Negated, informational, list, and explanatory requests cannot enter the
  canonical execution path.

### Entity resolution and pending fields

- `app/agent/entity_resolution.py` produces explicit `exact`,
  `one_candidate`, `ambiguous`, `not_found`, and `catalog_unavailable`
  outcomes against the current platform catalog.
- Only an exact catalog result becomes a canonical Oracle identifier. A fuzzy
  single candidate still requires confirmation.
- One simple pending text, choice, number, boolean, or key/value field can be
  answered conversationally. Multi-field inputs and all file inputs remain on
  the governed card.

### Compatibility and validation

- Variable definitions, scopes, and concurrency values still come from live
  Oracle state. Proposed substitution/user-variable values now use local
  scalar and well-known dimension-type checks without requiring live member
  enumeration. Unknown member text is allowed to the governed approval and
  Oracle remains the final validator; obvious conflicts such as
  `Scenario = FY28` are still rejected before submission.
- `app/agent/metadata_fallback.py` adds an environment-scoped verified
  metadata snapshot contract for old/on-premises Planning versions whose
  dimension/member APIs are unavailable.
- A fallback must be fresh and environment-compatible. Authentication and
  authorization failures never use it, and stale or missing metadata fails
  closed.

### Errors and observability

- `app/agent/errors.py` classifies provider limits, authentication,
  authorization, validation, catalog, conflict, timeout, upstream, and
  internal failures with safe messages and retryability.
- `app/agent/observability.py` emits a bounded structured decision event with
  conversation ID, task ID, canonical capability, phase, parameter sources,
  entity status, routed intent, and allowed tools. Prompts, credentials, and
  low-level payloads are excluded.
- Conversation/capability errors are no longer mislabeled as model-provider
  outages.

### Execution, persistence, and asynchronous jobs

These proven components were intentionally retained:

- stable environment/user/conversation LangGraph thread identity;
- PostgreSQL checkpointer and interrupt resumption;
- immutable approval checksum and idempotent decision records;
- permission checks and least-privilege tool exposure;
- deterministic operation input normalization and Oracle REST construction;
- credential-free durable execution queue;
- leases, heartbeat, duplicate-target protection, and recovery-required state;
- Oracle job polling, workflow evidence, and idempotent completion follow-up.

No database migration was needed for this slice because the strengthened
active-task state is already persisted in LangGraph checkpoints and execution
state already has durable relational persistence.

## Verification

- Complete Python suite: **874 passed**.
- Deterministic agent release evaluation: **43/43 passed**, including
  **40/40 critical** cases.
- New coverage includes canonical mapping, strict schema rejection, Hinglish
  semantic interpretation with a fake provider, negation, false-execution
  prevention, task identity and correction provenance, exact/ambiguous entity
  resolution, generic single-slot continuation, safe error categories, and
  old-version metadata fallback rules.

## Known limitations and deliberately deferred work

- Natural-language accuracy is not claimed to be 100%. All executable values
  still require deterministic catalog and input validation.
- The verified Planning metadata snapshot has a production-safe contract and
  validation behavior, but this slice does not add an administrator UI or a
  new database table that populates snapshots. Data Review and other features
  that require enumerated members continue to fail closed until such a
  provider is wired. Variable value entry no longer depends on that endpoint.
- The semantic fallback adds a small model call only for unresolved language.
  Free provider quotas may still limit throughput; provider-limit errors are
  now classified and retryable.
- Compact recent-entity/file/job memory is represented in task context but is
  not yet populated for every completed operation. Existing durable execution
  ownership and recent-history tools remain authoritative.
- Generic conversational slot filling intentionally does not accept uploads,
  complex Pipeline inputs, or multiple ambiguous fields.
- Old operation-specific deterministic parsers have not yet been deleted.
  They remain compatibility fallbacks until equivalent canonical and
  multi-turn evaluation coverage is broad enough for safe removal.
- Live-model multilingual quality must be measured with a controlled staging
  evaluation set; deterministic tests validate the boundary and safety
  behavior, not a third-party model's future wording quality.

## Recommended next increments

1. Persist and administer verified metadata snapshots per environment, with
   expiry, source, checksum, and audit ownership.
2. Populate recent resolved entity/file/job references after catalog
   selection and terminal execution, then add tests for “same file,” “run it
   again,” and ordinal choice across checkpoint reloads.
3. Add explicit lifecycle transition events from approval through worker
   completion to the checkpointed task summary.
4. Add staging-only live-model multilingual evaluations and operational
   metrics for false execution, incorrect parameters, hallucinated entities,
   repeated questions, and unexpected task resets.
5. Retire redundant sentence parsers and consolidate prompt text only after
   the expanded release gate proves equivalent behavior.

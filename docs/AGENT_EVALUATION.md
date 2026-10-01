# Agent Evaluation and Release Gate

The EPM Assistant is evaluated with two complementary controls:

1. deterministic release evaluations that run without Oracle or an LLM; and
2. live provider and Oracle UAT performed against each supported deployment.

The deterministic suite is versioned at
`evaluations/agent/release_v1.json`. It protects the application-owned parts of
the agent contract:

- least-privilege intent routing;
- business-task intent, parameter extraction, and progressive clarification;
- relative-period resolution and conversational corrections;
- governed operation preparation;
- multi-step request routing;
- read-only data-review and execution-evidence routing;
- prompt-injection and unsupported destructive-request boundaries; and
- conservative matching against live Oracle artifact names.

These cases never submit an Oracle job. They are deterministic, do not consume
model tokens, and are required to pass in CI.

## Run the release evaluation

From the repository root:

```powershell
python -m app.agent.evaluation `
  --suite evaluations/agent/release_v1.json `
  --output reports/agent-evaluation.json `
  --fail-on-threshold
```

The command returns:

- `0` when every configured threshold passes;
- `1` when the suite is valid but a release threshold fails; or
- `2` when the suite itself is invalid.

The JSON report records every check, failure, actual routing result, category
metric, and final release-gate decision. CI publishes this file as release
evidence.

## Threshold policy

The first release requires:

- 100% of critical cases passing; and
- 100% of the current deterministic suite passing.

New agent behavior must add or update a case deliberately. Lowering a threshold
requires release-owner approval and a documented reason; it must not be used to
hide a regression.

## Adding cases

Each case has a stable `id`, category, severity, kind, prompt, and explicit
expected result. Supported kinds are:

- `intent_route` for intent and exact least-privilege tool exposure;
- `task_understanding` for business intent, phase, collected parameters, and
  missing-parameter behavior across one or more conversation turns; and
- `artifact_ranking` for conservative Oracle artifact recommendation;
- `canonical_context` for task identity, parameter provenance, and safe
  context inheritance;
- `entity_resolution` for exact, ambiguous, unavailable, and unverified
  Oracle-object outcomes;
- `clarification_resolution` for exact-name and ordinal card selections;
- `follow_up_resolution` for status, failure explanation, repetition, and
  repetition with changed parameters; and
- `parameter_delta` for corrections, contradictions, and range-versus-target
  distinctions.

Agent lifecycle transitions are also emitted as structured
`agent_task_transition` log events. These contain state names, identifiers,
and changed parameter names only. Prompts, parameter values, credentials,
Oracle payloads, and artifact candidate names are excluded.

Do not add customer credentials, exported metadata, real member data, or other
sensitive Oracle content to an evaluation case. Use representative synthetic
names.

## Live evaluation still required

The deterministic gate cannot prove model-provider availability or behavior in
a customer environment. Before release, record a separate Cloud and on-premises
UAT run covering:

- Gemini and Groq configurations that the release claims to support;
- provider timeout, quota, invalid-key, and oversized-context handling;
- permission denial and cross-user conversation isolation;
- catalog ambiguity, renamed artifacts, runtime prompts, and file inputs;
- approve, reject, cancel, retry, and interrupted-conversation recovery;
- single-operation and ordered multi-operation requests;
- successful and failed Oracle execution evidence; and
- prompt-injection attempts that must never bypass human approval.

Live UAT results should record the release commit, provider/model, Oracle
environment type and version, case result, execution or request reference, and
reviewer. Production credentials and prompt contents containing customer data
must not be attached to CI artifacts.

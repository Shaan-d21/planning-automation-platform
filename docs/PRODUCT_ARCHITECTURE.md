# EPM Product Architecture

## Decision

EPM AI Assistant is a modular monolith. Authentication, authorization, agent
orchestration, approvals, scheduling, execution queues, audit, notifications,
and deployment remain shared. Each Oracle EPM business process contributes
operations through an allow-listed product provider.

The first provider is Planning. Financial Consolidation and Close (FCCS) is
enabled for a deliberately read-only foundation and contributes no executable
operations.

## Product selection

The platform classifies a selected Oracle application only from application
metadata returned by Oracle. Application names and user prompts are never used
to infer the business process. Recognized values are persisted as `PLANNING`,
`FCCS`, or `UNKNOWN`.

An `UNKNOWN` application inherits no product-specific operations. This is a
deliberate fail-closed behavior.

During the transition, legacy Planning environments that omit application-type
metadata retain the existing Planning compatibility path. An application that
Oracle explicitly identifies as FCCS receives only the FCCS read navigation;
Planning operations are never offered against it.

## Provider contract

Providers declare:

- a stable `BusinessProcessType`;
- whether the provider is enabled; and
- the exact governed operation definitions contributed by the provider;
- product navigation entries; and
- product capabilities and agent-tool allow-lists.

Stable operation codes, routes, risk levels, and presentation labels are part
of the Planning compatibility contract. Existing imports continue to consume
`OPERATION_DEFINITIONS`, which now resolves through the Planning provider.

## Current state

- Planning is enabled and exposes the unchanged release operation catalog.
- FCCS is enabled for verified read-only navigation and exposes no operations.
- Runtime operation catalogs, navigation, agent tools, preflight, action
  handoff, and worker startup consume the active product composition.
- Common EPM capabilities and shell navigation are defined independently from
  Planning workflow, Data Explorer, scheduling, and operation capabilities.
- Oracle environment selection retains the verified business-process type.
- The environment configuration API exposes product classification without
  exposing the Oracle base URL or credentials.
- Regression tests lock the exact Planning operation and navigation contracts
  and prevent `UNKNOWN` or unrelated providers from inheriting Planning
  operations, navigation, or agent tools.
- The FCCS module contains a read-only service contract and guarded API/UI for
  verified connection/application identity, plan types and dimensions, saved
  job definitions and job status, and bounded consolidation-journal retrieval.
  It contains no FCCS write method.
- The durable worker remains idle for a provider that exposes no executable
  operations, preventing stale Planning work from running in an FCCS
  deployment.
- Oracle applications now have normalized, non-secret registry records,
  explicit user memberships, and a durable active-application field on each
  platform session. Existing deployments are backfilled into one default
  workspace without changing their runtime behavior.
- The public workspace catalog is read-only for this increment. Switching is
  intentionally disabled until Oracle clients, provider composition, durable
  work, and product-owned records resolve their application from the request
  or queued execution rather than process-global settings.
- Authenticated requests now resolve an authorized runtime application context
  from the durable session. Bootstrap identity, navigation, product
  capabilities, standalone operation presentation, Planning route guards, and
  FCCS read-only Oracle clients use that context instead of process-global
  product composition.
- Write services, schedules, the agent, and durable workers remain bound to the
  deployment application. This deliberate split prevents a session selection
  from redirecting a write until application ownership is persisted on every
  proposal and queued execution.

The FCCS read contract follows Oracle's supported public REST resources:

- [Cloud EPM REST API support matrix](https://docs.oracle.com/en/cloud/saas/enterprise-performance-management-common/prest/all_rest_apis_table.html)
- [Get Dimensions for a Plan Type](https://docs.oracle.com/en/cloud/saas/enterprise-performance-management-common/prest/GUID-185E11F9-8420-414A-B2EA-9098767FC24F.pdf)
- [Manage Jobs](https://docs.oracle.com/en/cloud/saas/enterprise-performance-management-common/prest/manage_jobs.html)
- [Retrieve FCCS Journals](https://docs.oracle.com/en/cloud/saas/enterprise-performance-management-common/prest/fccs_retrieve_journals.html)

## Incremental migration rule

Existing services are not moved merely to create a new folder structure.
Common, Planning, and FCCS behavior is extracted only when an implementation
change requires it. Every extraction must retain the Planning contract and
pass the complete Planning regression suite.

## FCCS release gate

FCCS selection is enabled for the read-only workspace. Production acceptance
still requires live UAT for connection, application discovery, dimensions,
jobs, and journals against the target FCCS environment. Write operations stay
disabled until each operation receives its own supported Oracle contract,
authorization policy, review flow, audit evidence, and live UAT.

## Multi-application release gate

Application registration alone does not make this deployment multi-
application. Before the UI switcher is enabled, every request and queued job
must carry an authorized application identifier; Oracle clients and product
providers must be created from that context; and conversations, catalogs,
schedules, artifacts, uploads, reports, and execution evidence must be scoped
to it. Until those conditions are met, a session is bound to the deployment's
existing active application and cannot switch through the public API.

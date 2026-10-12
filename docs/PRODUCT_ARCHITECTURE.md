# EPM Product Architecture

## Decision

EPM AI Assistant is a modular monolith. Authentication, authorization, agent
orchestration, approvals, scheduling, execution queues, audit, notifications,
and deployment remain shared. Each Oracle EPM business process contributes
operations through an allow-listed product provider.

The first provider is Planning. Financial Consolidation and Close (FCCS)
provides a bounded read foundation plus separately governed executable
operations for consolidation and documented journal workflow actions.

## Product selection

The platform classifies a selected Oracle application only from application
metadata returned by Oracle. Application names and user prompts are never used
to infer the business process. Recognized values are persisted as `PLANNING`,
`FCCS`, or `UNKNOWN`.

An `UNKNOWN` application inherits no product-specific operations. This is a
deliberate fail-closed behavior.

During the transition, legacy Planning environments that omit application-type
metadata retain the existing Planning compatibility path. An application that
Oracle explicitly identifies as FCCS receives FCCS navigation and FCCS agent
tools; Planning operations are never offered against it.

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
- FCCS exposes verified overview, dimension, job, and journal reads plus the
  governed `fccs-consolidation` and `fccs-journal-action` operations.
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
  Its write service accepts only `SUBMIT`, `APPROVE`, `POST`, `UNPOST`, or
  `REJECT`, re-reads the exact journal POV and reviewed status, and submits only
  after explicit approval.
- The FCCS consolidation service permits only live-discovered `RULES` jobs
  identified as consolidation rules, requires exact Scenario, Year, Period,
  and Entity values, rechecks the rule before submission, and monitors the
  returned Oracle job to a terminal status.
- Oracle applications now have normalized, non-secret registry records,
  explicit user memberships, and a durable active-application field on each
  platform session. Existing deployments are backfilled into one default
  workspace without changing their runtime behavior.
- The workspace catalog now exposes a fail-closed session switcher. The
  deployment application remains fully available, and registered FCCS
  applications may be selected because their Oracle services are request-
  scoped. FCCS may reside at a different registered Oracle base
  URL: the selected workspace supplies both its verified URL and application
  name to the request context. Additional Planning applications remain visible
  but unavailable until every Planning write service is request-scoped.
- A System Administrator can submit another Oracle environment URL for live
  discovery with the deployment-owned service credential. The URL is
  normalized and retained server-side, while browser responses expose only a
  neutral environment label and verified application metadata. Removing a
  secondary application deactivates its memberships and active sessions but
  preserves historical executions and audit evidence. The deployment
  application remains protected while it is configured by server environment.
- Authenticated requests now resolve an authorized runtime application context
  from the durable session. Bootstrap identity, navigation, product
  capabilities, standalone operation presentation, Planning route guards, and
  FCCS read-only Oracle clients use that context instead of process-global
  product composition.
- Agent and identity-access services are request-scoped and cached by the
  active registered application. FCCS receives only its provider allow-list;
  Planning tools remain unavailable. Durable approved work carries the active
  application ID so the worker resolves the exact Oracle URL and application.
- Oracle identity providers, synchronized people, entitlements, and mappings
  are keyed by environment and application. Access Control displays the active
  application's Oracle-linked profiles separately while retaining local
  recovery accounts across application workspaces.
- EPM Assistant conversations are now owned by an authorized Oracle
  application. Conversation lists, messages, approvals, clarifications,
  inputs, drafts, and deletion fail closed when the active session selects a
  different application. Existing attributable conversations are backfilled;
  unattributable legacy rows remain hidden from application-scoped APIs.
- Durable execution queue entries and workflow evidence now retain the exact
  application identity. Workers verify that identity against their configured
  Oracle endpoint and refuse cross-application work before making an Oracle
  call. Active-target concurrency is isolated per application.
- Automation schedules now retain the registered application identity. Browser
  reads and mutations are application-scoped, agent-created schedules inherit
  their conversation application, due-work polling is restricted to the
  worker's exact Oracle environment/application, and submitted executions carry
  the schedule application into the durable queue.
- Temporary uploads are now owned by both the authenticated platform session
  and its active registered application. Resolution and terminal cleanup require
  the same ownership tuple, while logout safely clears every upload owned by
  that session. Queued work already retains its application identity.
- Saved report/Data Explorer definitions and generated Excel workbooks now use
  application-specific persistent namespaces. The deployment application's
  existing JSON catalog is copied once into its scoped catalog; other
  applications start empty and cannot resolve that catalog or its downloads.
  Report operations carry the active application into the durable queue, and
  Data Review resolves the catalog and Oracle client settings from the active
  request context.
- Jobs history, execution detail, operation/process logs, agent execution
  evidence, dashboard run metrics, and retained Oracle rejection/log artifacts
  now resolve only workflow rows owned by the active registered application.
  Newly retained Oracle files use application-specific persistent directories;
  legacy files are visible only to the deployment application and only through
  an attributed workflow. Unattributed legacy workflow rows fail closed.
- Local Pipeline, Data Integration, Planning Process, and Planning Cycle
  allow-lists now use persistent application-specific namespaces. Only the
  deployment application receives the legacy JSON seeds. Process Designer
  versions and run presets use application-qualified storage identities, so
  two applications can safely publish the same visible process code; existing
  designer records are adopted only by the deployment application.
- Operational Planning cycles now belong to one registered Oracle application.
  Their stages, tasks, execution attempts, validation evidence, approvals, and
  notifications are resolved through that root ownership and fail closed in a
  different workspace. The disconnected first-generation process scheduler
  and its table were removed; the active `automation_schedules` service is the
  only unattended scheduling runtime.

The FCCS read contract follows Oracle's supported public REST resources:

- [Cloud EPM REST API support matrix](https://docs.oracle.com/en/cloud/saas/enterprise-performance-management-common/prest/all_rest_apis_table.html)
- [Get Dimensions for a Plan Type](https://docs.oracle.com/en/cloud/saas/enterprise-performance-management-common/prest/GUID-185E11F9-8420-414A-B2EA-9098767FC24F.pdf)
- [Manage Jobs](https://docs.oracle.com/en/cloud/saas/enterprise-performance-management-common/prest/manage_jobs.html)
- [Retrieve FCCS Journals](https://docs.oracle.com/en/cloud/saas/enterprise-performance-management-common/prest/fccs_retrieve_journals.html)
- [Perform FCCS Journal Actions](https://docs.oracle.com/en/cloud/saas/enterprise-performance-management-common/prest/fccs_perform_journal_actions.html)
- [Execute an Oracle EPM Job](https://docs.oracle.com/en/cloud/saas/enterprise-performance-management-common/prest/execute_a_job.html)

## Incremental migration rule

Existing services are not moved merely to create a new folder structure.
Common, Planning, and FCCS behavior is extracted only when an implementation
change requires it. Every extraction must retain the Planning contract and
pass the complete Planning regression suite.

## FCCS release gate

FCCS selection and product-scoped assistant reads are enabled. Production
acceptance still requires live UAT for connection, application discovery,
dimensions, jobs, journals, consolidation execution, and every allowed journal
transition against the target FCCS environment. All other write operations stay disabled until each
receives its own supported Oracle contract, authorization policy, review flow,
audit evidence, and live UAT.

## Multi-application release gate

Application registration alone does not make every application executable.
The public switcher currently permits the deployment application and assigned
FCCS applications, including FCCS applications registered from a
different Oracle environment URL. Every selection is persisted on the
authenticated session and resolves both URL and application through membership
before navigation or Oracle reads are composed. Enabling a second Planning
application still requires request-scoped agent, operation-manager,
schedule-coordinator, artifact/RTP, variable, and Oracle file services; until
then that target is returned as unavailable and a direct switch attempt
receives a conflict response.

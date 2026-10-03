# EPM Product Architecture

## Decision

EPM AI Assistant is a modular monolith. Authentication, authorization, agent
orchestration, approvals, scheduling, execution queues, audit, notifications,
and deployment remain shared. Each Oracle EPM business process contributes
operations through an allow-listed product provider.

The first provider is Planning. Financial Consolidation and Close (FCCS) is
registered as a disabled provider with no executable operations until its
read-only Oracle contract and release controls are implemented and verified.

## Product selection

The platform classifies a selected Oracle application only from application
metadata returned by Oracle. Application names and user prompts are never used
to infer the business process. Recognized values are persisted as `PLANNING`,
`FCCS`, or `UNKNOWN`.

An `UNKNOWN` application inherits no product-specific operations. This is a
deliberate fail-closed behavior.

During the transition, legacy Planning environments that omit application-type
metadata retain the existing Planning compatibility path. An application that
Oracle explicitly identifies as FCCS cannot be selected while the FCCS
provider is disabled, preventing Planning operations from being offered
against an FCCS application.

## Provider contract

Providers declare:

- a stable `BusinessProcessType`;
- whether the provider is enabled; and
- the exact governed operation definitions contributed by the provider.

Stable operation codes, routes, risk levels, and presentation labels are part
of the Planning compatibility contract. Existing imports continue to consume
`OPERATION_DEFINITIONS`, which now resolves through the Planning provider.

## Current state

- Planning is enabled and exposes the unchanged release operation catalog.
- FCCS is registered but disabled and exposes no operations.
- Oracle environment selection retains the verified business-process type.
- The environment configuration API exposes product classification without
  exposing the Oracle base URL or credentials.
- Regression tests prevent `UNKNOWN` or disabled providers from inheriting
  Planning operations.

## Incremental migration rule

Existing services are not moved merely to create a new folder structure.
Common, Planning, and FCCS behavior is extracted only when an implementation
change requires it. Every extraction must retain the Planning contract and
pass the complete Planning regression suite.

## Next increment

The next increment will make backend navigation and capability composition
consume the active provider. Planning output must remain unchanged. FCCS will
stay disabled until a read-only application connection and discovery slice is
available.

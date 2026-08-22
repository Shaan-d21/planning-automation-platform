# Phase 1 — Monthly Forecast Pipeline Capability Audit

**Audit date:** 3 August 2026  
**Platform Process:** `PR02` — Monthly Revenue Forecast  
**Oracle Pipeline:** `PIPE02` — PL_Monthly_Product_Revenue_Fcst  
**Audit type:** Read-only architecture and configuration audit

## 1. Executive conclusion

The Monthly Revenue Forecast Process already delegates nearly the complete technical lifecycle to Oracle Pipeline `PIPE02`. The platform should not maintain a second workflow sequence around this Pipeline.

The target Process should contain only:

1. Resolve the approved Oracle Pipeline and its live inputs.
2. Validate the signed-in user's permission, required values, and required files.
3. Upload local files when the Pipeline requires them.
4. Show a concise impact review and obtain approval when required.
5. Run and monitor the Oracle Pipeline.
6. Run only platform-specific post-run controls that Oracle Pipeline does not provide, such as custom reconciliation or a custom workbook output.
7. Store consolidated history, logs, outputs, actor, and trigger source.

Substitution-variable changes, cube refreshes, imports, integrations, rules, rulesets, Plan Type Maps, file operations, and Oracle Pipeline email notification should be configured in Oracle Pipeline when they are part of this lifecycle.

## 2. Evidence reviewed

The audit reviewed:

- The active and draft Process versions stored in `var/workflow_history.sqlite3`.
- Registered Pipeline records.
- Run presets and schedules.
- Recent successful and failed Process executions.
- The legacy JSON Process and Planning-cycle definitions.
- The current Process Designer, preflight, execution, scheduling, and web-interface implementation.
- Oracle's current Pipeline job-type and runtime-variable documentation.

### Live-verification limitation

The workspace `.env` currently resolves to the old on-premises endpoint, while the saved Process snapshot was captured from the Cloud environment. Therefore, this audit could not refresh `PIPE02` directly from Oracle during this session.

The stored definition is sufficient to identify the architectural duplication, but the exact job types and parameters inside each Pipeline stage must be live-verified before Phase 2 changes begin. No Oracle or platform configuration was changed during this audit.

## 3. Active Process definition

The active `PR02` version is version 2. It references `PIPE02` and contains these platform steps:

| Sequence | Platform step | Current state | Audit decision |
|---:|---|---|---|
| 1 | Validate Process Inputs | Enabled | Keep, but simplify to Pipeline input/file/RBAC validation |
| 2 | Run Oracle Pipeline | Enabled | Keep as the single technical execution action |
| 3 | Publish Reporting Data Map | Disabled | Remove from the Process because `PIPE02` already contains a reporting-push stage |
| 4 | Validate Source and Target | Disabled | Keep only as a configured platform post-run policy if business reconciliation is required |

The open draft `PR02` version 3 additionally contains a disabled `UPDATE_VARIABLES` step. This draft should not be activated in its present form. Substitution-variable updates that are part of the forecast lifecycle belong in `PIPE02`.

## 4. Stored Oracle Pipeline snapshot

The latest verified Pipeline snapshot contains five serial stages with one job per stage:

| Sequence | Oracle stage | Business meaning | Ownership decision |
|---:|---|---|---|
| 1 | Load Product Metadata | Load approved Product metadata | Oracle Pipeline |
| 2 | Product Revenue Actual Load | Load source revenue data | Oracle Pipeline |
| 3 | Copy Actual to Forecast | Seed the forecast | Oracle Pipeline |
| 4 | Calculate | Execute forecast calculations | Oracle Pipeline |
| 5 | Push Data to Reporting | Publish calculated data to reporting | Oracle Pipeline |

The fifth stage confirms that the additional platform Data Map action duplicates the intended reporting push.

### Stored runtime variables

The saved Pipeline definition exposes:

- `STARTPERIOD`
- `ENDPERIOD`
- `IMPORTMODE`
- `EXPORTMODE`
- `SEND_MAIL`
- `SEND_TO`
- `ATTACH_LOGS`

The saved snapshot does not expose a `YEAR`, `SCENARIO`, or `VERSION` variable.

The platform must not display generic Year, Scenario, or Version fields unless the live Pipeline actually exposes and consumes them. If Planning Year must control this lifecycle, the administrator should add a required `YEAR` runtime variable to `PIPE02` and map it to the appropriate Pipeline job parameter or Set Substitution Variable job.

Scenario should remain controlled by Data Integration category mapping unless the Pipeline has an explicit runtime need for a Scenario variable. Version should follow the same rule.

## 5. Operation ownership matrix

| Operation | Supported inside Oracle Pipeline | Decision for the Monthly Forecast Process |
|---|---:|---|
| Set substitution variables | Yes | Move to or confirm in `PIPE02`; remove the platform prompt and Process step |
| Metadata load | Yes | Already represented by the Pipeline; do not duplicate |
| Data Integration/data load | Yes | Already represented by the Pipeline; do not duplicate |
| Business Rule or Ruleset with RTP values | Yes | Already represented by the calculation stage; keep RTP mapping in Oracle |
| Cube refresh | Yes | Add to `PIPE02` only when the metadata load requires it |
| Forecast seeding | Yes, through rule/ruleset/job | Already represented by the Pipeline |
| Plan Type Map | Yes | Use in Pipeline when it implements the required reporting movement |
| Planning Data Map | Not identical to Plan Type Map in every design | Existing Pipeline reporting stage appears to cover it; live-confirm its job type before deleting legacy configuration |
| Oracle email and attached logs | Yes | Configure Pipeline defaults; do not ask normal users on each run |
| Custom source-target reconciliation | Not guaranteed by Pipeline success | Retain as an administrator-configured post-run policy |
| Custom form-to-Excel report | Not equivalent to standard Pipeline execution | Retain only when required as a platform output policy |
| Oracle report bursting | Yes | Prefer Pipeline when the requirement is an Oracle bursting definition |
| Local browser upload | No | Keep a minimal platform upload step that supplies the resulting Inbox file name to Pipeline |
| RBAC, approval, consolidated history | No | Keep in platform |

## 6. Input and file findings

Two active `PR02` run presets exist:

| Preset | Period | Stored file policy | Finding |
|---|---|---|---|
| Revenue Forecast Preset | Jan–Jan, FY20 | Requires `METADATA_FILE` and `DATA_FILE` uploads | Appropriate for an attended run that receives new local files |
| Monthly Revenue Preset 2 | Jan–Jan, FY20 | Requires no uploads | Suitable only when the Pipeline intentionally uses fixed/default files or an automated source |

The second preset was used by the one-time schedule. This creates a stale-file risk if Pipeline jobs point to reusable Inbox file names and no upstream stage replaces them.

Before enabling an unattended recurring schedule, select one explicit file strategy:

1. **Automated delivery:** Pipeline copies files from SFTP or Object Storage.
2. **Controlled Inbox file:** another governed process replaces the file and the platform validates file freshness before execution.
3. **Attended upload:** the user uploads every required file; this Process cannot be fully unattended.

The platform must never silently reuse an old Inbox file unless the published Process explicitly permits it.

## 7. Runtime-input redesign for this Process

Normal users should see only live Pipeline inputs.

Recommended fields after live confirmation:

| User-facing field | Source | Display rule |
|---|---|---|
| Planning year | Required Process context and optional `YEAR` Pipeline variable | Always display; pass it to Oracle when the Pipeline exposes and consumes `YEAR` |
| Start period | `STARTPERIOD` | Display when required or when the user may override the default |
| End period | `ENDPERIOD` | Display when required or when the user may override the default |
| Metadata file | Pipeline file variable | Display only for attended-upload policy |
| Data file | Pipeline file variable | Display only for attended-upload policy |
| Import/export mode | Pipeline defaults | Hide from planners; expose only to authorized consultants when override is allowed |
| Email settings | Pipeline defaults | Hide from normal users |
| Scenario | Category mapping or explicit Pipeline variable | Do not display solely because it is a Planning dimension |
| Version | Explicit Pipeline variable | Do not display unless the Pipeline consumes it |

## 8. Simplified end-user lifecycle

The resulting interface should present:

1. **Choose Monthly Revenue Forecast**
2. **Provide required business inputs and files**
3. **Review** — period, files, five Oracle stages, environment, and impact
4. **Run**
5. **Monitor** the actual Oracle stages
6. **Result** — Pipeline outcome, optional reconciliation, logs, and outputs

Users should not see separate toggles for substitution variables, cube refresh, Data Map, validation, reporting, import mode, export mode, or notification. These are administrator-owned Process/Pipeline policies.

## 9. Live Cloud verification checklist

Before Phase 2, connect the audit environment to the current Cloud endpoint and verify:

- `PIPE02` still exists and its display name matches the saved registration.
- All five saved stages still exist in the same order.
- The exact job type and parameters for every stage.
- Whether the reporting stage uses a Data Map, Plan Type Map, business rule, or another mechanism.
- Whether a Set Substitution Variable job already exists.
- Whether a cube refresh is required after the metadata job.
- Whether `YEAR` is required and how it must be mapped.
- Actual Pipeline variable validation types, required flags, lists, and defaults.
- File-variable names, configured references, allowed extensions, and consuming jobs.
- Whether the Pipeline's email settings are enabled and correctly configured.
- Whether restarting a failed Pipeline stage is available through the current REST response and platform permissions.

The platform should store a new verified Pipeline signature after this check and warn administrators if Oracle later drifts from that published signature.

## 10. Phase 2 entry criteria

Phase 2 may begin when:

- The Cloud endpoint is available to the audit session.
- `PIPE02` has been live-inspected.
- Pipeline job ownership decisions are confirmed.
- The Data Map/Plan Type Map mechanism is confirmed.
- The Year and substitution-variable design is confirmed.
- The file strategy is explicit for both manual and scheduled runs.
- `PIPE02` completes successfully when run manually with the intended variables and files.

## 11. Recommended first Phase 2 change

Do not start by changing the web interface. First update and manually test `PIPE02` so it completely owns the technical forecast sequence. Then simplify `PR02` to a published Pipeline reference with platform preflight, file handling, monitoring, audit, and explicitly configured post-run policies.

## 12. Oracle references

- [Using the Pipeline](https://docs.oracle.com/en/cloud/saas/epm-cloud/diepm/integrations_pipeline.html)
- [Pipeline Job Types](https://docs.oracle.com/en/cloud/saas/enterprise-performance-management-common/diepm/integrations_pipeline_job_types.html)
- [Editing Runtime Variables](https://docs.oracle.com/en/cloud/saas/enterprise-performance-management-common/diepm/integrations_pipeline_variables.html)
- [Set Substitution Variable Job Type](https://docs.oracle.com/en/cloud/saas/enterprise-performance-management-common/diepm/integrations_pipeline_substitution_var.html)
- [EPM Platform Job Type for Planning](https://docs.oracle.com/en/cloud/saas/enterprise-performance-management-common/diepm/integrations_pipeline_epm_platform_job.html)
- [Running the Pipeline](https://docs.oracle.com/en/cloud/saas/enterprise-performance-management-common/diepm/integrations_pipeline_run.html)

## 13. Offline-safe redesign increment completed

The first implementation increment was completed without requiring the Cloud environment:

- Newly created or revised designer-managed Processes now contain only `PREFLIGHT` and `RUN_PIPELINE` steps.
- Process Designer no longer offers Cube Refresh, Data Map, report, or substitution-variable controls around a Pipeline.
- The designer explicitly explains that Oracle Pipeline owns the technical sequence.
- Planning Year is always required as Process business context. Other standard fields are displayed only when the saved/live Pipeline exposes matching variables; Year is passed as `YEAR` when Oracle exposes it.
- Run presets launched from Process Designer no longer prompt for or submit substitution-variable changes.
- The business workspace loads the substitution-variable prompt only for an explicitly legacy Process that still declares an `UPDATE_VARIABLES` step.
- Existing Process versions and execution history were not rewritten or deleted.

The full automated test suite passes after this increment. The next step is to introduce a safe migration path for active legacy wrapper definitions, followed by consolidation of the Process configuration sources.

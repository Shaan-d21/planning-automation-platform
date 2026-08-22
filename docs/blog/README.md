# From Scripts to a Framework: Oracle EPM Planning Automation with Python

This directory contains the evolving blog series for the Oracle EPM Planning
Automation Framework. Each article documents the framework as it exists after
a completed feature milestone.

## Published drafts

1. [Part 1 — Secure Login and End-to-End Metadata Loading](part-01-foundations-login-metadata-load.md)
2. [Part 2 — Two Execution Engines and Native Planning Data Loads](part-02-epm-automate-native-data-load.md)
3. [Part 3 — Flexible Data Integration Loads Without Hardcoded File Layouts](part-03-data-integration.md)
4. [Part 4 — Operational Email Notifications That Do Not Hide Job Results](part-04-email-notifications.md)
5. [Part 5 — Executing Planning Business Rules with Runtime Prompts](part-05-business-rules.md)

6. [Part 6 — Dynamic Data Integration Pipelines with Safe Multi-File Handling](part-06-data-integration-pipelines.md)
7. [Part 7 — From Separate Commands to a Controlled Monthly Forecast Workflow](part-07-data-maps-monthly-forecast-workflow.md)
8. [Part 8 — Planning Cycle Control with Scoped Variables and Safe Pre-flight](part-08-planning-cycle-control.md)

## Planned additions

- Rulesets
- File download and error-file handling
- Scheduling and unattended execution
- OAuth 2 authentication
- AI-agent orchestration
- Deployment, packaging, and operational monitoring

## Writing conventions

- Every article starts with the business problem before introducing code.
- Code samples are taken from the working project and use placeholder
  credentials and URLs.
- Mermaid diagrams are stored inline so they render on GitHub and can later be
  exported as PNG or SVG for other blogging platforms.
- Each feature chapter documents prerequisites, execution flow, errors, tests,
  limitations, and extension points.
- Articles should be updated when a change materially affects a previously
  documented workflow.

## Implementation status documented by this series

| Capability | REST | EPM Automate | Blog coverage |
|---|---:|---:|---|
| Login and connection verification | Yes | Used within command workflows | Part 1 and Part 2 |
| Metadata import | Yes | Yes | Part 1 and Part 2 |
| Native Planning data import | Yes | Yes | Part 2 |
| Data Integration / Data Management | Yes | Yes | Part 3 |
| Existing file replacement | Yes | Yes | Part 1 and Part 2 |
| Interactive saved-job selection | Yes | Yes, using REST discovery | Part 1 and Part 2 |
| Configurable Data Integration selection | Local catalog | Local catalog | Part 3 |
| Success and failure email | SMTP | SMTP | Part 4 |
| Business Rule execution | Yes | Yes | Part 5 |
| Data Integration Pipeline execution | Yes | Yes | Part 6 |
| Dynamic Pipeline variables | Yes | Yes | Part 6 |
| Multi-file Pipeline pre-flight | Yes | Interactive REST-assisted | Part 6 |
| Repository-aware Pipeline files | Yes | Interactive REST-assisted | Part 6 |
| Planning Data Map execution | Yes | Yes | Part 7 |
| Data Map member overrides | Yes | No | Part 7 |
| Monthly Forecast orchestration | Yes | Uses selected step engines | Part 7 |
| Durable workflow history | SQLite | SQLite | Part 7 |
| Source-target form validation | Yes | REST-assisted | Part 7 |
| Scoped substitution-variable discovery and updates | Yes | Not required | Part 8 |
| Explicit substitution-variable creation | Yes | Not required | Part 8 |
| Planning Cycle pre-flight and mapping | Yes | Not required | Part 8 |
| Workflow history viewer | Local SQLite | Not required | Part 8 |
| Saved Cube Refresh execution | Yes | Not required | Part 8 |

The matrix describes the framework at the end of Part 8. It does not imply
that every possible Oracle option is exposed yet; each article states its
tested scope and current limitations.

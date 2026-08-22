# BISP EPM React Frontend

This directory contains the modern React and TypeScript presentation layer for
the BISP Solutions Oracle EPM Automation Platform. It is intentionally separate
from the Python backend and can be developed, tested, built, and deployed
without moving Oracle integration logic into the browser.

## Architecture boundary

The frontend is responsible for presentation, navigation, input collection,
loading states, notifications, and accessible user interaction. FastAPI remains
responsible for authentication, authorization, CSRF validation, Planning-cycle
rules, task dependencies, database access, audit history, and every Oracle EPM
request.

The React application consumes one versioned contract rooted at `/api/v1`.
Representative endpoints include:

- `GET /api/v1/bootstrap` — product, session, user, permissions, navigation,
  environment, and feature availability
- `GET /api/v1/home` — role-filtered work, active cycles, and recent activity
- `POST /api/v1/session` — platform sign-in
- `GET /api/v1/operations/...` — governed operation catalogs and execution
- `GET /api/v1/schedules` — durable Planning-process schedules
- `GET /api/v1/data-review/...` — live read-only Planning analysis
- `GET /api/v1/agent/...` — governed assistant conversations and drafts

Older unversioned API URLs remain temporary compatibility aliases and return
deprecation metadata. New frontend code must use the typed client in
`src/api/client.ts`; components must not call `fetch` directly.

Normal product navigation stays inside React. Historical `/app/...` browser
entry points redirect to their corresponding React workspace; no duplicate
server-rendered product UI is maintained.

## Local development

Start FastAPI from the project root:

```powershell
python web_main.py
```

In a second terminal:

```powershell
cd frontend
pnpm install
pnpm dev
```

Open `http://127.0.0.1:5173`.

Set the backend redirect origin during local development:

```dotenv
WEB_FRONTEND_URL=http://127.0.0.1:5173
```

## Verification

```powershell
pnpm test
pnpm build
```

The tests cover authenticated dashboard loading, unauthenticated sign-in, and
task-status submission. The production build performs strict TypeScript
checking before Vite creates optimized static assets in `dist/`.

## Security rules

- Never place Oracle usernames, passwords, API keys, or database credentials in
  React environment variables or source code.
- Browser requests use the signed server session and send the CSRF token on
  state-changing requests.
- Navigation visibility is helpful UX, but FastAPI permissions remain the
  security boundary.
- Oracle operations continue through existing application services and governed
  backend endpoints.

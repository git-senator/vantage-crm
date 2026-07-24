# Vantage — AI Real Estate CRM

A private, single-tenant real estate CRM built on foundations that scale to
multi-tenant SaaS and an AI growth layer.

**Current state: Phase 5.** Authentication, multi-tenancy with row-level
security, RBAC and audit logging are complete; so are the CRM core, documents
and background jobs, the communication layer (notifications, email, calendar,
WhatsApp, MFA), the automation engine, and — in this phase — analytics,
reporting, the admin surface and production hardening.

See [docs/ROADMAP.md](docs/ROADMAP.md) for the phase plan.

## Documentation

| Doc | Contents |
| --- | --- |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Topology, data flow, backend layering, API structure |
| [DATABASE.md](docs/DATABASE.md) | Schema, tenancy/RLS, audit log, search, AI tables |
| [SECURITY.md](docs/SECURITY.md) | RBAC, auth flow, OWASP mapping, AI-layer risks |
| [ROADMAP.md](docs/ROADMAP.md) | Phases 0–6, exit criteria, risk register |
| [AUTHENTICATION.md](docs/AUTHENTICATION.md) | Token design, login, rotation, RLS bootstrap |
| [PERMISSIONS.md](docs/PERMISSIONS.md) | Permission registry, roles, scope resolution |
| [DOCUMENTS.md](docs/DOCUMENTS.md) | Object storage, upload workflow, file verification |
| [JOBS.md](docs/JOBS.md) | Worker architecture, queue semantics, retry and dead-lettering |
| [NOTIFICATIONS.md](docs/NOTIFICATIONS.md) | Notification centre, preferences, delivery |
| [MESSAGING.md](docs/MESSAGING.md) | Conversations, email send/receive, inbound webhook |
| [CALENDAR.md](docs/CALENDAR.md) | Events, attendees, conflict reporting, reminders |
| [MFA.md](docs/MFA.md) | TOTP, recovery codes, two-step login, role enforcement |
| [AUTOMATION.md](docs/AUTOMATION.md) | Workflow engine, triggers, actions, conditions, the builder |
| [ANALYTICS.md](docs/ANALYTICS.md) | Metric registry, scoped aggregates, snapshots, dashboards, forecasting |
| [REPORTING.md](docs/REPORTING.md) | Dataset registry, the query builder, CSV/XLSX/PDF export, scheduling |
| [HARDENING.md](docs/HARDENING.md) | Secrets at rest, malware scanning, email threading, readiness gates |
| [AI.md](docs/AI.md) | AI provider abstraction, prompt safety, redaction, cost ceilings, the ledger, the assistant |

## Stack

| Layer | Technology |
| --- | --- |
| Frontend | Next.js 16 (App Router, RSC), TypeScript, Tailwind v4, shadcn/ui |
| Backend | FastAPI, SQLAlchemy 2 (async), Pydantic v2, Python 3.14 |
| Database | PostgreSQL 16 (row-level security, `pgvector` from Phase 6) |
| Cache / jobs | Redis 7, ARQ |
| Storage | S3-compatible (MinIO locally, S3 in production) |
| Email | Amazon SES behind a provider-agnostic `NotificationService` |

## Quick start

```bash
cp .env.example .env
# Generate a real secret — the API refuses to start without one:
#   openssl rand -hex 32   →  paste into JWT_SECRET

docker compose up -d
docker compose run --rm migrate     # apply database migrations

# Create the first workspace and its owner. A fresh database has roles and
# permissions but no organization and no user, so nobody can sign in yet.
docker compose exec api python -m app.cli.bootstrap   --name "Your Brokerage" --email you@example.com

# Optional: fill the workspace with a realistic dataset for local testing or
# benchmarking (~250k rows at scale 1.0; dial down with --scale).
docker compose exec api python -m app.cli.seed_demo   --scale 0.1
docker compose exec api python -m app.cli.benchmark   # p50/p95/p99 list latency
```

- Web: <http://localhost:3000> → redirects to `/login`
- API: internal only, not published to the host (see below)

Check the stack:

```bash
docker compose ps
docker compose exec api curl -s localhost:8000/health/ready
```

### The API is deliberately not exposed

Only `web` publishes a port. The browser talks to Next.js, which proxies to
FastAPI over the compose network. This removes CORS entirely, permits
`SameSite=Strict` refresh cookies, and reduces the public attack surface to one
service. `curl localhost:8000` failing from the host is correct behaviour.

## Repository layout

```
crm/
├── frontend/            Next.js app
│   ├── src/app/         (app)/ route group + login
│   ├── src/components/  ui/ (shadcn), shared/, layout/
│   ├── src/lib/         api client, nav, format
│   └── eslint-rules/    custom client-boundary rule
├── backend/
│   ├── app/
│   │   ├── core/        config, logging, middleware, exceptions, redis
│   │   ├── db/          engine, session, RLS tenant context, ORM base
│   │   ├── api/v1/      routers
│   │   ├── services/    business logic (incl. notifications/)
│   │   ├── repositories/ data access — scoping enforcement point
│   │   ├── workers/     ARQ tasks
│   │   ├── analytics/   metric registry
│   │   ├── reporting/   dataset registry, query builder, exporters
│   │   └── automation/  workflow engine
│   ├── alembic/         migrations
│   └── tests/
├── docker/postgres/init/  role provisioning for RLS
└── docs/
```

## Development

```bash
# Frontend
cd frontend && npm install
npm run dev        # http://localhost:3000
npm run lint       # includes the client-boundary rule
npx tsc --noEmit

# Backend
cd backend
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
ruff check .
mypy app
pytest -q
```

> **VS Code:** select `backend/.venv` as the Python interpreter, or the editor
> will report every dependency as missing while the tests pass fine.

### Database migrations

```bash
cd backend
alembic upgrade head
alembic downgrade -1
alembic revision --autogenerate -m "add leads table"
```

Migrations run as `vantage_migrator`, which owns the schema. The application
connects as `vantage_app`, which owns nothing and has no `BYPASSRLS`. That
separation is what makes row-level security actually enforce tenant isolation —
table owners bypass RLS. Do not collapse the two roles.

## Three invariants worth knowing before you change anything

**1. Client components may not import data modules.** A pre-Phase-0 audit proved
that a client component importing the `notifications` array shipped internal
transaction notes and a user email to every browser. Enforcement is two-layer:
a custom ESLint rule (`boundary/no-server-data-in-client`) and `import
"server-only"` in data modules, which fails the build. Client components receive
scoped props from server components.

**2. Tenant context uses `SET LOCAL`, never `SET`.** `SET LOCAL` is scoped to a
transaction; `SET` is scoped to the connection, and connections are pooled. A
plain `SET` leaks one tenant's scope into the next tenant's request, silently.
See `backend/app/db/session.py`.

**3. The login bootstrap functions must be owned by `vantage_auth`.**
`FORCE ROW LEVEL SECURITY` subjects even the table owner to policies, so a
`SECURITY DEFINER` function owned by the migration role returns zero rows and
**every login fails** while the code looks correct. `vantage_auth` is NOLOGIN,
BYPASSRLS, and owns nothing else. See docs/AUTHENTICATION.md §5.

## Status by phase

| Phase | Scope | State |
| --- | --- | --- |
| 0 | Foundations, CI, security baseline | **Complete** |
| 1 | Auth, multi-tenancy, RBAC, audit logging | **Complete** |
| 2 | CRM core — leads, clients, properties, deals, activities, tasks, notes, timeline, dashboard | **Complete** — list p95 24.8 ms on 100k rows |
| 3 | Documents, S3, background jobs, notifications, email, calendar, WhatsApp, MFA | **Complete** |
| 4 | Automation engine — workflows, triggers, actions, conditions, the builder | **Complete** |
| 5 | Analytics, reporting, admin, production hardening | **Complete** |
| 6 | AI growth engine — infrastructure, assistant, lead/deal/property intelligence | **In progress** — 6.1 infrastructure + 6.2 assistant complete |

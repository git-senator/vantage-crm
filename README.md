# Vantage — AI Real Estate CRM

A private, single-tenant real estate CRM built on foundations that scale to
multi-tenant SaaS and an AI growth layer.

**Current state: Phase 0 complete.** Production foundations are in place —
monorepo, FastAPI backend skeleton, PostgreSQL with role separation for RLS,
Redis, S3-compatible storage, CI, and the security controls that must exist
before real data does. The frontend still renders fixture data; Phase 1
replaces that with the real API.

See [docs/ROADMAP.md](docs/ROADMAP.md) for the phase plan.

## Documentation

| Doc | Contents |
| --- | --- |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Topology, data flow, backend layering, API structure |
| [DATABASE.md](docs/DATABASE.md) | Schema, tenancy/RLS, audit log, search, AI tables |
| [SECURITY.md](docs/SECURITY.md) | RBAC, auth flow, OWASP mapping, AI-layer risks |
| [ROADMAP.md](docs/ROADMAP.md) | Phases 0–5, exit criteria, risk register |

## Stack

| Layer | Technology |
| --- | --- |
| Frontend | Next.js 16 (App Router, RSC), TypeScript, Tailwind v4, shadcn/ui |
| Backend | FastAPI, SQLAlchemy 2 (async), Pydantic v2, Python 3.12 |
| Database | PostgreSQL 16 (row-level security, `pgvector` from Phase 5) |
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
├── frontend/            Next.js app (unchanged design; data source changes in Phase 1)
│   ├── src/app/         (app)/ route group + login
│   ├── src/components/  ui/ (shadcn), shared/, layout/
│   ├── src/lib/         mock-data (server-only), nav, format
│   └── eslint-rules/    custom client-boundary rule
├── backend/
│   ├── app/
│   │   ├── core/        config, logging, middleware, exceptions, redis
│   │   ├── db/          engine, session, RLS tenant context, ORM base
│   │   ├── api/v1/      routers
│   │   ├── services/    business logic (incl. notifications/)
│   │   ├── repositories/ data access — scoping enforcement point
│   │   ├── workers/     ARQ tasks (Phase 3)
│   │   └── ai/          AI layer (Phase 5)
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

## Two invariants worth knowing before you change anything

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

## Status by phase

| Phase | Scope | State |
| --- | --- | --- |
| 0 | Foundations, CI, security baseline | **Complete** |
| 1 | Auth, RBAC, RLS, first vertical slice (Leads) | Next |
| 2 | CRM core — clients, properties, deals, activities | Planned |
| 3 | Documents, S3, background jobs | Planned |
| 4 | Analytics, admin, production hardening | Planned |
| 5 | AI layer — scoring, assistant, generation, discovery | Planned |

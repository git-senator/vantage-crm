# Vantage CRM — Target Architecture

Status: **proposed, awaiting approval**
Supersedes: nothing (first architecture document)
Related: [DATABASE.md](./DATABASE.md) · [SECURITY.md](./SECURITY.md) · [ROADMAP.md](./ROADMAP.md)

---

## 1. Governing decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Single-tenant deployment, **multi-tenant schema from day one** | `organization_id` on every business table + Postgres RLS. MVP seeds exactly one organization. Going multi-tenant becomes a provisioning change, not a migration. |
| D2 | JWT access token in httpOnly cookie, **opaque** refresh token with rotation + reuse detection | Access tokens stay stateless and short-lived; refresh tokens must be revocable, so a JWT buys nothing there. |
| D3 | **Next.js acts as a BFF**; FastAPI is never publicly exposed | Same-origin cookies, no CORS, RSC can render with user context, one place to handle refresh races. |
| D4 | Layered backend: `api → services → repositories → models` | Repositories are the single chokepoint where tenant + permission scoping is enforced. This is also where the AI layer plugs in without touching endpoints. |
| D5 | Authorization scope becomes **SQL predicates**, never post-fetch filtering | Post-filtering leaks row counts through pagination and scales O(n). |
| D6 | Types generated from OpenAPI, not hand-written twice | Kills the Pydantic/TypeScript drift risk (audit R4). |

---

## 2. System topology

```
                    ┌──────────────────────────────────────┐
  Browser  ────────▶│  Next.js 16  (public, port 443)      │
   (cookies)        │  ├─ RSC pages    → server-side fetch │
                    │  ├─ /api/[...]   → BFF proxy         │
                    │  └─ middleware   → session refresh   │
                    └──────────────┬───────────────────────┘
                                   │ internal network only
                                   │ Authorization: Bearer <access>
                    ┌──────────────▼───────────────────────┐
                    │  FastAPI  (private, port 8000)       │
                    │  ├─ /api/v1/*                        │
                    │  └─ dependency chain: authn→authz→db │
                    └───┬──────────────┬──────────────┬────┘
                        │              │              │
              ┌─────────▼───┐   ┌──────▼─────┐  ┌─────▼──────┐
              │ PostgreSQL  │   │   Redis    │  │ S3-compat  │
              │ + RLS       │   │ cache/queue│  │  storage   │
              │ + pgvector  │   │ /ratelimit │  │            │
              └─────────────┘   └──────┬─────┘  └────────────┘
                                       │
                                ┌──────▼──────┐
                                │ ARQ workers │
                                └─────────────┘
```

**FastAPI is not reachable from the internet.** It binds to the internal network only. This removes CORS entirely, allows `SameSite=Strict` on the refresh cookie, and shrinks the public attack surface to one service.

---

## 3. Data flow

### 3.1 Server-rendered page (the common case)

```
Browser GET /leads
  └─▶ Next.js RSC
        ├─ cookies() → read access token
        ├─ fetch("http://api:8000/api/v1/leads", {headers: Bearer})   ← internal, no browser
        └─ render <LeadsPage data={...} />  → HTML to browser
```

No client-side fetch, no loading spinner, no token exposed to JavaScript. This is why the existing RSC-majority component tree (audit §4) is worth preserving — it drops straight into this model.

### 3.2 Client mutation

```
Client component  POST /api/leads          (same origin, cookie auto-sent)
  └─▶ Next.js route handler /api/[...path]
        ├─ verify CSRF token
        ├─ attach access token from cookie
        └─▶ FastAPI POST /api/v1/leads
              └─ 201 → back through proxy → client
```

The BFF proxy is a **single catch-all route handler**, not one handler per endpoint — roughly 100 LOC total, not 100 files.

### 3.3 Transparent token refresh

Access tokens expire every 15 minutes. Handling this naively causes a thundering herd where ten concurrent requests each trigger a refresh and rotation invalidates nine of them.

Refresh is therefore handled in **exactly one place** — Next.js middleware — with a short Redis mutex keyed on the refresh-token family. Concurrent requests either wait on the in-flight refresh or proceed with the newly minted token. FastAPI never participates in refresh logic.

### 3.4 The client-boundary rule (fixes audit DEFECT)

The audit proved `topbar.tsx` ships the full `notifications` array to every browser because a client component imported a data module.

**Rule, enforced by lint rather than convention:** client components may not import from data-access modules. They receive scoped props from server components. An ESLint `no-restricted-imports` rule on `"use client"` files makes this a build failure, not a review comment.

---

## 4. Backend architecture

### 4.1 Layering

```
api/          HTTP concerns only: routing, status codes, dependency wiring.
              Never imports models. Never contains business rules.
    ↓
services/     Business logic, orchestration, transactions, domain events.
              Knows nothing about HTTP.
    ↓
repositories/ All data access. THE enforcement point for org scoping and
              permission scope → WHERE clauses. Returns domain objects.
    ↓
models/       SQLAlchemy ORM. Structure only, no behaviour.
```

The layering is not ceremony — it is what makes the AI layer insertable. In Phase 5, `services/lead_service.py` gains a call to `ai/scoring.py`; no router, schema, or repository changes.

### 4.2 Request dependency chain

Every authenticated endpoint resolves this chain, in order:

```python
get_access_token       # read Bearer header
  → get_current_user   # verify JWT signature, exp, jti denylist
  → get_org_context    # extract organization_id from claims
  → get_db_session     # opens tx, SET LOCAL app.current_org = <id>
  → require("lead:read")  # permission gate
  → scope_for(user)    # returns OWN | TEAM | ALL → becomes a filter
```

`SET LOCAL` is transaction-scoped, so the RLS context cannot leak across pooled connections — a classic and severe bug in tenant-scoped systems.

### 4.3 Technology choices

| Concern | Choice | Note |
|---|---|---|
| Framework | FastAPI + Uvicorn | |
| ORM | SQLAlchemy 2.0 **async** + asyncpg | |
| Migrations | Alembic | |
| Validation | Pydantic v2 | Separate `Create` / `Update` / `Read` schemas |
| Jobs | **ARQ** | Async-native, Redis-backed. Celery if complex workflow routing is ever needed. |
| Hashing | **Argon2id** | Current OWASP recommendation over bcrypt |
| Tests | pytest + **testcontainers** | See warning below |
| Packaging | uv | |

> **Testing must run against real PostgreSQL.** SQLite does not implement row-level security. A test suite on SQLite would pass green while tenant isolation does nothing whatsoever. Testcontainers is a correctness requirement here, not a preference.

---

## 5. API structure

Versioned REST at `/api/v1`. Resource-oriented, predictable.

```
Auth
  POST   /auth/login                  → sets access + refresh cookies
  POST   /auth/refresh                → rotates refresh, issues access
  POST   /auth/logout                 → revokes family, clears cookies
  GET    /auth/me                     → current user + roles + permissions
  POST   /auth/password/change

Resources  (same shape for leads, clients, properties, deals, tasks, documents)
  GET    /leads                       → list: filter, sort, cursor-paginate
  POST   /leads
  GET    /leads/{id}
  PATCH  /leads/{id}                  → partial, If-Match for concurrency
  DELETE /leads/{id}                  → soft delete
  GET    /leads/{id}/activities
  POST   /leads/{id}/convert          → domain action, not CRUD

Admin
  GET/POST/PATCH  /users, /roles, /teams
  GET             /audit-logs         → admin-only, read-only, filterable
```

### 5.1 Conventions

- **Cursor (keyset) pagination**, not offset. Offset degrades on deep pages and returns inconsistent results under concurrent writes.
- **Envelope for collections:** `{ "data": [...], "meta": { "next_cursor": "...", "has_more": true } }`
- **Explicit filter params.** No generic query DSL — that is both an injection surface and an unbounded-query performance risk.
- **RFC 7807 `application/problem+json`** for all errors, with a stable `type` URI per error class.
- **Idempotency-Key** header honoured on POST for money- and document-affecting mutations.
- **Response models are explicit.** Endpoints never return ORM objects directly; every field crossing the boundary is declared. This prevents silent field leakage when a column is added.

### 5.2 Type generation

FastAPI emits OpenAPI → `openapi-typescript` generates `frontend/src/types/api.ts` → committed and CI-verified. A backend schema change that the frontend has not absorbed becomes a failed typecheck rather than a runtime bug.

---

## 6. Repository layout

Monorepo. Frontend keeps its current structure untouched; backend is added alongside.

```
crm/
├── frontend/                    ← existing app, moved wholesale, not rewritten
│   ├── src/
│   │   ├── app/
│   │   │   ├── (app)/           ← 14 pages, unchanged layout/markup
│   │   │   ├── login/
│   │   │   └── api/[...path]/   ← NEW: BFF proxy route handler
│   │   ├── components/          ← unchanged
│   │   ├── lib/
│   │   │   ├── api/             ← NEW: typed client, replaces mock-data.ts
│   │   │   ├── auth/            ← NEW: session helpers
│   │   │   ├── nav.ts           ← gains permission predicates
│   │   │   └── format.ts        ← unchanged
│   │   ├── types/api.ts         ← NEW: generated from OpenAPI
│   │   └── middleware.ts        ← NEW: route protection + refresh
│   └── package.json
│
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── core/                config, security, exceptions, logging
│   │   ├── db/                  session, base, rls
│   │   ├── models/              SQLAlchemy ORM
│   │   ├── schemas/             Pydantic contracts
│   │   ├── api/v1/              routers, dependencies
│   │   ├── services/            business logic
│   │   ├── repositories/        data access + scoping enforcement
│   │   ├── workers/             ARQ tasks
│   │   └── ai/                  Phase 5 — scoring, rag, generation
│   ├── alembic/
│   ├── tests/
│   └── pyproject.toml
│
├── docs/                        ← this directory
├── docker-compose.yml           ← postgres, redis, minio, api, web
└── .github/workflows/
```

**The `frontend/` move is a directory relocation, not a rewrite.** Import aliases (`@/*`) are unchanged, so no source file needs editing for the move itself.

---

## 7. Frontend changes

Deliberately minimal. The audit established that ~4,500 LOC of page and component code is sound.

| Change | Scope |
|---|---|
| Move to `frontend/` | Directory move, zero file edits |
| Add `middleware.ts` | New file — route protection + refresh |
| Add BFF proxy | One catch-all route handler |
| Replace `mock-data.ts` | 18 importers switch to `lib/api/*`. Mechanical, one module at a time. |
| Rendering strategy | Static → dynamic on the 14 authenticated routes (audit R1) |
| `nav.ts` | Items gain a `permission` field; sidebar filters on it |
| Client-boundary lint rule | Fixes the audit DEFECT |
| Forms | Wire submission + validation against generated types |

**No page layout, no component, and no design token changes.** Visual output after Phase 2 should be pixel-identical to today, with real data behind it.

---

## 8. Environments

| Env | Purpose | Data |
|---|---|---|
| local | docker-compose: web, api, postgres, redis, minio | seeded fixtures |
| staging | mirrors prod topology | anonymised |
| production | single tenant, one organization row | live |

Secrets via environment variables in Phase 1, migrating to a managed secrets store before production. Never in the repository — enforced by a CI secret scanner.

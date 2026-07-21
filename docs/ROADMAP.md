# Vantage CRM — Implementation Roadmap

Six phases. Each has explicit deliverables and **exit criteria** — a phase is not
finished because the code exists, but because the criteria demonstrably pass.

Sequencing principle: **the first vertical slice goes all the way down.**
Phase 1 delivers one resource (Leads) through every layer — auth, RBAC, RLS,
repository, API, frontend, audit log, tests. Everything after it is repetition
of a proven pattern rather than exploration.

---

## Phase 0 — Foundations

*No product behaviour. Establishes the ground everything else stands on.*

**Deliverables**
- Monorepo restructure: existing app → `frontend/` (directory move, zero file edits)
- `backend/` scaffold with the layered structure from ARCHITECTURE §6
- `docker-compose.yml`: postgres, redis, minio, api, web
- Alembic wired; migration + rollback proven on an empty database
- CI: typecheck, lint, test, `pip-audit`, `npm audit`, secret scan
- Structured JSON logging with correlation IDs
- **Fix the audit DEFECT:** client-boundary ESLint rule + `server-only` markers
- **Fix the audit dependency finding:** `shadcn` → devDependencies

**Exit criteria**
- `docker compose up` yields a working stack from a clean clone
- Frontend builds and renders identically to today after the move
- CI green on a pull request
- The client-boundary rule **fails the build** when deliberately violated

**Risk addressed:** audit DEFECT, R6 (the leak must be structurally impossible before 18 modules are ported)

---

## Phase 1 — Auth, RBAC, and the first vertical slice ✅ COMPLETE

*The most important phase. Every later phase copies this pattern.*

Delivered across five commits: 1.1 authentication, 1.2 multi-tenancy,
1.3 RBAC, 1.4 audit logging, 1.5 frontend integration.

**Deviation from the original sequencing:** `organizations` was created in the
1.1 migration rather than 1.2, because `users.organization_id` is NOT NULL and
needs its FK target to exist. Creating users without a tenant and retrofitting
it later is precisely the migration churn decision D1 exists to avoid. Phase
1.2 therefore delivered the tenancy *machinery* — RLS policies, tenant context
resolution, isolation tests — rather than the table.

**Deliverables**

Database
- `organizations`, `users`, `roles`, `permissions`, `role_permissions`, `user_roles`, `teams`, `team_members`, `refresh_tokens`, `audit_logs`, `leads`
- RLS enabled and `FORCE`d on all tenant tables
- Separate migration role vs. application role; app role without `BYPASSRLS`
- Seeded: one organization, six system roles, the permission matrix

Backend
- Argon2id hashing; login with per-IP and per-account rate limiting
- JWT access (15 min) + opaque refresh (30 d) in httpOnly cookies
- **Refresh rotation with family-based reuse detection**
- `/auth/login`, `/auth/refresh`, `/auth/logout`, `/auth/me`
- Dependency chain: authn → org context (`SET LOCAL`) → permission → scope
- Repository layer with scope-as-SQL-predicate
- Full Leads CRUD + list with filtering, sorting, cursor pagination
- Audit logging on auth events and all mutations

Frontend
- `middleware.ts` — route protection + single-point refresh with Redis mutex
- BFF catch-all proxy + CSRF double-submit
- Real login page (replaces the `<Link href="/dashboard">` placeholder)
- `nav.ts` gains permission predicates; sidebar filters on them
- **Leads page reads live data** — mock-data removed for this resource only
- OpenAPI → TypeScript generation in CI

**Exit criteria**
- A user logs in, receives cookies, and reaches `/leads` with real database rows
- An `agent` sees only owned leads; a `manager` sees the team's; verified by test
- **Cross-tenant test:** a second organization is seeded and proven unreachable at the RLS layer with scoping deliberately disabled
- Refresh reuse revokes the family and raises an audit entry
- Ten concurrent requests across a token expiry boundary do **not** log the user out
- Every mutation produces an audit row
- Tests run against real PostgreSQL via testcontainers

**Risks addressed:** R1 (rendering strategy proven on one route first), R2, R3

---

## Phase 2 — CRM core

*Repetition of the Phase 1 pattern across the remaining domain.*

**Delivered so far:** 2.1 refresh concurrency (R7), 2.2 Redis rate limiting,
2.3 the Leads vertical slice — the reference implementation the remaining
entities copy — 2.4 Clients plus Lead → Client conversion, 2.5 Properties, 2.6
Deals and the Kanban board, and 2.7 Activities and Tasks. The CRM-core entities
are now complete; full-text search across them and the deletion of the last
`mock-data` fixtures are what remain before the phase exit criteria.

2.4 proved the pattern generalises: Clients is a near-mechanical copy of the
Leads slice, and the only genuinely new work was the conversion action and the
`contacts.assign` permission. Two deviations were deliberate — a client may be
a company rather than a person, and `status` is an ordinary editable field on a
client (it is not on a lead, because converting is a domain action).

2.5 found the pattern's first real limit. Leads and clients are a personal book
of business; **listings are shared inventory** — an agent holds
`properties.view` at ALL and `properties.manage` at OWN. The repository and
scope machinery absorbed that without change, which is the good news. What did
not carry over is the 404-everywhere rule: a listing the caller can see but not
edit now returns **403**, because there is no existence to conceal and a 404
there is simply a lie. See ARCHITECTURE §5.

The frontend gained its first test infrastructure in 2.5 (Vitest + Testing
Library), covering the query builder, formatters, and the property form's
payload construction — the place where a price could silently be rounded by
passing through a JS number.

2.6 delivered the central entity across two commits — 2.6a pipelines, deals and
stage history; 2.6b the Kanban board, drag-and-drop and pipeline management.
It is the first slice that was not a repetition, and the first to need a
supporting entity: `activities` was created here because a stage change has to
land on a timeline somewhere. Only `stage_change` is written; manual logging and
the other entities' timelines remain for the Activities slice.

The load-bearing decision is that **a stage transition is a domain action, not a
field edit**. `DealUpdate` has no `stage_id`, and there is a test asserting it.
`deal_stage_history` — the substrate Phase 4 velocity and cycle-time reporting
is computed from — is written on creation and on every move, with the measured
time in the stage being left, under a row lock so two concurrent drags cannot
both claim to have left the same stage.

2.6 also surfaced two pre-existing production bugs in the audit layer that had
shipped since 2.3. See the risk register.

2.7 finished the timeline 2.6 only half-built and added the last CRM entity.
`activities` gained the search vector and actor-scoped feed index it needed once
people read it rather than only the deal transition writing to it, and manual
logging arrived — a call, email, meeting, showing or note against any record.
The two rules that carry weight: an activity **has no scope anchor of its own**,
so its visibility follows the parent record through one shared `EntityAccess`
resolver rather than a duplicated predicate, and system-written `stage_change`
entries are immutable. Tasks are the first entity to anchor scope on
`assignee_id` rather than `owner_id` — work belongs to whoever must do it, not
whoever asked — and completing a task is a domain action (`POST .../complete`)
that stamps the timestamp, logs on the linked record and audits, exactly as a
deal stage transition is. Assignment carries a notification seam that logs
rather than sends; delivery is Phase 3 queued work.

**Deliverables**
- Clients, Properties, Pipelines + Stages, Deals, Activities, Tasks
- `deal_stage_history` (analytics substrate and Phase 5 training data)
- Lead → Client conversion as a domain action, transactional
- Unified activity timeline across entities
- Full-text search (`tsvector` + `pg_trgm`) on leads, clients, properties
- Detail routes the prototype never had: `/leads/[id]`, `/clients/[id]`, `/properties/[id]`, `/deals/[id]`
- Deals Kanban wired to real stage transitions (drag-and-drop persists)
- All forms submit with validation against generated types
- `mock-data.ts` **deleted**

**Exit criteria**
- All 14 routes render live data; no fixtures remain in the repository
- Visual parity with the approved prototype design maintained
- Stage transitions write history and emit activities
- Search returns correct results under RBAC scope
- p95 list-endpoint latency < 200 ms on 100k seeded rows

---

## Phase 3 — Documents, storage, background jobs

**Deliverables**
- S3-compatible storage; private bucket, public access blocked at policy
- Presigned upload/download with short TTLs
- ARQ worker pool + scheduled jobs
- Post-upload pipeline: magic-byte MIME verification, checksum, virus scan, quarantine
- Document versioning and lifecycle (`draft → awaiting_signature → signed → expired`)
- Email/notification delivery via queue
- Expiry reminders and digest jobs

**Exit criteria**
- Upload → scan → visible, end to end
- An infected fixture is quarantined and audited, never served
- Presigned URLs expire correctly and are not reusable cross-user
- Failed jobs retry with backoff and surface in a dead-letter view

---

## Phase 4 — Analytics, admin, production hardening

**Deliverables**
- Dashboard and Reports on real aggregates (materialised views where needed)
- Pipeline velocity, conversion funnel, agent production, cycle time from `deal_stage_history`
- Admin: user management, custom roles UI over `role_permissions`, team management
- Audit-log viewer (admin-only, filterable)
- TOTP MFA enforced for `owner` and `admin`
- Backup + **restore rehearsal**, monitoring, alerting, runbooks
- Penetration test and remediation

**Exit criteria**
- Every pre-production gate in [SECURITY.md §6](./SECURITY.md) is checked
- Restore from backup rehearsed end to end against a live-shaped dataset
- Dashboard p95 < 500 ms
- Pen-test findings remediated or formally accepted

**→ Production go-live for the single tenant.**

---

## Phase 5 — AI layer

*Additive. Touches services, never routers or repositories — this is what the
Phase 0 layering bought.*

**Deliverables**

5a — Infrastructure
- `pgvector`; `entity_embeddings`, `ai_jobs`, `lead_scores`, `ai_generated_content`
- Provider abstraction (no vendor lock-in at the call site)
- Per-org token quotas and hard cost ceilings enforced pre-dispatch

5b — Lead scoring
- Feature extraction from engagement, source, budget fit, response latency
- Scores written with `explanation` JSONB — honouring the product's existing
  "three signals that drove it" promise
- Rescoring on activity via queue, not on read

5c — Sales assistant (RAG)
- Embedding pipeline over org data, incrementally maintained by `content_hash`
- **Retrieval filtered by `organization_id` + user scope predicate before
  similarity search** — the same scope resolver as SECURITY §1.3, one
  implementation shared with the CRM
- Citations back to source records

5d — Generated messaging
- Email/SMS drafting from CRM context
- Always `draft` → explicit human approval → send. No autonomous sending, ever.

5e — Lead discovery
- Scheduled ingestion workers with a domain allowlist and egress proxy
- Deduplication against existing leads
- Auto-qualification proposals surfaced for human confirmation

**Exit criteria**
- **RAG leakage test:** a user provably cannot retrieve, via the assistant, any
  record they cannot retrieve via the API
- Prompt-injection fixtures in lead notes do not alter model behaviour or
  trigger privileged actions
- Per-org cost ceilings enforced and observable
- Nothing generated reaches a customer without a recorded human approval

---

## Multi-tenant activation (post-MVP, on demand)

Because of decision D1, this is **not a migration**:

- Organization signup and provisioning
- Org switcher for multi-org users
- Per-org billing, plans, quotas
- Subdomain or path-based routing
- Per-org branding

No schema change. No query rewrite. No RLS change. This is the entire return on
carrying `organization_id` from day one, and it is why that decision is worth
making before the first table exists rather than after the thirtieth.

---

## Risk register

| ID | Risk | Severity | Mitigation | Phase |
|---|---|---|---|---|
| R1 | Static→dynamic rendering change touches all 14 routes | ~~Medium~~ **CLOSED** | All 14 routes now render dynamically; only the public login page is prerendered | 1 ✅ |
| R2 | Multi-tenancy retrofit | ~~High~~ **CLOSED** | `organization_id` on every table from the first migration; RLS FORCEd and proven by 10 cross-tenant isolation tests | 1 ✅ |
| R3 | Client-boundary leak recurs during data port | **High — MITIGATED** | Lint rule + `server-only` caught a real violation during Phase 1.5 and forced a correct module split. Still live for the Phase 2 data port. | 0 ✅ |
| R4 | Pydantic/TypeScript drift | Medium | OpenAPI type generation, CI-verified | 1 |
| R5 | 33 vendored UI primitives don't auto-update | Low | Quarterly review; documented ownership | ongoing |
| R6 | 18 modules import `mock-data` | Medium — **reducing** | Typed data layer; port resource by resource. Leads (2.3), Clients (2.4), Properties (2.5) and Deals (2.6) are ported and their fixtures deleted; tasks, documents, calendar, messages and the dashboard's remaining panels are what is left | 1–2 |
| R11 | Audit metadata could not serialise `Decimal`; a failed audit write poisoned the caller's transaction | ~~High~~ **CLOSED** | Found in 2.6, live since 2.3 — reachable from any money-field edit on leads, clients or properties, and no test had changed one. Values now coerce to JSON-safe types (Decimal → string, never float), comparison happens before coercion, and the insert runs in a SAVEPOINT so an audit failure genuinely cannot break the request it describes | 2.6 ✅ |
| R12 | Read-after-write returned stale relationship state | ~~Medium~~ **CLOSED** | SQLAlchemy does not overwrite loaded state on a fresh query, so a stage transition returned the deal's *old* stage and therefore its old derived status — the Kanban card would snap back. `populate_existing` on the deal and pipeline read paths | 2.6 ✅ |
| R7 | Refresh rotation logs users out under concurrency | ~~High~~ **CLOSED** | Redis lock on the presented token plus a 10s rotation grace window. Proven by 10 genuinely parallel refreshes all succeeding, and by the suite passing with Redis deliberately unreachable | 2.1 ✅ |
| R8 | `SET` instead of `SET LOCAL` leaks tenant context across pooled connections | ~~Critical~~ **CLOSED** | `set_config(..., true)` throughout; proven by `TestTransactionScopedContext` — context does not survive the transaction on a reused connection | 1 ✅ |
| R9 | RAG retrieval bypasses RBAC | **Critical** | Shared scope resolver; pre-filter before similarity search; leakage test | 5 |
| R10 | AI cost runaway | Medium | Per-org quotas, hard ceilings, per-job cost recording | 5 |

R8 and R9 are the two failures that would be silent, severe, and hardest to
detect after the fact. Both have a dedicated test as a phase exit criterion.

---

## Sequencing rules

1. **No phase starts before the previous phase's exit criteria pass.**
2. **Security controls ship with the feature**, never as a later hardening pass.
3. **The frontend is never rewritten** — pages change data source, not design.
4. Every phase ends deployable. No long-lived branches.

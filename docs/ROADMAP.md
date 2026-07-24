# Vantage CRM — Implementation Roadmap

Seven phases. Each has explicit deliverables and **exit criteria** — a phase is not
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
Deals and the Kanban board, 2.7 Activities and Tasks, and 2.8 Notes, the unified
timeline, the attachment placeholders and the live dashboard. The CRM-core
entities are complete and every record's detail page now shows real notes,
timeline and files; the remaining `mock-data` fixtures belong to modules that
are intentionally later phases (messages, calendar, documents, reports).

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
`deal_stage_history` — the substrate Phase 5 velocity and cycle-time reporting
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

2.8 completed the record-level surface across six commits — 2.8a Notes, 2.8b
Attachments, 2.8c the unified timeline, 2.8d the dashboard aggregates, 2.8e the
frontend panels, 2.8f the live dashboard. **Notes are a separate table from
`activities`**, deliberately: an activity is a terse timeline *event*, a note is
a durable, editable *document*, and collapsing them would make one of the two
wrong. Both share the polymorphic `(entity_type, entity_id)` shape so the
**unified timeline merges them on one axis** — activity by `occurred_at`, note by
`created_at` — with no third source: task events arrive through the activities
they already generate, and the audit log stays out because it is the security
record, not the business timeline. `EntityAccess` gained a `task` case, since a
note may annotate a task.

**Attachments ship as a placeholder architecture**, not storage. A registered
file is metadata only — `pending_upload` status, a computed `storage_key` it
*will* occupy, no bytes — and the presign upload/download methods raise
`NotImplementedError` pointing at Phase 3. The point is that Phase 3's S3 layer
is an additive change behind a seam that already exists, and the attachments UI
is real today.

The **dashboard now reads live data** within the caller's scope — there is no
separate "dashboard scope", so a total can never exceed what the user could
reach by browsing — and its recent-activity panel is the same timeline feed the
records show. `mock-data.ts` was **shrunk, not deleted**: only slices with a real
backend were removed (the `leads` fixture), and the file stays for the modules
still awaiting their own phase.

**Deliverables**
- Clients, Properties, Pipelines + Stages, Deals, Activities, Tasks, Notes
- Attachments as a metadata placeholder (real object storage is Phase 3)
- `deal_stage_history` (analytics substrate and Phase 5 training data)
- Lead → Client conversion as a domain action, transactional
- Unified activity + note timeline across every entity, and a live dashboard
- Full-text search (`tsvector` + `pg_trgm`) on leads, clients, properties, deals, tasks, notes
- Detail routes the prototype never had: `/leads/[id]`, `/clients/[id]`, `/properties/[id]`, `/deals/[id]`
- Deals Kanban wired to real stage transitions (drag-and-drop persists)
- All forms submit with validation against generated types
- `mock-data.ts` **shrunk** to only the still-unbacked modules; full deletion is
  a later-phase exit criterion (documents in Phase 3, reports in Phase 5)

**Exit criteria**
- The CRM-core routes render live data; the fixtures still present belong to
  intentionally later-phase modules (documents, reports, calendar, messages)
- Visual parity with the approved prototype design maintained
- Stage transitions write history and emit activities ✅
- Search returns correct results under RBAC scope ✅
- **p95 list-endpoint latency < 200 ms on 100k seeded rows ✅** — measured at
  **24.8 ms worst-case p95** across all eleven list shapes on 102k leads /
  ~250k total rows (2.9b). `EXPLAIN` confirms every list is an index scan on the
  tenant-first `(organization_id, created_at, id)` composite with the keyset
  `LIMIT` stopping early, so latency is O(page), independent of table size; the
  full-text planner adapts between the GIN index and the ordered scan by term
  selectivity. No index or query changes were required.

**2.9 finalization.** Two developer CLIs land the performance story: `seed_demo`
generates a realistic, RLS-correct dataset (~250k rows at `--scale 1.0`, ~90 s)
spread across a pool of agents, and `benchmark` drives the repository
`list_page` path and reports p50/p95/p99 against the exit threshold. The
benchmark is the evidence the latency criterion is met, and both are the tools
Phase 5's reporting work and any future regression check reuse.

---

## Phase 3 — Documents, storage, background jobs

**3.1 Documents & storage — delivered.** The Phase 2.8 placeholder is gone and
the table it left behind did not need reshaping: five columns, two constraints,
two partial indexes, and one widened vocabulary. See
[DOCUMENTS.md](./DOCUMENTS.md).

The load-bearing decision is that **an upload is a three-step handshake**
(register → PUT straight to storage → finalize), not a multipart POST. Bytes
never traverse the API, so a 25 MB upload on a slow connection costs a signature
rather than an occupied worker. The consequence that matters more, though, is
the third step: after the client's PUT the server knows *nothing* about what
landed, so `finalize` reads the object back and establishes the real size, hash
and type from storage. It is the only path that can set `available`, and a row
that skips it stays unservable.

**Nothing a client says about its own file is stored without being checked
against what actually arrived.** An executable renamed `offer.pdf` is caught on
magic bytes, deleted from the bucket, and the row is marked `failed` with a
user-facing reason — the delete happens *before* the error is raised, so a file
that failed verification does not survive where a later bug could serve it. The
type allowlist is checked twice: at registration, so a rejected type costs one
round trip instead of a transfer, and again against the bytes, because the first
check is only as good as the client's honesty. `image/svg+xml` and `text/html`
are absent from the allowlist deliberately, and every download carries
`Content-Disposition: attachment`.

`status` and `scan_status` are separate columns because there is a real gap
between "these bytes are the type they claim" and "these bytes are not
malware", and a file must not be servable inside it. With scanning off (today's
default) finalization publishes immediately and records `skipped` — honest that
nothing looked at it. With it on, the row is held until the scan job clears it,
which is the seam 3.2 fills.

Attachments gained `note` as a parent. A note has no scope anchor of its own, so
`EntityAccess` resolves it by delegating one hop to the note's parent record —
attaching to a note cannot become a way around the lead's scope. Two smaller
decisions worth recording: **issuing a download URL is the access grant** (the
fetch never reaches this application, so `document.downloaded` is audited at
signing time), and **deletion is soft for metadata, hard for bytes** — keeping
objects behind a row marked deleted is how a deletion quietly fails to be one.

One piece of infrastructure was not obvious: SigV4 signs the `Host` header, so a
URL minted against compose's internal `minio:9000` cannot be rewritten to a
browser-reachable host afterwards. Presigning therefore runs on a second client
bound to `S3_PUBLIC_ENDPOINT_URL`. On real S3 the two collapse into one.

**3.2 Background jobs — delivered.** ARQ on the existing Redis, one worker
service, and the document pipeline moved behind it. See [JOBS.md](./JOBS.md).

The decision that shaped the phase: **a job is a service call with a different
trigger.** Job functions bind a tenant and call the same service methods a
request does, rather than reimplementing business logic with looser rules. That
sounds like style until you meet its consequence — RLS is FORCEd on every
business table, so a worker with no tenant context bound *sees nothing*. A sweep
written the obvious way runs cleanly, touches zero rows, and reports success:
silent, and indistinguishable from "there was no work to do".

The tempting fix is `BYPASSRLS` on the worker's role, which is one broad grant
to solve one narrow problem and would exempt every query the worker ever makes.
Instead a locked-down `SECURITY DEFINER` function returns the list of
organization ids and nothing else, and each tenant's work then runs under RLS
exactly like a request. The only thing crossing a tenant boundary is a list of
uuids. Services also need an actor, so machine work gets an explicit
`system_context` with ALL scope on *only* the permissions that job requires and
`user_id=None` — genuinely absent rather than a placeholder id something could
later join on, with narrow scopes failing closed rather than reading "no actor,
therefore no restriction".

**Retry and dead-lettering are not configuration.** ARQ re-queues on `arq.Retry`
and nothing else, so a job raising `ValueError` would fail once and never run
again; and its lifecycle hooks receive neither the function name, the arguments,
nor the exception, so a hook cannot write a useful failure record without
reading the result back out of Redis. Both therefore live in one `@job`
decorator, which re-raises as `Retry(defer=…)` with jittered exponential backoff
while attempts remain and writes the dead-letter row on the attempt that gives
up. Measured on the live stack: 1.48s, 3.76s, 7.65s, 9.14s, then dead-lettered.

`job_failures` holds one row per (job name, key) rather than per attempt, and a
later success resolves rather than deletes it. Its RLS policy is deliberately a
shade wider than the standard one — a tenant-less row is infrastructure, and
those are exactly the failures an operator most needs to see.

**Enqueueing is best-effort and that is a stated trade-off**, not an oversight:
a Redis blip must not turn a successful assignment into a 500, so a lost job is
genuinely lost — which is why every job here is either reconstructible from
database state (`sweep_scan_backlog` re-finds its work) or genuinely optional (a
notification). Nothing whose loss would corrupt data goes through the queue.

The scanner ships honest about its limits: `EicarSignatureScanner` detects the
EICAR test file and nothing else, and production refuses to start with scanning
enabled while it is configured. A scanner that catches nothing is worse than
none, because it looks like protection. What is real is everything around it —
verdict, quarantine, byte deletion, audit, never-served — and a scanner *outage*
leaves files unpublished rather than waved through, because "we could not check
it" is not "it is fine".

Phase 2.7's task-assignment seam is now wired to the queue, and the job re-reads
the task at send time: between assignment and delivery it may have been
reassigned or completed, and mailing someone about work that is no longer theirs
is worse than not mailing them.

**3.3 Notifications — delivered.** An in-app notification centre with per-user,
per-category delivery preferences, and the email path behind the queue. See
[NOTIFICATIONS.md](./NOTIFICATIONS.md).

The split that carries the phase: **a service says what happened and to whom;
the centre decides how it is delivered.** `TaskService` did not change when
email delivery arrived and will not change when push does — it raises a
notification and stops. Phase 3.2's dedicated task-assignment email job was
deleted in the process, replaced by one `deliver_notification_email` for every
category, because the wording that distinguishes an assignment from a quarantine
notice already exists as the notification's own title and body. Per-event email
templates are how the app and the inbox end up saying subtly different things.

**Muting hides the badge, not the history.** A muted notification is still
written, pre-read: a preference governs whether something is *surfaced*, not
whether it happened, and a user who later asks "when was I told about this?"
should get an answer. Absent preferences mean the category default rather than
off, so adding a category later cannot silently mute it for everyone.

**A notification belongs to its recipient and to nobody else** — not their
manager, not an admin at ALL scope, because "who was told what" is somebody's
inbox rather than an administrative view. That predicate lives in the repository
rather than the RLS policy, matching the system-wide split (RLS is the tenant
boundary; row visibility is a SQL predicate) — and because a notification is
created by one user *for another*, a `WITH CHECK` on the recipient would refuse
every assignment notification ever sent. The endpoints therefore take no
permission at all, and there is no create endpoint: an API that let a client
post a notification to another user is a phishing surface inside the product.

Two smaller decisions worth recording: nobody is notified about their own
action, and email defaults on only where the recipient is likely away from the
product and the thing is addressed to them personally (tasks, mentions) —
defaulting it on everywhere is how a CRM lands in a spam filter, taking its
password resets with it.

The notifications page and the topbar badge now read live data, and
`mock-data.ts` lost its last interactive fixture.

**3.4 Email integration — delivered.** Conversations, outbound send through the
queue, and an authenticated inbound webhook that files replies onto the right
record. See [MESSAGING.md](./MESSAGING.md).

The structural decision is that **email and WhatsApp are one system, not two**.
`conversations.channel` is a column rather than a table, so 3.6 is an adapter
plus an enum value that already exists — the inbox, the threading, the record
matching and the unread counts never knew which channel they were looking at.
The prototype's own inbox already listed sms, email and whatsapp in one stream,
so this is not speculative generality; two parallel implementations would have
had to be merged later, at the point where they had diverged most.

A thread is keyed by (tenant, channel, counterparty address), not by CRM record:
the same person is a lead today and a client tomorrow, and their history should
survive the conversion rather than fragment at exactly the moment it becomes
valuable. Address normalisation therefore lives on the channel — it is the
identity function for a thread, and getting it wrong gives one person two
conversations.

**Outbound records before it sends.** The message row is written in the request's
transaction as `queued`, then delivery is enqueued; a provider outage becomes a
failed message visible in the thread rather than a request that lost what the
user typed. The endpoint returns 202 rather than 201, because claiming it was
sent would be a lie the UI then has to un-tell.

**Inbound is the only endpoint in the system with no authenticated user behind
it**, and is treated accordingly: HMAC over the raw body (not the reserialised
model — that difference only shows up in production, months later), constant-time
comparison, a bounded signed timestamp, and the tenant *inside* the signature so
a validly-signed body cannot be re-pointed at another tenant. An unset secret
refuses everything rather than defaulting open. Ingestion deduplicates on the
provider id before any write, because every provider replays eventually.

Record matching is an exact address lookup or nothing — no fuzzy matching, since
filing a stranger's mail onto a customer's record is worse than leaving it
unfiled, and unfiled is visible and fixable. Mail from a stranger is kept in an
unmatched thread, which is how an inbound enquiry becomes a lead. Unowned threads
are visible to the whole tenant, because hiding unclaimed mail from everybody is
how an enquiry sits unanswered for a week; replying claims it.

The messages page reads live data and `mock-data.ts` lost its conversation
fixtures.

**3.5 Calendar — delivered.** Events, attendees, conflict reporting and a
reminder sweep. See [CALENDAR.md](./CALENDAR.md).

Two decisions carry the phase. The first is that **every calendar query is an
overlap test, not a containment one** — an event that began this morning and
runs into the afternoon belongs in the afternoon's view, and the obvious
`starts_at BETWEEN …` misses exactly the long events people most need to see.
The list endpoint requires a window for the same reason a calendar has one: an
unbounded calendar is a full-table scan dressed as a feature.

The second is that **conflicts are reported, never enforced**. Refusing a
double-booking would be the system claiming to know better than the person
holding the calendar — sometimes it is a broker covering two open houses on the
same street — while staying silent would let a clashing showing reach a client.
Tentative events neither conflict nor are conflicted with, since a pencilled-in
showing is precisely what you expect to be booked against while it is arranged,
and an event never conflicts with itself or every update would report one.

Internal and external attendees share one table, because a showing has an agent
(a user) and a buyer (an address) and splitting them would double every query
that asks who is coming. Times are instants rather than wall-clock, with all-day
as a flag over a range — one representation of time, and the timezone anchoring
that costs is the honest trade.

`reminded_at` makes a five-minute reminder sweep safe to run: stamped in the
same transaction as the notification it raises, so an event is reminded about
exactly once. Moving an event clears it, because the reminder that matters is
the one for the new time.

The calendar page and the dashboard's today panel read live data, and
`mock-data.ts` lost its event fixtures.

**3.6 WhatsApp — delivered.** One adapter, one webhook translator, one arm of
`build_channel`. Not a line of the ingestion pipeline changed, which is exactly
the return 3.4's channel-as-a-column decision was taken for. See
[MESSAGING.md](./MESSAGING.md) §7.

What is genuinely new is small and specific. Addresses are phone numbers, so
normalisation is E.164 with the `+` stripped to match what Meta sends inbound.
Record matching digit-strips the *stored* value too, because a CRM's phone column
holds every spelling a human has typed — and a number stored without a country
code deliberately does **not** match, because a trailing-suffix match files a
Colombian number onto a US contact often enough to be worse than an unfiled
thread. There is a test pinning that limitation so it stays visible.

The 24-hour session window is modelled rather than papered over: outside it
WhatsApp rejects free-form text at the API, so the adapter turns Meta's 131047
into a terminal, readable error instead of a retry that cannot possibly succeed
until the customer writes again. Sending pre-approved templates is a product
decision and is deliberately out of scope; what ships is an honest boundary.

Meta's webhook differs from the mail one in ways worth recording: the signature
is `sha256=`-prefixed, there is **no timestamp** (so dedupe on the provider id is
the only replay protection, making it a correctness requirement rather than a
nicety), and there is no tenant hint. The subscription handshake echoes
`hub.challenge` as bare text, because Meta compares the body byte for byte. The
delivery endpoint always answers 202 once the signature verifies — Meta retries
any non-2xx for hours, so erroring on a status receipt or an unsupported media
type would create an infinite redelivery loop over something that can never
succeed.

**3.7 MFA — delivered.** TOTP with single-use recovery codes, a two-step login,
and role-based enforcement. See [MFA.md](./MFA.md). This was scheduled for Phase
4 in the original plan and was pulled forward with the rest of the platform
services; Phase 5's remaining MFA work is the production hardening around it
(KMS-managed secret encryption), not the feature.

TOTP is implemented rather than imported, which is the one place in this
codebase where writing crypto is the right call: it is HMAC-SHA1 over a counter
with no key agreement and no parsing of hostile structure, and the RFC ships a
conformance vector table the tests check against — an authoritative correctness
oracle that a dependency would not add.

Enrolment is two steps because a one-step version locks people out with a secret
they never successfully scanned, and re-enrolling while enabled is refused
because it would let anyone holding a live session swap the second factor to a
device they control. Login is two steps for the same class of reason: when MFA
is on, no session and no profile are returned — not even a name, since that
would confirm the password was correct — only a short-lived token typed `mfa`
so it cannot be replayed as an access token, and vice versa.

Two details that decide whether a second factor is really one. **A used TOTP
code is dead**: a code is valid for its whole 30-second step, so `mfa_last_counter`
refuses anything at or before the last accepted one, at the price of the
legitimate user waiting for the next code. **Recovery codes are single-use
enforced by the database** — an UPDATE with `used_at IS NULL` in its predicate —
because a read-modify-write over a JSON array has a race that testing does not
catch. Spent codes are kept rather than deleted, because "one of your recovery
codes was used on Tuesday" is the signal that tells someone their phone was
compromised.

Enforcement is a nudge with teeth rather than a lockout: a user whose role
requires MFA and has not enrolled still gets a session, flagged
`setup_required`. Refusing the login would lock out the owner the moment
somebody granted them the role — in the worst case the only owner, with nobody
able to undo it.

**Deliverables**
- S3-compatible storage; private bucket, public access blocked at policy ✅
- Presigned upload/download with short TTLs ✅
- Post-upload pipeline: magic-byte MIME verification, checksum, virus scan, quarantine ✅
- Entity attachments on leads, clients, properties, deals, tasks and notes ✅
- ARQ worker pool + scheduled jobs ✅
- Email/notification delivery via queue ✅
- Conversations and email: outbound send, inbound ingestion, record matching ✅
- Calendar: events, attendees, conflict reporting, reminder sweep ✅
- WhatsApp on the same conversation substrate: adapter, webhook, session window ✅
- MFA: TOTP, recovery codes, two-step login, role enforcement ✅
- Document versioning and lifecycle (`draft → awaiting_signature → signed → expired`)
- Expiry reminders and digest jobs

**Exit criteria**
- Upload → verify → visible, end to end ✅ — proven against MinIO on the real
  S3 adapter (presigned PUT with SSE, ranged read, checksum, presigned GET with
  `Content-Disposition`, delete) and across 30 lifecycle tests
- Presigned URLs expire correctly and are not reusable cross-user ✅ — expiry,
  key tampering and GET-as-PUT are each refused by signature verification, and a
  user who cannot read the parent record never receives a URL at all
- Upload → scan → visible, with an infected fixture quarantined and audited,
  never served ✅ — an EICAR fixture is quarantined, its bytes deleted, a
  `document.quarantined` entry raised, and the download path refuses it
- Failed jobs retry with backoff and surface in a dead-letter view ✅ — measured
  on the live worker at 1.48s / 3.76s / 7.65s / 9.14s before dead-lettering,
  with the row readable at `GET /api/v1/jobs/failures`

---

## Phase 4 — Automation engine ✅ COMPLETE

*Workflows that react to what happens in the CRM. See
[AUTOMATION.md](./AUTOMATION.md).*

Four decisions carry the phase.

**A transactional outbox, not the best-effort enqueue used everywhere else.**
Every other enqueue in this codebase may be lost, because the worst case is a
missing notification. Here it is a *phantom trigger* — a workflow reacting to a
change that rolled back and emailing a real customer about something that never
happened. The event row commits in the caller's transaction; the enqueue stays
best-effort, and a sweep re-finds what it drops. `workflow_events` is
deliberately not the audit log despite firing at the same moments: one answers
*what happened, for the record*, the other *what should react*, and only the
second needs retry and replay.

**A tree, not a DAG.** One outgoing edge per node, two for a condition, no
joins. Joins are the single thing a DAG buys and they bring partial state, join
timeouts and runs half-finished in two places. Cycles are refused at publish
time rather than bounded by a step budget, because a loop that emails a client
forty times because the budget was fifty is not a smaller bug. Validation
refuses only at publish — a draft is allowed to be incoherent, or the builder is
unusable halfway through building.

**A workflow can never trigger another workflow.** Its own writes emit events
like any change, so "when a lead is updated, update the lead" is a loop whose
output is outbound email. The marker comes from the authorization context rather
than a flag threaded through every service call that one could forget.

**Authoring is administrative, and that is what makes execution safe.**
`automations.manage` is held only by owner and admin — both already at ALL scope
— so the system context a run uses is never wider than its author's. Running as
the author instead sounds tighter and behaves worse: the workflow silently stops
when they change role, and the failure is a customer who never got their
follow-up. Stated plainly: a workflow can act on records no individual agent
could see.

Smaller decisions worth recording. Actions are thin calls into existing services
— `create_task` through `TaskService`, `change_deal_stage` through `move_stage`
so stage history survives — never a second implementation with looser rules. A
missing field never matches a condition, and numeric comparisons are typed by
the operator because `"10" > "9"` has opposite answers as text and as a number.
Delays park the run with a `resume_at` and hold no worker; business-hours delays
count *working* minutes, so "wait 2 hours" from 5pm is 10am rather than 7pm.
`call_webhook` resolves the host and refuses private addresses — without that it
is an SSRF primitive with a form in the UI. Runs pin their version, so editing a
live workflow cannot change what an in-flight execution does halfway through.

The builder is a vertical chain rather than a free-form canvas, deliberately
matching the tree the backend accepts: a canvas that let you draw arbitrary
edges would let you draw workflows the engine refuses. Every control is
generated from the registry the server serves, so the palette cannot drift from
what the executor can run.

**Exit criteria**
- A workflow fires from a real CRM mutation and its action lands through the
  real service ✅
- A workflow's own writes cannot trigger another workflow ✅
- An in-flight run executes the version it started on, after the workflow is
  edited and republished ✅
- A failing step stops the run, records why, and does not run the step after ✅
- Cycles, orphans and incompatible actions are refused at publish ✅

---

## Phase 5 — Analytics, admin, production hardening

**Deliverables**
- Dashboard and Reports on real aggregates (materialised views where needed)
- Pipeline velocity, conversion funnel, agent production, cycle time from `deal_stage_history`
- Admin: user management, custom roles UI over `role_permissions`, team management
- Audit-log viewer (admin-only, filterable)
- ~~TOTP MFA enforced for `owner` and `admin`~~ — delivered in 3.7; what remains here is KMS-managed encryption of the TOTP secret at rest
- Backup + **restore rehearsal**, monitoring, alerting, runbooks
- Penetration test and remediation

**5.1 analytics engine — delivered.** See [ANALYTICS.md](./ANALYTICS.md). The
decisions worth carrying forward:

- **Analytics has no scope of its own.** A metric is readable exactly when its
  entity is, resolved through the same grants the list endpoints use. A second
  definition of visibility is a second thing to keep in step, and the first
  divergence is a disclosure. A caller without a grant gets `null`, not `0` — a
  blank panel is honest, a zero is a claim.
- **Ratios are derived, never stored.** The average of thirty daily win rates is
  not the month's win rate. Only summable components are snapshotted; every
  ratio is recomputed at read time, and `validate_registry()` refuses to start if
  a ratio's components are not themselves snapshotted.
- **Snapshots are per owner**, because a scope rolls up by summing the ids the
  resolver returns. An org-grain row could only answer an admin's question.
- **The seam at today lives in one place.** History from `metric_snapshots`,
  today computed live, implemented once in `series` — otherwise dashboards go
  flat at midnight and fill in overnight, which reads as an outage.
- **The cache key carries a scope digest.** Keying on tenant and window alone
  serves one agent's numbers to another: a leak that passes every single-user
  test.
- Two pre-existing correctness bugs surfaced while building on the CRM core: the
  loss-reason rollup grouped by a COALESCE expression that recompiles to a fresh
  bind parameter (Postgres rejected the statement), and `actual_close_date` was
  written with the host-local `date.today()` while every other timestamp is UTC —
  a deal closed near midnight fell outside the day it closed. Both fixed.

**5.3 reporting — delivered.** See [REPORTING.md](./REPORTING.md).

- **There is no user SQL.** A report is a specification whose every name is
  resolved against a fixed registry; a filter that accepted a column name would
  eventually accept `id) OR (1=1`, and a builder that accepted an expression is a
  SQL console with a nicer font. Operators are closed and type-checked, LIKE
  metacharacters are escaped, and values are coerced to the column's type.
- **Sharing widens who may run a report, never what comes back.** Rows resolve
  against the runner's scope. The alternative makes a saved report a
  privilege-escalation primitive that looks like a feature in a demo.
- **A truncated export reports `partial`, not `succeeded`**, with the total
  alongside the row count.
- CSV injection is neutralised once, at the cell boundary; exports are queued
  and stored, never rendered inline; scheduled runs carry no fake actor.

**5.6 admin and hardening — delivered.** See [HARDENING.md](./HARDENING.md).

- **Secrets at rest.** A `KeyProvider`/`SecretBox` pair with a local and a KMS
  envelope provider; the TOTP secret is sealed. The key id travels inside each
  token, so rotation is a config change rather than a migration. Legacy
  plaintext still reads and is re-sealed on the next read — refusing it would
  have locked every enrolled user out at deploy time. It protects a database
  dump, not code execution in the API process, and the docs say so rather than
  claiming more.
- **A real scanner.** ClamAV over clamd's INSTREAM protocol. `failed` stays a
  distinct verdict from `clean` so "we could not tell" never becomes "safe", and
  clamd's `ERROR` maps to `failed` rather than `infected` — quarantining on a
  size-limit error would delete a legitimate document.
- **Email threading.** Message-ID, In-Reply-To and References, with the id
  generated and stored *before* the send: a reply must reference an id that
  exists, and a provider-assigned one is unknowable at compose time. SES
  switches to raw MIME when headers are present.
- **Operational visibility.** The admin panel reports what readiness cannot —
  worker heartbeats, snapshot freshness, delivery rates that are `null` rather
  than `0%` over no traffic.
- Three new production gates: unconfigured encryption, the `local` key provider,
  and scanning enabled with the EICAR engine. Each is a silent failure mode
  where everything works and the gap is invisible until the incident.

**Exit criteria**
- Every pre-production gate in [SECURITY.md §6](./SECURITY.md) is checked
- Restore from backup rehearsed end to end against a live-shaped dataset
- Dashboard p95 < 500 ms
- Pen-test findings remediated or formally accepted

**→ Production go-live for the single tenant.**

---

## Phase 6 — AI layer

*Additive. Touches services, never routers or repositories — this is what the
Phase 0 layering bought.*

**Deliverables**

6a — Infrastructure
- `pgvector`; `entity_embeddings`, `ai_jobs`, `lead_scores`, `ai_generated_content`
- Provider abstraction (no vendor lock-in at the call site)
- Per-org token quotas and hard cost ceilings enforced pre-dispatch

6b — Lead scoring
- Feature extraction from engagement, source, budget fit, response latency
- Scores written with `explanation` JSONB — honouring the product's existing
  "three signals that drove it" promise
- Rescoring on activity via queue, not on read

6c — Sales assistant (RAG)
- Embedding pipeline over org data, incrementally maintained by `content_hash`
- **Retrieval filtered by `organization_id` + user scope predicate before
  similarity search** — the same scope resolver as SECURITY §1.3, one
  implementation shared with the CRM
- Citations back to source records

6d — Generated messaging
- Email/SMS drafting from CRM context
- Always `draft` → explicit human approval → send. No autonomous sending, ever.

6e — Lead discovery
- Scheduled ingestion workers with a domain allowlist and egress proxy
- Deduplication against existing leads
- Auto-qualification proposals surfaced for human confirmation

**6.1 infrastructure — delivered.** See [AI.md](./AI.md).

- **Provider abstraction, echo by default.** `CompletionProvider` behind the
  same Protocol as email and storage; the Anthropic adapter talks to the
  Messages API over httpx (no SDK); the echo provider is the default because it
  never calls a model and cannot leak customer data during a test run.
  Production refuses `AI_ENABLED` with the echo provider.
- **The cost ceiling is enforced before dispatch**, not after. A refused call
  sends nothing, spends nothing, is recorded `refused`, and audited
  high-severity. Refused spend does not count toward the ceiling. Cost is
  `Decimal` end to end, and an unknown model is priced high rather than free —
  the dangerous direction for a budget guard is under-counting.
- **Instructions and data are not confusable.** A prompt is a system string plus
  fenced, escaped content blocks; untrusted CRM text is the default and cannot
  forge its own closing delimiter. Redaction masks email, phone and long digit
  runs before egress.
- **`ai_jobs` is a ledger, not a transcript** — no prompt, no completion text,
  so it does not re-introduce the PII redaction just removed. Every egress is
  audited (`ai.completion`) whether or not the model answered.
- Context builders ship as a scope-first framework; concrete builders arrive
  with the features (6.3+). The background completion job runs through the same
  `AIService.complete`, so the ceiling, ledger and audit hold off the request
  path too.

**6.2 assistant — delivered.** See [AI.md §11](./AI.md).

- **A per-user CRM assistant**, composed entirely over the 6.1 infrastructure —
  it never talks to a model, only to `AIService.complete`, so every 6.1
  guarantee holds without being re-established.
- **Two isolation boundaries**: RLS for the organization, and a `user_id` filter
  in every query for the user. An assistant thread is private to its creator —
  stricter than the CRM's scope rules, because a transcript is more revealing
  than the records it discusses.
- **Entity-aware context, scope-first**: a conversation can be anchored to a
  record, validated under scope at creation and rebuilt under scope at every
  turn through the same `get_*` service a request handler uses. Invisible record
  in, no context out.
- **The composed prompt is never stored** — only the visible turns are. A lead's
  notes, redacted or not, never land in the conversation tables.
- **Streaming- and tool-ready seams**, both additive and off: a
  `StreamingProvider` Protocol and `CompletionChunk`, and a scoped read-only
  `Tool`/`ToolRegistry` with `CompletionRequest.tools` defaulting empty. Turning
  either on later is a transport or a `register` change, not a contract change.

The remaining sub-phases (lead/deal/property intelligence, growth engine) fill
the same frameworks.

**Exit criteria**
- **RAG leakage test:** a user provably cannot retrieve, via the assistant, any
  record they cannot retrieve via the API
- Prompt-injection fixtures in lead notes do not alter model behaviour or
  trigger privileged actions
- Per-org cost ceilings enforced and observable — **met at the infrastructure
  level in 6.1**
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
| R6 | 18 modules import `mock-data` | ~~Medium~~ **CLOSED for data** | Typed data layer; ported resource by resource. Leads (2.3), Clients (2.4), Properties (2.5), Deals (2.6), the dashboard (2.8), notifications (3.3), messages (3.4), the calendar (3.5) and reports (5.1–5.3) are all live and their fixtures deleted; `revenueByMonth` went with the dashboard's revenue chart in 5.1. What remains are the contacts/team/document fixtures that ported pages borrow for chrome, on pages whose own modules are later phases | 1–5 |
| R11 | Audit metadata could not serialise `Decimal`; a failed audit write poisoned the caller's transaction | ~~High~~ **CLOSED** | Found in 2.6, live since 2.3 — reachable from any money-field edit on leads, clients or properties, and no test had changed one. Values now coerce to JSON-safe types (Decimal → string, never float), comparison happens before coercion, and the insert runs in a SAVEPOINT so an audit failure genuinely cannot break the request it describes | 2.6 ✅ |
| R12 | Read-after-write returned stale relationship state | ~~Medium~~ **CLOSED** | SQLAlchemy does not overwrite loaded state on a fresh query, so a stage transition returned the deal's *old* stage and therefore its old derived status — the Kanban card would snap back. `populate_existing` on the deal and pipeline read paths | 2.6 ✅ |
| R7 | Refresh rotation logs users out under concurrency | ~~High~~ **CLOSED** | Redis lock on the presented token plus a 10s rotation grace window. Proven by 10 genuinely parallel refreshes all succeeding, and by the suite passing with Redis deliberately unreachable | 2.1 ✅ |
| R8 | `SET` instead of `SET LOCAL` leaks tenant context across pooled connections | ~~Critical~~ **CLOSED** | `set_config(..., true)` throughout; proven by `TestTransactionScopedContext` — context does not survive the transaction on a reused connection | 1 ✅ |
| R9 | RAG retrieval bypasses RBAC | **Critical** | Shared scope resolver; pre-filter before similarity search; leakage test | 6 |
| R10 | AI cost runaway | Medium | Per-org quotas, hard ceilings, per-job cost recording | 6 |

R8 and R9 are the two failures that would be silent, severe, and hardest to
detect after the fact. Both have a dedicated test as a phase exit criterion.

---

## Sequencing rules

1. **No phase starts before the previous phase's exit criteria pass.**
2. **Security controls ship with the feature**, never as a later hardening pass.
3. **The frontend is never rewritten** — pages change data source, not design.
4. Every phase ends deployable. No long-lived branches.

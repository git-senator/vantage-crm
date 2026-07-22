# Background Jobs

Status: **Phase 3.2 implemented.**
Related: [ARCHITECTURE.md](./ARCHITECTURE.md) · [DOCUMENTS.md](./DOCUMENTS.md) · [SECURITY.md](./SECURITY.md) · [ROADMAP.md](./ROADMAP.md)

ARQ on the existing Redis. One worker service in compose, running the jobs and
holding the cron schedule.

```
  API ──enqueue──▶ Redis ──▶ worker ──▶ services ──▶ Postgres / S3
   │                          │
   └── best-effort            └── retry w/ backoff ──▶ job_failures
```

---

## 1. The rule: a job is a service call with a different trigger

Job functions resolve a session, bind tenant context, and then call the same
service methods a request would. They do not reimplement business logic with
looser rules, and they do not see more data than a user does.

That sounds like a style preference until you meet its consequence.

### A worker must not have BYPASSRLS

RLS is enabled and `FORCE`d on every business table, and the policies compare
against a per-transaction setting. **A worker with no tenant context bound sees
nothing.** A sweep written the obvious way — `SELECT … WHERE expires_at < now()`
— runs cleanly, returns zero rows, and reports success. It is the worst kind of
bug: silent, and indistinguishable from "there was no work to do".

The tempting fix is `BYPASSRLS` on the worker's database role. That is one broad
grant to solve one narrow problem: every query the worker ever makes, including
ones written years later, would be exempt from every policy.

What we do instead:

```sql
CREATE FUNCTION list_active_organization_ids() RETURNS SETOF uuid
LANGUAGE sql SECURITY DEFINER STABLE AS $$ SELECT id FROM organizations … $$;
```

Owned by the same NOLOGIN + BYPASSRLS role that owns the login-lookup functions,
granted to the app role, returning ids and nothing else. A sweep reads the list,
then binds each tenant and does its actual work under RLS exactly like a request.
The only thing that crosses a tenant boundary is a list of uuids.

### And it needs an authorization context

Services take an `AuthorizationContext` — they ask "what may *this actor* do",
and there is no actor behind a cron tick. `system_context(org, *permissions)`
supplies a real one: ALL scope on exactly the permissions that job needs,
`user_id=None`, role key `system`.

`user_id` is genuinely `None` rather than a placeholder id, because a fake id is
something a query can later join on. The narrow scopes fail closed without an
actor — `owner_ids_for_scope` returns `[]`, a predicate matching no rows — since
the alternative reading ("no actor, therefore no restriction") is how a job
quietly acquires more access than any user has.

---

## 2. Retry and dead-lettering

Two things about ARQ that are easy to assume wrongly:

**ARQ does not retry ordinary exceptions.** `retry_jobs=True` re-queues a job
that raises `arq.Retry` and nothing else. A job raising `ValueError` is failed on
its first attempt and never runs again.

**The lifecycle hooks receive almost nothing.** `after_job_end` gets `job_id`,
`job_try`, `enqueue_time`, `score` — no function name, no arguments, no
exception. Writing a useful failure record from there means reading the result
back out of Redis and hoping it was kept.

So both live in the `@job` decorator (`app/workers/runner.py`): it catches the
exception, and while attempts remain it re-raises as `Retry(defer=…)` with
exponential backoff and jitter. The jitter is not decoration — a burst of jobs
failing against the same dependency would otherwise retry in lockstep forever,
which is a synchronised load spike aimed at whatever is already struggling. On
the final attempt it writes the dead-letter row, with the error in hand.

Observed on the live stack: retries at 1.48s, 3.76s, 7.65s, 9.14s, then
`job_dead_lettered`.

### The dead-letter table

`job_failures`, one row per **(job_name, job_key)** — a job failing every ten
minutes for a week is one problem, and a thousand rows for it would bury the
other three. `attempts` accumulates, `first_failed_at`/`last_failed_at` bracket,
and a later success sets `resolved_at` rather than deleting the row.

Its RLS policy is a shade wider than the standard one:

```sql
organization_id IS NULL OR organization_id = current_organization_id()
```

Because a job that belongs to no tenant (the scheduler's own sweep) would
otherwise be invisible to everyone — and those are precisely the failures an
operator needs to see. Tenant-less rows carry no customer data by construction.

No payload is stored: `job_args` holds ids, redacted with the logger's key list,
and the error is reduced to a class name and a message. A dead-letter table that
recorded arguments verbatim would become the one place customer data outlives
its retention policy.

Exposed at `GET /api/v1/jobs/failures`, gated on `settings.manage`.
`GET /api/v1/jobs/health` is separate because an empty dead-letter view looks
identical whether the queue is healthy or the worker has been down since Tuesday.

There is deliberately **no retry button**. Every job here is re-derivable from
database state, so a manual retry would duplicate a mechanism that already
exists while adding a way to re-run something at an arbitrary moment.

---

## 3. Enqueueing is best-effort, on purpose

`enqueue` logs and returns `None` when Redis is unreachable. A queue is by
definition the part of the system doing work the user is not waiting for, so a
Redis blip must not turn a successful task assignment into a 500.

The trade-off is stated rather than implied: **a job lost this way is lost.**
Every job in this system is therefore either

* reconstructible from database state — the sweeps re-find their work on the
  next tick, which is why `sweep_scan_backlog` exists at all; or
* genuinely optional — one missed "you were assigned a task" email, about work
  the assignee can still see in their own list.

Nothing whose loss would corrupt data goes through the queue. That work belongs
in the caller's transaction.

`job_id` makes an enqueue idempotent where it matters: `scan:{attachment_id}`
means a retried finalize, or the backlog sweep racing the direct enqueue, cannot
queue the same scan twice.

---

## 4. The jobs

| Job | Trigger | What it does |
|---|---|---|
| `scan_attachment` | after `finalize` | Reads the object, scans, publishes or quarantines |
| `sweep_scan_backlog` | cron :05/:20/:35/:50 | Re-enqueues anything still `pending` |
| `sweep_abandoned_uploads` | cron :00/:15/:30/:45 | Closes expired registrations, deletes late objects |
| `notify_task_assigned` | on assignment | Emails the assignee, re-reading state at send time |
| `send_email` | ad hoc | Generic queued delivery |

Crons are staggered rather than all firing at `:00`: two sweeps starting
simultaneously across every tenant is a self-inflicted thundering herd on a
database that is also serving requests.

Multiple worker replicas are safe — ARQ's cron jobs are `unique=True`, so a
scheduled tick is claimed by one worker rather than run by each.

### Scanning, and an honest limitation

`EicarSignatureScanner` detects the **EICAR test file** and nothing else. It is
not antivirus. It exists because the quarantine pipeline — scan, verdict,
quarantine, delete the bytes, audit, never serve — is real code that needs
proving end to end, and EICAR is the standard harmless way to prove it.

`assert_production_ready` refuses to start a production process with scanning
enabled *and* this scanner configured. A scanner that catches nothing is worse
than none, because it looks like protection.

Three verdicts, three outcomes:

* **clean** — publish what finalization already verified.
* **infected** — quarantine **and delete the bytes**. A quarantined file is not a
  file kept somewhere safer; it is a file that no longer exists, because the only
  thing anyone would ever do with it is accidentally serve it. The row stays, so
  the audit trail records that it was here.
* **failed** (scanner unavailable) — change nothing but the scan state. The file
  stays unpublished and the next sweep retries. *"We could not check it" is not
  "it is fine"*, and a scanner outage must never publish files.

### `notify_task_assigned` re-reads at send time

Nothing about the task travels in the job arguments beyond ids. Between
assignment and delivery the task may have been reassigned, completed or deleted,
and mailing someone about work that is no longer theirs is worse than not mailing
them at all.

---

## 5. Operating it

```bash
docker compose up -d worker
docker compose logs -f worker
```

Health check is `arq --check app.workers.settings.WorkerSettings`. The worker
runs `assert_production_ready()` at startup and verifies the storage bucket
once, so a misconfigured deploy fails at boot rather than on its first document.

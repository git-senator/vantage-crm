# Automation Engine

Status: **Phase 4 implemented.**
Related: [JOBS.md](./JOBS.md) · [ARCHITECTURE.md](./ARCHITECTURE.md) · [SECURITY.md](./SECURITY.md) · [PERMISSIONS.md](./PERMISSIONS.md) · [ROADMAP.md](./ROADMAP.md)

Workflows that react to what happens in the CRM: a trigger, a tree of
conditions, actions and delays, and an execution log that answers *why did this
client get that email*.

```
CRM service ──emit──▶ workflow_events ──dispatch──▶ workflow_runs ──execute──▶ actions
   (same txn)          (outbox)          (worker)      (sliced)      (real services)
```

---

## 1. Why an outbox

Every other enqueue in this codebase is best-effort (JOBS.md §3). Here it is
not, and the difference is worth being precise about.

The worst case elsewhere is a missing notification. The worst case here is a
**phantom trigger**: a workflow reacting to a change that rolled back, sending a
real customer a real email about something that never happened.

So the event row is written **inside the caller's transaction**. It commits
exactly when the change does, or not at all. A dispatcher job is then enqueued
optimistically, and `sweep_workflow_events` re-finds anything that enqueue lost
— the belt-and-braces shape from Phase 3.2, applied to a table that is the real
record of outstanding work.

`workflow_events` is deliberately **not** the audit log, despite firing at the
same moments. The audit log answers *what happened, for the record*, and is
valuable because it is append-only and never re-processed. This answers *what
should react*, and needs retry, replay and a dispatched flag. One table cannot
have both properties.

---

## 2. A tree, not a DAG

A definition is one trigger and a graph where each node has **one outgoing edge**
— two for a condition. No joins.

A DAG with joins buys exactly one thing: "wait for both branches, then
continue". It brings with it partial state, join timeouts, and runs that are
half-finished in two places at once. Every automation builder people actually
use makes the same call, and the escape hatch when somebody genuinely needs a
join is a second workflow triggered by the first one's side effect.

**Cycles are refused at publish time, not bounded by a step budget.** A loop that
emails a client forty times because the budget was fifty is not a smaller bug
than one that never stops. The executor still carries a budget as a second line
of defence: if validation ever lets a cycle through, the run fails loudly rather
than running forever.

**Unreachable steps are an error too.** A step nobody can reach is almost always
a wiring mistake, and ignoring it means the author believes their workflow does
something it does not.

**Validation runs on publish, not on save.** A draft is allowed to be
incoherent — that is what a draft is — and blocking every save until the graph
is complete makes the builder unusable halfway through building something.

---

## 3. Who a workflow runs as

This is the security question the phase turns on.

**Authoring requires `automations.manage`, held only by owner and admin.** Not a
convenience: a workflow acts on records its author might not otherwise reach,
which is the point of automation and the reason it is an administrative
capability rather than a wider version of editing your own book. Managers and
agents hold neither automation permission.

**Execution uses a system context** granted exactly what the actions need — no
`users.manage`, no `roles.manage`, no `settings.manage`. A workflow that could
grant itself a role would be a privilege-escalation primitive with a friendly
UI. That context is never *wider* than its author's, because only ALL-scope
roles can author.

The alternative — running as the author, resolved at execution time — sounds
tighter and behaves worse: the workflow silently stops working when its author
changes role or leaves, weeks after anyone could connect the two, and the
failure is a customer who never got their follow-up.

**Writes are attributed to the workflow's author.** Services take an acting user
for the audit trail, and "the admin who published this" is both true and the
most useful thing an audit reader can be told. With no author left, the run
fails visibly rather than acting under a fabricated identity.

Stated plainly, because it is a real consequence: **a workflow can act on
records no individual agent could see.**

---

## 4. The recursion guard

A workflow's own writes emit events like any other change. "When a lead is
updated, update the lead" is therefore a loop whose output is outbound email.

Events carry an automation marker, taken from the authorization context rather
than a flag threaded through every service call that one of them could forget.
The dispatcher refuses any event carrying it.

That is stricter than a depth limit — **a workflow can never trigger another
workflow** — and it is the right default when the failure mode is a mailbox.
Chaining, if it is ever needed, is an explicit follow-up with its own budget.

---

## 5. Triggers

Ten sources, matching the CRM surface: leads, clients, properties, deals, tasks,
notes, activities, inbound messages (email and WhatsApp), and calendar events.

The set is deliberately **coarse**. `lead.updated` covers every field edit and
the workflow narrows it with a condition, rather than the registry carrying
`lead.email_changed`, `lead.budget_changed` and forty siblings — a registry that
grows with the schema is permanently out of date with it.

The exceptions are changes that are **domain actions rather than field edits**,
which get their own triggers for the same reason they get their own audit
actions: `deal.stage_changed`, `lead.converted`, `task.completed`. Those are what
people automate on, and digging them out of a generic `updated` diff is work
every author would otherwise repeat.

Optional narrowing (`only when these fields change`, `only into this stage`) is
applied **before a run is created**, so a workflow scoped to one pipeline stage
does not spawn a run per deal edit and immediately abandon it.

`trigger_type` is denormalised onto `workflow_versions` with a partial index,
because matching runs once for every CRM mutation in the workspace. A JSONB scan
there would sit on the write path of every record edit.

---

## 6. Conditions

A one-level boolean group: comparisons combined with ALL or ANY.

**Not an expression language.** `lead.budget > 500000 and tags contains "cash"`
needs a parser, a sandbox, and an answer for missing fields. A structured list
gives the builder something to render, gives validation something to check, and
has exactly one behaviour for an absent value.

The semantics that matter:

* **A missing field never matches.** Not an error that fails the run, not
  "matches empty" — the branch is not taken and the step output says which field
  was absent. A run that dies because a lead has no phone number is worse than
  one that skips and says so.
* **Comparisons are typed by the operator, not guessed from the values.**
  `"10" > "9"` is false as text and true as a number; picking by inspection is a
  bug nobody finds.
* **`changed` is false on a create.** There is no before-image to differ from. A
  workflow that wants both says so with two triggers.

---

## 7. Actions

Ten, each a **thin call into an existing service**. `create_task` goes through
`TaskService`, so the task lands on the record's timeline, audits, and honours
the same validation as one a person created. `change_deal_stage` goes through
`move_stage`, so stage history is written with the measured time in the previous
stage — a direct field write would silently break velocity reporting.

Failures are **diagnosed, not raised raw**: "this lead has no email address"
rather than a `NotFoundError` traceback. An automation that fails
incomprehensibly is one nobody can fix.

**An action failure stops the run by default**, because the next step almost
always assumes this one happened. `continue_on_error` is per node, since that is
a decision about one step's meaning rather than about workflows in general.

### Templating

`{{record.first_name}}` substituted from the run context. No expressions, no
filters, no loops — a template language in a message sent to a customer is an
injection surface and a support burden, and the moment it grows an `if` somebody
will want a `for`.

An unresolved placeholder renders **empty**, not literal. Leaving
`{{lead.first_name}}` in a message tells the recipient they are being processed
by a machine that is not working.

### `call_webhook` and egress

Resolves the hostname and refuses private, loopback, link-local, reserved and
multicast addresses; https only. Without that check the action is a
server-side request forgery primitive with a form in the UI — an admin, or
anyone who compromises one, could point a workflow at
`http://169.254.169.254/` and read cloud instance credentials.

Resolution happens before the request and every resolved address is checked;
validating only the hostname loses to a DNS record pointing at a private
address, which is the standard bypass.

---

## 8. Delays and scheduling

A delay parks the run: status `waiting`, a `resume_at`, **no worker held and no
timer in memory**. `sweep_workflow_runs` wakes it. That is the only shape that
survives "wait three days".

**Business-hours delays count working minutes.** "Wait 2 hours" from 5pm resumes
at 10am, not 7pm — counting wall-clock and then shifting into hours would make
it a 17-hour wait nobody configured.

Weekends and the configured window are honoured; **public holidays are not**.
Holidays need a calendar per country and region, and getting them subtly wrong
is worse than not claiming to handle them.

---

## 9. Runs, retries, and the log

Execution is **sliced**: a run executes until the workflow ends, fails, or hits
a delay, persisting after every node. A run that fails at step six does not
re-run steps one to five on retry — it resumes at `current_node_id`, which is
what stops a retry emailing a client twice.

Retries are bounded at **three** rather than the worker's default five: a step
that failed twice on a transient fault is unlikely to succeed on the fifth, and
every attempt may re-contact a customer. The costs are not symmetric.

A finished run is **never re-executed**, whatever the queue delivers. Workflow
actions are not idempotent.

Every node writes a `workflow_run_steps` row with its **label copied in at
execution time**, so editing the workflow afterwards cannot rewrite what the log
says happened. `GET /automations/runs/{id}` is the answer to "why did this client
get that email", and that question is what decides whether an automation feature
is operable at all.

**A run pins its version.** Editing a published workflow forks a draft; the
previous published version is archived rather than deleted, because a run in
flight still points at it.

---

## 10. The builder

A **vertical chain**, not a free-form canvas with draggable boxes and drawn
edges. That is a deliberate match to the data model: the backend accepts a tree,
so a canvas that let you draw arbitrary edges would let you draw workflows the
engine refuses. A chain makes the legal shapes the only expressible ones, and it
reads top-to-bottom the way people describe automations out loud. Condition
branches indent; that is the only place the layout has to think.

Every form control is generated from the **registry the server sends**. Adding
an action backend-side makes it appear in the palette with its fields already
rendered. Two hand-maintained copies of "what fields does send-email take" drift
within a week, and the symptom is a workflow that validates in the UI and fails
at run time.

Actions that cannot run under the chosen trigger are **hidden rather than shown
and rejected** — offering "change deal stage" on a note trigger only to fail
validation later is a worse experience than never offering it.

Validation returns **every** problem at once, so the canvas marks up in one pass
instead of making the author fix, save, discover, repeat.

---

## 11. Operating it

| | |
|---|---|
| Enable / disable | `POST /automations/{id}/enabled` — separate from publishing, so pausing a misbehaving automation at 2am needs no edit or republish |
| Publish | `POST /automations/{id}/publish` — validates, archives the previous version, audited as high severity |
| Run log | `GET /automations/runs` and `/automations/runs/{id}` |
| Cancel | `POST /automations/runs/{id}/cancel` — only a `waiting` run; a `running` one is inside a worker, and a flag it never checks is not a cancellation |

Sweeps run every minute (`sweep_workflow_runs` at :15, `sweep_workflow_events`
at :45). Both queries are partial-indexed and normally empty.

Deleting a workflow **switches it off on the way out** — a deleted-but-enabled
workflow whose rows survive is exactly the thing that keeps firing after
somebody thought they had stopped it.

# Notifications

Status: **Phase 3.3 implemented.**
Related: [JOBS.md](./JOBS.md) · [SECURITY.md](./SECURITY.md) · [ARCHITECTURE.md](./ARCHITECTURE.md) · [ROADMAP.md](./ROADMAP.md)

Two things share the word "notification" in this codebase and they are not the
same thing:

* `app/services/notifications/` — the **email transport**. Providers, adapters,
  message construction. Shipped in Phase 1.
* `app/services/notification_center.py` — the **product concept**. An event
  happened, some people should know, and each of them has said how.

This document is about the second.

---

## 1. The shape

```
  service                  NotificationCenter                 worker
     │                            │                             │
     │  raise_notification(…)     │                             │
     ├───────────────────────────▶│                             │
     │                            ├─ in-app row (always)        │
     │                            └─ enqueue, if preference ───▶│─▶ email
```

A service says *what happened and to whom*. The centre decides how it is
delivered. That split is why `TaskService` did not have to change when email
delivery arrived, and will not have to change when push does.

### The in-app row is written whatever the preference says

Muting sets `read_at` at creation instead of skipping the row. A preference
governs whether something is **surfaced as unread**, not whether it happened —
a user who muted deal notifications and later asks "when was I told about this?"
should get an answer. The bell stays quiet; the history stays complete.

### Recipients are named, never derived

There is deliberately no "notify everyone who can see this record". Fan-out by
scope turns one deal update into forty notifications and makes the feature
worthless inside a week. Callers name recipients explicitly.

### Nobody is told about their own action

`raise_notification` returns `None` when the actor is the recipient. A product
that emails you about what you just did is one people learn to ignore, and that
habit costs you the notifications that mattered.

---

## 2. Why it is not the timeline and not the audit log

Three tables look similar and answer different questions:

| | audience | answers | mutable | lifetime |
|---|---|---|---|---|
| `activities` | agents, on a record | what happened to this **record** | yes | business |
| `audit_logs` | security, compliance | what happened, for the **record of it** | never | compliance |
| `notifications` | one person | what **I** still need to look at | read/dismissed | until read, then history |

They diverge immediately. An activity nobody needs to act on belongs on the
timeline and not in anyone's notifications; an audit entry exists whether or not
a human should see it. Collapsing any two would make one of them wrong.

---

## 3. Visibility

A notification belongs to its recipient **and to nobody else** — not their
manager, not an admin at ALL scope. "Who was told what" is somebody's inbox, not
an administrative view of the CRM.

The recipient predicate lives in the repository, not in the RLS policy, matching
the split the rest of the system uses: **RLS is the tenant boundary, row
visibility is a SQL predicate** (SECURITY.md §1). Putting it in the policy would
also break every writer, since a notification is created by one user *for
another* — a `WITH CHECK` on the recipient would refuse every assignment
notification ever sent.

The endpoints take no permission. There is no `notifications.view` because there
is nothing to grant; authentication is the whole authorization story. There is
no create endpoint either: an API that let a client post a notification to
another user is a phishing surface inside the product.

---

## 4. Preferences

Per user, per category, per channel. Six categories — `lead`, `deal`, `task`,
`document`, `mention`, `system` — kept coarse on purpose, because a preference
screen with thirty switches is one nobody configures. The finer distinction
lives in each notification's `type` (`task.assigned`, `document.quarantined`).

**Absent means "the category default", not "off".** A row is written only when
someone changes something, so adding a category later cannot silently mute it
for every existing user. The API materialises defaults for unset categories so
the frontend holds no second copy of them.

| Category | In-app | Email | Why |
|---|---|---|---|
| `task` | on | **on** | Addressed to you personally, and you may be away from the product |
| `mention` | on | **on** | Same |
| `lead`, `deal`, `document` | on | off | Volume; you will see these when you next open the CRM |
| `system` | on | off | Operational notices — an email per infrastructure event trains people to ignore them |

Email defaulting on everywhere is how a CRM ends up in a spam filter, taking its
password resets with it.

Preferences are saved as a **whole set** (`PUT`), not one switch at a time. A
per-category PATCH lets two open preference screens overwrite each other
silently.

---

## 5. Email delivery

One job, `deliver_notification_email`, for every category. Not one job per event
type — the wording that distinguishes a task assignment from a quarantine notice
already exists as the notification's own title and body, written once by the
service that raised it. Duplicating it into per-event email templates is how the
app and the inbox end up saying subtly different things.

The job re-reads the notification at send time and stops if:

* it is gone — deleted between raise and delivery;
* `emailed_at` is set — a duplicate run, made impossible rather than unlikely;
* `read_at` is set — **already seen in the app**. Sending anyway is how a
  product teaches people its emails are not worth opening.

`emailed_at` is stamped in a second transaction, after the provider call. A
database connection held open across a network call to a third party is a
connection lost to its timeout.

Enqueueing is best-effort, as everywhere else (JOBS.md §3). The cost of a lost
one here is a missing email copy of something already sitting in the user's bell.

---

## 6. What raises notifications today

| Event | Category | Recipient |
|---|---|---|
| `task.assigned` | `task` | The assignee (never the assigner) |
| `document.quarantined` | `system` | Whoever uploaded the file |

The quarantine notice matters more than it looks: without it a user's upload
simply never becomes available, with nothing anywhere telling them why. Its
`actor_id` is `NULL` — a machine decided this — which is what separates it from
"Sofia mentioned you".

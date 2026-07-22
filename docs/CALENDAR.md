# Calendar

Status: **Phase 3.5 implemented.**
Related: [JOBS.md](./JOBS.md) · [NOTIFICATIONS.md](./NOTIFICATIONS.md) · [ARCHITECTURE.md](./ARCHITECTURE.md) · [ROADMAP.md](./ROADMAP.md)

Two tables — `calendar_events` and `event_attendees` — plus one cron sweep for
reminders. Scope, audit and timeline behaviour are the same as every other CRM
entity; what follows is only what is *different* about scheduling.

---

## 1. Overlap, not containment

Every calendar query is a range query, and the predicate is:

```sql
starts_at < window_end AND ends_at > window_start
```

An event that began this morning and runs into the afternoon **is** in the
afternoon's window. The obvious `starts_at BETWEEN …` misses exactly the long
events people most need to see — the closing that started at 9 when they are
looking at 2pm.

Both bounds are strict, so an event ending precisely when a window opens is not
counted twice at a month boundary.

The list endpoint **requires** a window. A calendar without bounds is a
full-table scan dressed as a feature, and there is no sensible default: a month
view, a day view and a record's schedule panel all mean something different by
"now". A single request may span at most 400 days.

---

## 2. Conflicts are reported, never enforced

`POST` and `PATCH` return the saved event **and** the events it overlaps on the
same person's calendar:

```json
{ "event": { … }, "conflicts": [ { "event_id": "…", "title": "…" } ] }
```

Refusing the write would be the system claiming to know better than the person
holding the calendar — double-booking is usually a mistake and occasionally
deliberate, like a broker covering two open houses on the same street. Staying
silent would let a double-booked showing reach a client. Reporting is the only
option that respects both.

Two rules make the signal useful rather than noisy:

* **Tentative events neither conflict nor are conflicted with.** A pencilled-in
  showing is exactly the thing you expect to be double-booked against while it
  is being arranged.
* **An event never conflicts with itself.** Without that exclusion every single
  update would report one conflict.

---

## 3. Attendees: internal and external, one table

A showing has an agent and a buyer. The agent is a user; the buyer is an email
address and nothing more. `event_attendees` holds both, with
`(user_id IS NULL) <> (email IS NULL)` enforcing exactly one identity per row.

Splitting them into `event_users` and `event_contacts` would double every query
that asks "who is coming" — which is every query the UI makes.

Attendees are replaced as a set on update rather than patched individually.
Existing **responses are preserved by identity**: somebody who already accepted
is not reset to `needs_action` because a colleague was added to the same event.

---

## 4. Times are instants

`starts_at`/`ends_at` are `timestamptz`, and an all-day event is a flag over an
instant range rather than a date. One column pair serves both, so no query has
to switch on a type to answer "is this happening now".

The cost is real and worth stating: an all-day event is anchored to a timezone.
That is the honest trade against carrying two representations of time in one
table and getting the comparison between them wrong.

---

## 5. Reminders

One cron sweep every five minutes, per tenant, raising a notification for each
event whose lead time has elapsed. The notification centre decides whether that
also becomes an email — the job neither knows nor needs to.

`reminded_at` is what makes a five-minute sweep safe: it is stamped in the same
transaction as the notification, so an event is reminded about exactly once
however many times the sweep sees it. **Moving an event clears the stamp**,
because the reminder that matters is the one for the new time.

The sweep's horizon is computed from the longest permitted lead time (one week)
rather than a fixed lookahead, and events that are not yet due are skipped in
the loop. A fixed window would either miss the week-ahead reminders or scan
uselessly far for the ten-minute ones.

`reminder_minutes = NULL` means no reminder; `0` means at the moment it starts.
Those are different choices and both are legitimate.

---

## 6. Booking, cancelling, deleting

**Booking for someone else is an assignment.** Putting an event on a
colleague's calendar is the same kind of act as reassigning their lead, so it
goes through a scope check: OWN can only book their own calendar, TEAM their
team's, ALL anyone's. The owner is notified when somebody else schedules for
them.

**Cancel, don't delete.** "What was I meant to be doing on Tuesday" is a real
question, and an event that vanishes takes its answer with it. Cancelled events
drop off the calendar view but stay queryable with `include_cancelled=true`.
Delete exists, is a soft delete, and is the rarer path.

**A scheduled event lands on the linked record's timeline**, next to its notes
and tasks. The unified timeline is only unified if everything that happens to a
record reaches it.

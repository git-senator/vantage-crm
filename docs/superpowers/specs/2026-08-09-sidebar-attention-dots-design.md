# Sidebar attention dots

A small blue dot on a navigation item, meaning **something here is waiting on you**.

## The decision that shapes everything

The dot is **personal** and it is about **unfinished work**, not about novelty.

Two readings were on the table. "Something new arrived since you last looked"
needs a per-user, per-section last-seen timestamp, and it goes out the moment
you glance at the page — including at the thread you did not answer and the
request you did not approve. "Something is waiting on you" needs no new storage
at all, because the state that answers it is already in the database, and it
stays lit until the work is actually done.

The second was chosen. The practical consequence: **there is nothing to mark as
seen.** No "clear" endpoint, no last-seen table, no way for the two to disagree.

## What lights each dot

| Item | Condition |
|---|---|
| Messages | conversations owned by me with `unread_count > 0` |
| Tasks | my tasks not `done`, overdue or due within 12 hours |
| Requests | access requests in `pending` |
| Notifications | my notifications with `read_at IS NULL` |

Only these four. Leads, Properties, Reports and the rest do not *send* anything
to a person, so a dot there would either never light or never go out.

### Why 12 hours rather than "today"

The workspace timezone is stored as display strings ("Mountain Time"), not IANA
zones, so a calendar-day boundary cannot be computed reliably from it. A rolling
12-hour window needs no timezone, behaves the same in every country, and does
not light up for a task due the day after tomorrow.

### Messages are personal by owner

`conversations.unread_count` is shared across the team — one manager opens the
thread and it clears for everyone. That is deliberate for an inbox, but it means
the count alone cannot answer "is this waiting on *me*". Ownership does:
the dot counts only threads assigned to the person looking.

## Delivery

`GET /api/v1/attention` returns the four counts in one response:

```json
{ "messages": 3, "tasks": 1, "requests": 0, "notifications": 7 }
```

Considered and rejected: computing the counts in the server-rendered layout
(a full layout re-render per poll, to move one dot), and one endpoint per
section (four requests where one will do).

Four `COUNT` queries over already-indexed columns, one round trip. The sidebar
polls it on an interval and refetches on navigation.

**Permissions gate each count independently.** A caller without the permission
for a section gets `0`, not the real number — otherwise the sidebar becomes a
side channel telling an agent how many access requests exist on a page they
cannot open.

## The dot itself

A small filled circle in the badge slot the sidebar already has (the one
currently holding "New" beside the AI assistant). Not a number: the question a
person asks the sidebar is "is there anything?", and the count is on the page
itself the moment they arrive.

It carries an accessible label — a bare coloured circle says nothing to a screen
reader — and it is hidden along with its nav item when permissions hide the row.

## Testing

* Each count returns zero without its permission, and the real figure with it.
* A thread owned by someone else does not light my Messages dot.
* A task due in three days does not light Tasks; one due in two hours does.
* A read notification stops counting.
* The sidebar renders a dot when a count is above zero and nothing when it is
  zero — the absent state matters as much as the present one.

## Rollout

Local first, verified against the real workspace, then production as a separate
decision — production is frozen and each deploy needs its own go-ahead.

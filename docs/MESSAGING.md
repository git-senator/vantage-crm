# Messaging

Status: **Phase 3.4 implemented (email). WhatsApp is Phase 3.6.**
Related: [JOBS.md](./JOBS.md) · [NOTIFICATIONS.md](./NOTIFICATIONS.md) · [SECURITY.md](./SECURITY.md) · [ROADMAP.md](./ROADMAP.md)

Two tables and one channel abstraction. Email ships now; WhatsApp will be an
adapter plus an enum value that already exists.

---

## 1. Why one substrate, not one per channel

`conversations.channel` is a column, not a table. A conversation is a thread with
a person over some transport; a message is one thing said in it. The inbox, the
threading, the record matching and the unread counts do not care which channel
they are looking at — and every one of them would have had to be written twice
if the channel were a table.

This is not speculative generality. The prototype's own inbox already listed
sms, email and whatsapp in one stream, so the product concept was settled before
the schema was. Two parallel implementations would have had to be merged later,
at exactly the point where they had diverged most.

`MessageChannel` carries two responsibilities that look unrelated:

* `send` — outbound.
* `normalise_address` — **the identity function for a thread.** Get it wrong and
  one person gets two conversations. `"Sana Kaur" <Sana@Example.com>` and
  `sana@example.com` are the same thread; `+1 (415) 555-0100` and `+14155550100`
  will be too. Only the channel knows its own rules, so normalisation lives
  there.

The email local part is lowercased, which RFC 5321 says is technically
significant. No mail system anyone uses treats it that way, and honouring the
RFC here would split real conversations to satisfy a rule the world does not
follow.

---

## 2. Threading and identity

A conversation is keyed by **(tenant, channel, counterparty address)**, enforced
by a unique partial index. Not by CRM record: the same person is a lead today
and a client tomorrow, and their thread should survive the conversion rather
than fragment at exactly the moment it becomes valuable.

The CRM link is a **nullable pointer, resolved at ingestion**. Mail arrives from
strangers, and an unmatched thread is still a thread — filed against nothing,
shown in the inbox, waiting for someone to act on it. Dropping what does not fit
the schema is how an inbound enquiry disappears.

---

## 3. Outbound: record first, then send

```
POST /conversations/messages
    └─ find-or-create thread
    └─ INSERT message (status='queued')   ← inside the request transaction
    └─ enqueue deliver_message
        └─ worker → channel → provider
            └─ status='sent' + provider id, or status='failed' + reason
```

The row exists **before** anything is sent. That ordering is the whole point: if
the enqueue is lost or the provider is down, there is still a record that a
person tried to send this, visible in the thread as queued or failed. A
send-then-record order loses what the user typed on the same failure.

The endpoint returns **202, not 201** — the message is recorded and accepted;
claiming it was sent would be a lie the UI would then have to un-tell.

A retryable provider error leaves the message `queued` so the retry picks it up
unchanged and the sender still sees it as in flight. A terminal rejection — a
malformed or suppressed address — sets `failed` immediately rather than spending
five attempts proving the same thing and, for email, five more marks against
sender reputation.

**Known limitation, stated rather than hidden.** True threading in the
*recipient's* mail client needs `In-Reply-To`/`References` headers, which
require the raw-MIME send API. `EmailProvider.send` speaks the simple API, so a
reply threads correctly in the CRM (the conversation is keyed on the address)
but may start a new chain in their client. `in_reply_to` is carried and stored,
so closing the gap is a change to the provider adapter alone.

---

## 4. Inbound: every input is hostile

`POST /conversations/inbound/email` is the only endpoint in the system with **no
authenticated user behind it**. It is treated accordingly.

### Authentication

HMAC-SHA256 over the **raw request body**, under a shared secret, with a signed
timestamp and a signed tenant header.

* **Raw bytes, not the parsed model.** Re-serialising JSON and signing that is a
  different string than the sender signed, and the difference only shows up for
  unusual key order or unicode — that is, in production, months later.
* **Constant-time comparison.** A byte-by-byte `==` on a MAC leaks its prefix.
* **Bounded timestamp** (±5 min). Without it a captured request replays forever.
* **The tenant is inside the signature**, so a validly-signed body cannot be
  re-pointed at another tenant's data.
* **An unset secret refuses everything.** A default-open check on an
  unconfigured secret would accept anything that posted.

The session it opens is tenant-bound with `SET LOCAL` like any request. A
webhook is not an excuse to run unscoped.

### Ingestion

1. **Deduplicate** on the provider's message id, before any write. Every
   provider replays webhooks eventually.
2. **Normalise** the sender address, then find or create the thread.
3. **Match** to a lead or client by *exact* address — leads first, since a lead
   is the newer and more actively worked record. `lower()` on both sides,
   because the stored address was typed by a person and the inbound one is
   normalised.
4. **Notify** the thread's owner, if it has one.

No fuzzy matching. Filing a stranger's mail onto a customer's record is worse
than leaving it unfiled — and unfiled is visible and fixable.

The response says `accepted` or `duplicate` and nothing else. A body that
revealed whether the sender matched a record would turn this endpoint into an
oracle for enumerating a workspace's contacts.

---

## 5. Visibility

A conversation's scope anchor is `owner_id`, so the same own/team/all machinery
applies as for leads — there is no fourth idea of visibility in the codebase.

**One deviation: an unowned thread is visible to everyone in the tenant.**
Nobody owns mail from a stranger, and hiding unclaimed mail from everybody is
how an enquiry sits unanswered for a week. Replying claims it — somebody
answering is the clearest possible signal of who owns it.

Messages carry no scope of their own; they are read through their conversation,
which the service proves first. The same rule notes and activities follow.

**Read state is per workspace, not per user.** A thread a colleague answered is
not still unread for everybody else. Notifications are the opposite — they are
personal — which is why the two track it differently, and why that difference is
worth stating rather than discovering.

---

## 6. HTML bodies are stored, never rendered

`body_html` is kept for fidelity and returned by the API **unsanitised**. It
arrived from outside. The frontend renders `body_text` only; rendering the HTML
would be a stored-XSS hole with somebody's inbox as the delivery mechanism.

Sanitising server-side was the alternative and was rejected for now: it would
mean one more parser processing hostile input in the API process, and the
product does not yet need rich inbound rendering. When it does, the sanitiser
belongs at render time, in one place, with the raw copy retained.

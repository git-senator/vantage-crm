# Production hardening

Phase 5.6. Four things that were deferred, with the reasons they were deferred
and what replaced them.

- [1. Secrets at rest](#1-secrets-at-rest)
- [2. Malware scanning](#2-malware-scanning)
- [3. Email threading](#3-email-threading)
- [4. Operational visibility](#4-operational-visibility)
- [5. Production readiness gates](#5-production-readiness-gates)

---

## 1. Secrets at rest

`app/core/secrets.py`. The TOTP secret used to be stored as issued — a stated
trade-off, not an oversight, because encrypting a column needs a key managed
somewhere other than beside the data.

### The shape

A `KeyProvider` produces and unwraps **data keys**; a `SecretBox` uses them to
seal values. Both behind a `Protocol`, the same shape `ObjectStorage` and
`EmailProvider` use, so nothing above this layer imports boto3 or holds a key.

| Provider | Key management | Use |
| --- | --- | --- |
| `LocalKeyProvider` | keys from configuration | local, staging |
| `AwsKmsKeyProvider` | KMS envelope encryption | production |

With no configuration at all, the local provider derives a key from
`JWT_SECRET` via HKDF. That is deliberate: a developer clone encrypts by default
rather than silently storing plaintext until somebody remembers a variable, and
`JWT_SECRET` is already a required, environment-managed secret, so it adds no
new key-management surface. The cost — rotating `JWT_SECRET` orphans anything
sealed under the derived key — is why the key id is literally `jwt-derived` in
every token it produces, and why production refuses the local provider outright.

### The token

```
vnt1.<key_id>.<wrapped_key_b64>.<nonce_b64>.<ciphertext_b64>
```

Self-describing. **The key id travels with the value**, so rotating the active
key does not require rewriting every row: new writes use the new key, old reads
unwrap with the old one, and a background rewrite can happen whenever rather
than as a migration that must not fail.

AES-256-GCM. Authenticated, so a tampered ciphertext raises instead of
decrypting to plausible garbage. The token prefix and a caller-supplied
`context` are bound in as associated data — a sealed `mfa_secret` lifted into a
different column fails authentication there rather than working.

### What it protects

**A database dump.** A backup, a replica, or a stolen snapshot yields
ciphertext. It does **not** protect against code execution in the API process,
which can simply ask the box to decrypt. No field-level scheme can, and claiming
otherwise turns a security control into a false sense of one.

### The migration

`decrypt` returns a non-token value unchanged. That is the path off the
plaintext this shipped on top of: refusing those values would lock every
enrolled user out of their account at deploy time. `MfaService` re-seals
opportunistically on read, so the plaintext population drains through ordinary
use. The migration itself only widens the column (255 → 1024, since a KMS token
runs to ~420 characters) — encrypting rows there would need key material in a
migration, the one place with no sensible way to get it and no way to roll back
a half-finished run.

---

## 2. Malware scanning

`app/services/storage/scanning.py`. `ClamAvScanner` replaces the EICAR-only
placeholder as the production engine; the placeholder stays, because the
quarantine pipeline needs something harmless to prove itself against.

**clamd INSTREAM**, not a path-based `SCAN`: the path commands need clamd to
share a filesystem with the caller, and streaming works when clamd is a separate
container, a sidecar or a shared service — which is every deployment this
application has.

Three verdicts, and the third is the point. `failed` exists so "we could not
tell" never collapses into "clean", which is the one substitution that turns a
scanner into a liability. Everything that can go wrong — connection refused,
timeout, a reset mid-stream, an unrecognised reply — produces `failed`, and the
pipeline already knows what to do with it: leave the file unpublished, retry.

clamd's `ERROR` (size limit exceeded, for instance) maps to `failed`, never
`infected`. Quarantining on it would delete a user's legitimate document because
nobody managed to look at it.

`ping()` is separate from `scan()` so the admin health panel can report a
scanner outage before a user's upload discovers it — an unreachable scanner
means uploads silently stop being published, which presents as the upload
feature being broken.

```
MALWARE_SCAN_ENABLED=true
MALWARE_SCANNER=clamav
CLAMAV_HOST=clamav
CLAMAV_PORT=3310
CLAMAV_TIMEOUT_SECONDS=30
```

---

## 3. Email threading

`app/services/messaging/threading.py`. Replies used to thread correctly in the
CRM and start a fresh chain in the recipient's mail client, because the simple
send API cannot carry the headers that make a reply a reply.

### The headers

| Header | What it is | Why |
| --- | --- | --- |
| `Message-ID` | this message's identity | a reply must reference it |
| `In-Reply-To` | the immediate parent | one id |
| `References` | the full ancestry, oldest first | what clients actually thread on |

**The Message-ID is ours and is generated before the send.** That is the
decision everything else rests on: a reply references the id of what it answers,
so the id must exist and be recorded at compose time. A provider-assigned id is
unknowable then, which makes threading a chain of guesses. It is stored on the
message row, so a retry sends the *same* id — two ids for one message would fork
the recipient's thread on a redelivery.

Ids are minted as `<uuid4@sending-domain>`. UUID4 rather than the traditional
`<timestamp.counter@host>`, which leaks the host, the queue position and the
workspace's send volume. The domain matches the sender because a mismatched
Message-ID is a weak DMARC signal.

`References` is stored as a `TEXT[]` on `messages`, not recomputed from the
conversation — a thread is not the same thing as a conversation. The
counterparty may reply from a different client, fork the thread, or loop
somebody in, and the chain they are threading on is whatever their headers say.

**Trimming keeps the root.** A long thread's References header grows without
bound and servers reject oversized headers, so it is capped at 9 ids: the first,
plus the most recent eight. Dropping the root instead would sever the thread's
identity for any client that has it cached.

### The transport

SES's `Simple` content has no header field, so the adapter switches to
`Content.Raw` when headers are present, composing MIME with
`email.message.EmailMessage` rather than string concatenation — boundary
generation, header folding and non-ASCII encoding are each places where
hand-rolled output is wrong in ways only some clients reveal. Bcc stays out of
the MIME document and travels in the envelope, which is the one thing Bcc must
never get wrong.

---

## 4. Operational visibility

See [ANALYTICS.md](ANALYTICS.md) and the admin surface at `/api/v1/admin`. The
part that belongs here: **the health panel reports the things readiness cannot
see.** `/health/ready` answers whether an instance should take traffic; a
process can be perfectly ready while no worker has run a job since Sunday.

- **Worker heartbeats** are counted alongside queue depth. A deep queue with
  zero workers is a different incident from a deep queue with four.
- **Snapshot freshness** is the only place a silently dead nightly job shows up
  — dashboards keep working from live data while only history stops growing.
- **Rates over nothing are `null`, not `0%`.** A 0% failure rate reads as "all
  good" on a workspace whose email integration is switched off.

---

## 5. Production readiness gates

`Settings.assert_production_ready` refuses to serve production traffic with
development defaults. The Phase 5.6 additions:

| Refused | Because |
| --- | --- |
| no encryption configured | MFA secrets sit in the database in the clear |
| `ENCRYPTION_PROVIDER=local` | the key lives in the process environment |
| `MALWARE_SCAN_ENABLED` with the `eicar` engine | a scanner that catches nothing looks like protection |

Each is a silent failure mode: everything works, and the gap is invisible until
the one incident that matters. Checked explicitly rather than trusted to review.

Related: [SECURITY.md](SECURITY.md) for the full pre-production gate list,
[DOCUMENTS.md](DOCUMENTS.md) for the quarantine pipeline scanning feeds,
[MESSAGING.md](MESSAGING.md) for the conversation model threading sits on,
[MFA.md](MFA.md) for the enrolment flow whose secret is now sealed.

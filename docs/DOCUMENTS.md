# Documents & Storage

Status: **Phase 3.1 implemented.**
Related: [ARCHITECTURE.md](./ARCHITECTURE.md) · [SECURITY.md](./SECURITY.md) · [PERMISSIONS.md](./PERMISSIONS.md) · [ROADMAP.md](./ROADMAP.md)

Phase 2.8 shipped attachments as a **placeholder**: metadata rows with a
`storage_key` they would one day occupy and a `status` permanently stuck at
`pending_upload`. Phase 3.1 filled that seam in. The table was shaped for it, so
the migration is purely additive and no CRM module changed.

---

## 1. The shape of an upload

```
  Browser                         API                        Object storage
     │                             │                                │
     │  POST /attachments          │                                │
     ├────────────────────────────▶│  row (pending_upload)          │
     │                             │  key chosen server-side        │
     │◀────────────────────────────┤  + presigned PUT (5 min)       │
     │                             │                                │
     │  PUT <presigned url>        │                                │
     ├──────────────────────────────────────────────────────────────▶
     │                             │                                │
     │  POST /attachments/{id}/finalize                             │
     ├────────────────────────────▶│  HEAD  ───────────────────────▶│
     │                             │  read first 4 KiB ────────────▶│
     │                             │  stream + SHA-256 ────────────▶│
     │◀────────────────────────────┤  available                     │
```

**The bytes never traverse the API.** A multipart POST would occupy a worker for
the whole of a slow client's transfer and scale memory with concurrent uploads
rather than with request count. Presigning moves the transfer to the layer built
for it; the API handles metadata and signatures only.

**Finalization is not a formality.** After the PUT the server knows nothing —
the client may have uploaded nothing, something enormous, or an executable
called `offer.pdf`. `finalize` is the only point at which the real size, hash
and type are established, all read back *from storage*, and the only path that
can set `available`. A row that skips it stays unservable forever.

### Why three calls instead of two

The obvious simplification is to drop `finalize` and let a bucket event promote
the row. That is Phase 3.2 territory and it does not remove this endpoint: the
user is standing at the screen waiting to know whether their file was accepted,
and a rejection that arrives asynchronously is a rejection they will not see.
The synchronous path gives an answer; the queue is for what happens afterwards.

---

## 2. What is verified, and against what

| Check | Source of truth | On failure |
|---|---|---|
| Content type is accepted | Allowlist in `storage/validation.py` | 415 at **registration** — before an upload URL exists |
| Bytes match the declared type | First 4 KiB read back from storage | Object deleted, row `failed`, 415 |
| Not an executable | Magic bytes, regardless of declaration | Object deleted, row `failed`, 415 |
| Size within the ceiling | `head` from storage | Object deleted, row `failed`, 413 |
| Integrity in transit | SHA-256 over the stored object, compared to the client's optional claim | Object deleted, row `failed`, 409 |

The declaration is a hint from an untrusted party; the bytes are evidence.
Nothing a client sends about its own file — size, hash, type — is stored without
being checked against what actually landed.

The allowlist is an **allowlist**. A blocklist of dangerous extensions has to be
complete, and it never is. `image/svg+xml` and `text/html` are absent
deliberately: both are scriptable, and a browser that renders one from the
storage origin has been handed a stored-XSS primitive. For the same reason every
download URL carries `Content-Disposition: attachment`.

Sniffing is a signature table rather than `libmagic` — the set of types a CRM
accepts is small and their signatures are short and stable, which is a poor
trade for a native dependency on every dev box, CI runner and container.

**This is not virus scanning.** A well-formed PDF carrying a payload passes every
check here. That is what `scan_status` and the quarantine lifecycle exist for.

---

## 3. Lifecycle

```
pending_upload ──(client PUTs, then finalize verifies)──> available
      │                                                       │
      │ upload window elapses                                 │ scan trips
      ▼                                                       ▼
   failed  ◀──(bytes contradict the declared type)──      quarantined
```

`status` answers "may this be served". `scan_status` answers "what did the
scanner say". They are separate columns because there is a gap between them, and
collapsing it makes one of the two answers wrong.

With `MALWARE_SCAN_ENABLED=false` (the default today), finalization publishes
immediately and records `scan_status='skipped'` — honest about the fact that
nothing looked at the file. With it on, a verified upload is held at
`pending_upload` until the scan job clears it: a file must not be servable in
the window where its safety is unknown.

`failed` and `quarantined` are terminal. A rejected row cannot be re-presigned
or re-finalized, so good bytes cannot be used to launder an id that already
failed verification.

---

## 4. Object keys

```
org/<organization_id>/<entity_type>/<entity_id>/<attachment_id>/<safe_filename>
```

* **Tenant-first** so a per-tenant lifecycle rule, deletion or cost report is a
  prefix operation rather than a bucket scan — and so the key itself carries the
  tenant it belongs to. `key_belongs_to_organization` re-checks that before
  anything is signed: defence in depth *behind* RLS, not instead of it.
* **The attachment id is in the path** so two files called `contract.pdf` on one
  deal cannot collide, and a re-upload cannot silently overwrite an object that
  has already been verified and audited.
* **Only the last component is user-influenced**, and it is sanitised centrally
  in `storage/keys.py`: both separator conventions are stripped to a basename
  first, so `../../etc/passwd` and `..\..\secrets` cannot steer the key.

---

## 5. Access control

Attachments have no scope anchor of their own. Visibility follows the record
they hang off, through the same `EntityAccess` resolver notes and activities use
— so there is one definition of "visible" per entity rather than four.

| Action | Permission | Plus |
|---|---|---|
| List, get, download URL | `documents.view` | parent record readable |
| Register, finalize, delete | `documents.manage` | parent record readable |

`note` joined the vocabulary in 3.1. A note has no scope anchor either, so
`EntityAccess` resolves it by delegating one hop to the note's own parent —
attaching to a note cannot become a way around the lead's scope. Exactly one
hop, guaranteed by the note table's own `entity_type` CHECK, which excludes
`note`.

**Issuing a download URL is the access grant.** The fetch never reaches this
application, so there is no later moment to record — `document.downloaded` is
audited at the point of signing, not at the point of reading.

Download URLs are never included in a list response. Signing forty of them so a
user can click one mints thirty-nine bearer credentials for nothing.

---

## 6. Configuration

| Setting | Default | Notes |
|---|---|---|
| `STORAGE_PROVIDER` | `s3` | `memory` is for tests; refused in production |
| `S3_ENDPOINT_URL` | unset | Set for MinIO; unset targets real AWS S3 |
| `S3_PUBLIC_ENDPOINT_URL` | unset | The endpoint a **browser** must use, when it differs |
| `S3_SERVER_SIDE_ENCRYPTION` | `AES256` | Applied to every object, required on every PUT |
| `S3_PRESIGN_TTL_SECONDS` | 300 | Upload credential lifetime; capped at 3600 in production |
| `S3_DOWNLOAD_TTL_SECONDS` | 120 | Shorter — a leaked link is the whole exposure |
| `MAX_UPLOAD_BYTES` | 25 MiB | Enforced at finalization from the size storage reports |
| `UPLOAD_WINDOW_SECONDS` | 3600 | How long a registration stays claimable |
| `MALWARE_SCAN_ENABLED` | `false` | On: hold uploads unservable until scanned |

### The two-endpoint problem

In compose the API reaches MinIO at `http://minio:9000`, which no browser can
resolve. SigV4 signs the `Host` header, so a URL minted against the internal
name **cannot** be rewritten to the public one afterwards — the signature stops
verifying. Hence a second boto3 client bound to `S3_PUBLIC_ENDPOINT_URL`, used
for signing only. On real S3 both are the same endpoint and the second client is
the first.

MinIO also needs `MINIO_KMS_SECRET_KEY` to answer an SSE request at all; without
it, development would have to disable server-side encryption and would stop
exercising the production code path — the exact divergence running MinIO locally
is meant to avoid.

---

## 7. Deletion

Soft for metadata, **hard for bytes**. The row survives because that is what
makes an audit trail and a retention policy possible; the object does not,
because keeping bytes behind a row marked "deleted" is how a deletion request
quietly fails to be one.

Storage failure does not block the delete. The row is marked deleted with its
key intact and the sweeper reclaims the object later — the alternative is a user
who cannot remove a file because a bucket is having a bad minute.

---

## 8. Provider abstraction

Business logic depends on the `ObjectStorage` protocol. Nothing outside
`app/services/storage/` imports boto3 or knows a bucket exists, which is the
same arrangement `app/services/notifications/` uses for email. Adding a provider
is an adapter plus one arm of `build_object_storage`, with no call-site change.

Three adapters ship:

* **`S3ObjectStorage`** — real S3 and anything speaking its API. boto3 is
  synchronous, so every network call goes to a worker thread; presigning is a
  local HMAC and stays on the loop.
* **`InMemoryObjectStorage`** — tests and a laptop with no Docker. It issues
  *genuine* HMAC-signed URLs with real expiry, which is why the tests for
  expiry, tampering and operation confusion assert on behaviour rather than on a
  stub's return value.
* (MinIO is not a third adapter — it is `S3ObjectStorage` with an endpoint.)

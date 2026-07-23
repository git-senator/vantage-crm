# Vantage CRM — Security Architecture

Covers: RBAC model · authentication flow · authorization · OWASP controls · AI-specific risks

---

## 1. RBAC model

Two dimensions, evaluated independently:

```
CAN this actor perform this action?   →  permission check   (role → permission)
WHICH records may they touch?         →  scope resolution   (own | team | all)
```

Collapsing these into one check is the usual mistake — it forces a role explosion (`agent_own_leads`, `manager_team_leads`, …) instead of a 2×N matrix.

### 1.1 Permission keys

Format `resource:action`, with scope attached at the **grant**, not baked into the key:

```
lead:read      lead:create     lead:update     lead:delete     lead:assign
client:read    client:create   client:update   client:delete
property:read  property:create property:update property:delete property:publish
deal:read      deal:create     deal:update     deal:delete     deal:approve
document:read  document:upload document:delete document:sign
task:*   activity:*   report:view   report:export
user:read      user:invite     user:update     user:deactivate
role:read      role:assign     role:manage
audit:read     org:settings    ai:use          ai:configure
```

### 1.2 System roles (seeded)

| Role | Scope | Notes |
|---|---|---|
| `owner` | all | Full control incl. billing, org settings, role management. Cannot be deleted; at least one must exist. |
| `admin` | all | Everything except billing and ownership transfer. |
| `manager` | team | Full CRUD on their team's records; may reassign within team. |
| `agent` | own | CRUD on records they own; read-only on shared org data (properties). |
| `coordinator` | all (narrow) | Documents + deals only. Read-only on leads/clients. Models a transaction coordinator. |
| `viewer` | all (read) | Read-only. For auditors and observers. |

Roles are **rows, not code**. Custom roles are therefore a Phase 2 feature with no code change — only a UI over `role_permissions`.

### 1.3 Scope → SQL

The critical implementation rule (decision D5). Scope resolves to a predicate applied in the repository:

```
own   →  WHERE owner_id = :user_id
team  →  WHERE owner_id IN (SELECT user_id FROM team_members WHERE team_id = ANY(:user_teams))
all   →  (no additional predicate; RLS still constrains organization_id)
```

**Never** fetch-then-filter. Post-filtering produces wrong pagination counts, leaks the existence of records through totals, and degrades linearly with table size.

### 1.4 Enforcement layers

Authorization is checked at four independent layers. Any one failing does not produce a breach.

| Layer | Enforces | Failure mode if it alone fails |
|---|---|---|
| Next.js middleware | Route reachability | Cosmetic — API still refuses |
| FastAPI dependency | Permission | Blocked at repository/RLS |
| Repository | Scope predicate | Blocked at RLS |
| PostgreSQL RLS | Tenant isolation | Last line — must never be the only one |

The UI additionally hides unauthorised navigation via permission predicates on `nav.ts`. **This is UX, not security**, and is documented as such so nobody mistakes it for a control.

---

## 2. Authentication flow

### 2.1 Token design

| | Access token | Refresh token |
|---|---|---|
| Format | JWT (HS256 → RS256 at scale) | **Opaque** 256-bit random |
| Lifetime | 15 minutes | 30 days |
| Storage | httpOnly cookie | httpOnly cookie |
| Cookie flags | `Secure`, `SameSite=Lax`, `Path=/` | `Secure`, `SameSite=Strict`, `Path=/api/v1/auth` |
| Server state | None | Hashed row in `refresh_tokens` |
| Revocable | Via `jti` denylist (rare) | Yes, immediately |

The refresh token is **not** a JWT. It must be revocable and is stored server-side for rotation regardless, so JWT structure would add size and signature verification for no benefit.

Access token claims are kept minimal — `sub`, `org`, `roles`, `jti`, `iat`, `exp`, `typ`.

**Permissions are not in the token.** Roles are; permissions resolve server-side from a Redis-cached role map. This means a permission revocation takes effect within one access-token lifetime (≤15 min) instead of requiring the user to log out, and it keeps the cookie small.

### 2.2 Login

```
POST /api/v1/auth/login  { email, password }
  ├─ rate limit: per-IP AND per-account (independent counters)
  ├─ constant-time lookup + Argon2id verify
  │    → on failure: increment failed_login_count, exponential lockout,
  │      audit_log(login_failed), return generic 401
  │      (never reveal whether the account exists)
  ├─ on success: reset counters, create refresh family,
  │  issue access + refresh, audit_log(login)
  └─ Set-Cookie ×2
```

### 2.3 Refresh rotation with reuse detection

Every refresh issues a **new** refresh token and marks the old one used. Tokens are linked by `family_id`.

```
POST /api/v1/auth/refresh   (refresh cookie)
  ├─ hash token → look up row
  ├─ not found         → 401
  ├─ expired / revoked  → 401
  ├─ used_at IS NOT NULL  ⚠ REUSE DETECTED
  │     → revoke ENTIRE family
  │     → audit_log(refresh_reuse_detected, severity=high)
  │     → alert
  │     → 401, force re-login
  └─ valid → mark used, issue new pair in same family, return
```

Reuse detection is what makes rotation worth doing. A stolen token is usable at most once; the moment either the attacker or the legitimate user refreshes again, the theft is detected and the whole family dies.

### 2.4 Concurrency

Rotation plus parallel requests is a known footgun: ten concurrent calls each attempt refresh, nine get invalidated, the user is logged out at random.

Mitigated structurally — refresh happens in **exactly one place**, Next.js middleware, guarded by a short Redis mutex on `family_id`. Requests arriving during an in-flight refresh wait for it rather than starting their own.

### 2.5 CSRF

Cookies are sent automatically, so cookie auth requires CSRF defence.

- `SameSite=Lax` on access, `SameSite=Strict` on refresh — blocks the common cases
- **Double-submit token** for all state-changing verbs: a non-httpOnly `csrf_token` cookie must match the `X-CSRF-Token` header; the BFF verifies before proxying
- `Origin`/`Referer` validated server-side on mutations
- No `GET` endpoint ever mutates state

### 2.6 MFA

Schema provisioned in Phase 1 (`users.mfa_secret`, `mfa_enabled`), TOTP enforcement shipped in Phase 3.7. Required for `owner` and `admin` before production go-live.

---

## 3. Application security controls

Mapped to OWASP Top 10 (2021).

| Risk | Control |
|---|---|
| **A01 Broken Access Control** | Four-layer enforcement (§1.4). Scope as SQL predicates. RLS with `FORCE`, app role lacks `BYPASSRLS`. UUIDv7 IDs prevent enumeration. Every endpoint denies by default — no permission declared, no access. |
| **A02 Cryptographic Failures** | Argon2id (memory-hard params tuned to hardware). TLS 1.3, HSTS preload. Refresh tokens stored hashed. Secrets from env → managed store. Encryption at rest on DB and object storage. |
| **A03 Injection** | SQLAlchemy parameterised queries only; raw SQL forbidden by lint. Pydantic validation at every boundary. No generic query DSL exposed. Filenames never interpolated into paths. |
| **A04 Insecure Design** | Threat-modelled per feature. Explicit response models prevent field leakage. Idempotency keys on critical mutations. Rate limits on every mutating endpoint. |
| **A05 Security Misconfiguration** | Security headers (§4). Debug off in prod. FastAPI docs disabled in prod. Generic error responses — stack traces to logs only. Least-privilege DB roles. |
| **A06 Vulnerable Components** | `pip-audit` + `npm audit` gate CI. Dependabot. **Open item from audit:** move `shadcn` CLI to devDependencies; track transitive `postcss` advisory (build-tooling only, not runtime-reachable). |
| **A07 Auth Failures** | Rate limiting + exponential lockout. Generic failure messages. Refresh rotation with reuse detection. Session invalidation on password change. MFA for privileged roles. |
| **A08 Data Integrity** | Checksums on uploads. Append-only audit log enforced by grants. Optimistic concurrency via ETag/If-Match. Signed container images. |
| **A09 Logging Failures** | Structured JSON logs with correlation IDs. Auth events, permission denials, exports, and admin actions all audited. **Passwords, tokens, and PII redacted at the logger** — never at the call site. Alerting on reuse detection and privilege change. |
| **A10 SSRF** | No user-supplied URL fetching in MVP. Phase 5 lead-discovery workers use an explicit domain allowlist, egress proxy, and blocked link-local/private ranges. |

### 3.1 Security headers

Set at the Next.js edge:

```
Content-Security-Policy: default-src 'self'; img-src 'self' data: blob:;
  style-src 'self' 'unsafe-inline'; script-src 'self' 'nonce-{random}';
  connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'
Strict-Transport-Security: max-age=63072000; includeSubDomains; preload
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: geolocation=(), camera=(), microphone=()
```

CSP is nonce-based, not `unsafe-inline` for scripts. Tailwind requires `unsafe-inline` for styles only — an accepted, documented exception.

### 3.2 File upload pipeline

Documents are the highest-risk data path in a CRM.

```
1. Client requests presigned PUT   → API authorises, validates declared
                                     type + size, returns short-TTL URL
2. Client uploads directly to S3   → never transits the API
3. S3 event → ARQ worker           → verify real MIME by magic bytes
                                     (not the declared header),
                                     checksum, virus scan
4. scan_status = clean             → document becomes visible
   scan_status = infected          → quarantined, uploader notified, audited
```

Downloads are short-TTL presigned GETs, authorised per request. Storage bucket is fully private with public access blocked at the bucket policy. Original filenames are stored as metadata and never used as storage keys or filesystem paths.

### 3.3 Rate limiting

**Implemented in Phase 2.2** (`app/core/rate_limit.py`). Sliding window over a
Redis sorted set, with check-and-increment as a Lua script so it is atomic — a
read followed by a separate write lets two concurrent requests both take the
last slot, which under credential stuffing is the whole attack.

Rejected attempts are deliberately not recorded: counting them would let a
throttled attacker extend their own window and lock out the real account
holder. Fails open, because a limiter that rejects traffic during a cache
outage turns a degraded dependency into a full one.

Layered:

| Scope | Limit |
|---|---|
| `POST /auth/login` per IP | 10 / 15 min |
| `POST /auth/login` per account | 5 / 15 min → exponential lockout |
| `POST /auth/refresh` per family | 30 / min |
| Authenticated global | 1000 / min per user |
| Mutations | 100 / min per user |
| AI endpoints (Phase 5) | per-org quota + cost ceiling |

---

## 4. Frontend security

Carried over from the audit as first-class requirements:

1. **Client-boundary lint rule.** `"use client"` files may not import data-access modules. ESLint `no-restricted-imports`, failing the build. This fixes the proven leak in `topbar.tsx` and prevents its recurrence during the data port.
2. **No tokens in JavaScript.** httpOnly throughout; nothing in `localStorage` or `sessionStorage`. XSS cannot exfiltrate a session.
3. **Explicit server-only markers.** `import "server-only"` in every data module, so a mistaken client import fails at build rather than shipping data.
4. **UI permission gating is UX, never a control.** Documented in-code to prevent misreading.

---

## 5. AI-layer security (Phase 6; infrastructure implemented in 6.1)

The failure modes here are unlike the rest of the system and must be designed for in advance, not bolted on.

| Risk | Control |
|---|---|
| **RAG bypasses RBAC** — the flagship AI risk. Vector search over all org data returns records the user may not read. | Retrieval filters by `organization_id` **and** the user's scope predicate **before** the similarity search, never after. The same scope resolver as §1.3 — one implementation, not two. |
| **Prompt injection via lead data.** A lead's "notes" field is attacker-controlled text that reaches a model prompt. | All CRM-sourced text is untrusted input. Strict system/user separation, delimited and escaped. Model output never authorises an action. |
| **Autonomous action.** | No AI-initiated writes. Generated content lands in `ai_generated_content` as `draft`; a human transition to `approved` is required before anything sends. |
| **Data egress to model providers.** | PII minimisation and redaction before egress. Per-org opt-in. Zero-retention provider agreements. Regional routing where required. |
| **Cost exhaustion.** | Per-org hard cost ceilings, enforced before dispatch — a refused call sends and spends nothing. Every call's cost recorded in `ai_jobs`. **Implemented in 6.1** (docs/AI.md §7). |
| **Output injection.** | Model output is untrusted: escaped on render, never `dangerouslySetInnerHTML`, never executed. |

---

## 6. Pre-production gate

Go-live blockers:

- [ ] Independent review of RLS policies with a deliberate cross-tenant test suite
- [ ] MFA enforced for `owner` and `admin`
- [ ] Penetration test covering authn, authz, and the document pipeline
- [ ] Secrets migrated out of environment variables into a managed store —
      the abstraction is in place (`ENCRYPTION_PROVIDER=aws_kms` + `KMS_KEY_ID`),
      and `assert_production_ready` already refuses the local provider; what
      remains is provisioning the key and the IAM policy. See
      [HARDENING.md §1](./HARDENING.md)
- [ ] Malware scanning pointed at a real engine (`MALWARE_SCANNER=clamav`) and
      clamd reachable — production refuses to start with scanning enabled while
      the EICAR placeholder is configured, so this fails closed rather than
      silently. See [HARDENING.md §2](./HARDENING.md)
- [ ] Export retention and the audit trail reviewed against the data-retention
      policy: export *files* age out after 30 days, export *records* do not
- [ ] Backup **restore** rehearsed end-to-end (an untested backup is not a backup)
- [ ] Alerting live for refresh-reuse, permission changes, bulk export, failed-login spikes
- [ ] Incident response runbook written and walked through
- [ ] `shadcn` moved to devDependencies; dependency advisories triaged

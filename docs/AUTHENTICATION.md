# Authentication & Session Flow

Implemented in Phase 1.1, 1.2 and 1.5. This documents what the code does, not
what was planned — see [SECURITY.md](./SECURITY.md) for the design rationale.

---

## 1. Token design

| | Access token | Refresh token |
| --- | --- | --- |
| Format | JWT (HS256) | **Opaque** 256-bit random |
| Lifetime | 15 minutes | 30 days |
| Storage (browser) | httpOnly cookie `vg_access` | httpOnly cookie `vg_refresh` |
| Storage (server) | none — stateless | SHA-256 hash in `refresh_tokens` |
| Cookie flags | `Secure`, `SameSite=Lax`, `Path=/` | `Secure`, `SameSite=Strict`, `Path=/api/auth` |
| Revocable | via short TTL | immediately, per family |

The refresh token is **not** a JWT. It must be revocable and is stored
server-side for rotation regardless, so JWT structure would add size and
signature cost for nothing.

**Access token claims:** `sub`, `org`, `roles`, `jti`, `iat`, `exp`, `typ`.

**Permissions are deliberately absent from the token.** Roles are in it;
permissions resolve server-side from a Redis-cached role map. A permission
revocation therefore takes effect within one access-token lifetime (≤15 min)
rather than requiring the user to log out, and the cookie stays small.

---

## 2. Login

```
POST /api/auth/login  { email, password }
   │
   ├─ 1. lookup_login_identity(email)          ← SECURITY DEFINER, ids only
   │      RLS denies reads until a tenant is bound, and the tenant is only
   │      known once the account is found. See §5.
   │
   ├─ 2. SET LOCAL app.current_org = <org>     ← transaction-scoped
   ├─ 3. load the user row under RLS
   ├─ 4. lockout check → Argon2id verify → active check
   ├─ 5. rehash if Argon2 parameters have hardened since signup
   ├─ 6. issue access + refresh (new family), resolve roles
   ├─ 7. audit: auth.login.succeeded
   └─ Set-Cookie ×3  (access, refresh, csrf)
```

### Every failure path is identical

Unknown account, wrong password, suspended account and locked account all
return `401` with the same message after comparable work. A missing account
additionally burns Argon2-equivalent CPU (`verify_password_dummy`) so it is not
measurably faster than a wrong password.

Distinguishing them would turn login into a user-enumeration oracle
(OWASP A07). Test: `test_unknown_account_gives_identical_error`.

### Lockout

`LOGIN_MAX_ATTEMPTS` (default 5) consecutive failures set `locked_until`. This
is **per account**, independent of per-IP rate limiting, so a distributed
attack on one account still trips it. A successful login resets the counter.

---

## 3. Refresh rotation with reuse detection

Every refresh issues a new token and marks the presented one used. Tokens
descending from one login share a `family_id`.

```
POST /api/auth/refresh   (refresh cookie)
   │
   ├─ lookup_token_organization(hash) → org      ← bootstrap, see §5
   ├─ SET LOCAL app.current_org
   ├─ load the token row under RLS
   │
   ├─ used_at IS NOT NULL   ⚠ REUSE DETECTED
   │     ├─ revoke the ENTIRE family
   │     ├─ audit: auth.token.reuse_detected  (high severity)
   │     ├─ COMMIT                            ← see the warning below
   │     └─ 401
   │
   ├─ revoked / expired  → 401
   └─ valid → rotate within the same family, return a new pair
```

### Why reuse kills the whole family

An already-used token being presented means two parties hold it — the
legitimate user and a thief. Which is which is unknowable, so both are
revoked and both must re-authenticate. Detecting theft is the only reason
rotation is worth doing; rotation alone leaves a stolen token usable until it
expires.

### ⚠ The revocation must commit before the exception

Reuse detection revokes and then raises. The raise propagates out of the
endpoint and rolls back the request transaction — which would undo the
revocation *and* its audit entry. The API would return 401 as though it had
acted while the stolen family stayed fully usable.

`AuthService.refresh` therefore calls `session.commit()` before raising. This
is safe because the flow has only read up to that point, so there is no partial
work to leak.

This bug shipped and was caught end-to-end, not by unit tests — at service
level the revocation is visible inside the same transaction and looks correct.
Regression guard: `TestReuseRevocationSurvivesRollback`.

---

## 4. Frontend session flow

The browser never talks to FastAPI. Next.js is a BFF
([ARCHITECTURE.md §2](./ARCHITECTURE.md)).

```
Server-rendered page:
   Browser → Next.js RSC → (internal) → FastAPI
   cookies() reads the access token, forwarded as a Bearer header.

Client mutation:
   Browser → /api/* (same origin) → BFF proxy → FastAPI
   Cookie sent automatically; CSRF token echoed in X-CSRF-Token.
```

### ⚠ The proxy rewrites cookie paths

FastAPI scopes the refresh cookie to its own path, `/api/v1/auth`. The browser
never sees that path — it requests `/api/auth/refresh`. Cookie paths are
matched **browser-side** against the URL the browser requests, so relaying
`Set-Cookie` unchanged means the refresh cookie is never sent back and every
session dies at the 15-minute access-token expiry.

`rewriteCookiePath()` in the proxy maps `Path=/api/v1` → `Path=/api`. The
backend stays correct when called directly (tests, service-to-service).

### Two layers of route protection

| Layer | Checks | If it alone failed |
| --- | --- | --- |
| `middleware.ts` | cookie **presence** | API still refuses — cosmetic |
| `requireSession()` in the layout | token **validity**, via `/auth/me` | — |

Middleware does not verify signatures: the JWT secret has no business in the
edge runtime. Its job is to spare an unauthenticated visitor a flash of the app
shell, and to keep signed-in users off the login page.

---

## 5. The RLS bootstrap problem

Authentication has a genuine chicken-and-egg: it must find the account before
the tenant is known, but row-level security denies reads without tenant
context.

Two `SECURITY DEFINER` functions are the only escape hatch, and each returns
**ids only** — never a password hash, never profile data:

| Function | Returns | Used by |
| --- | --- | --- |
| `lookup_login_identity(email)` | `(user_id, organization_id)` | login |
| `lookup_token_organization(hash)` | `organization_id` | refresh, logout |

Everything after that runs under RLS like any other query.

### ⚠ Their owner must have BYPASSRLS

`FORCE ROW LEVEL SECURITY` subjects **even the table owner** to policies. A
SECURITY DEFINER function owned by the migration role returns zero rows — so
every login fails with "invalid credentials" while the code path looks entirely
correct.

Ownership belongs to `vantage_auth`: `NOLOGIN`, `BYPASSRLS`, and the owner of
nothing except these two functions. BYPASSRLS exempts from policies but grants
no table access, so it additionally holds `SELECT` — and only `SELECT` — on
`users` and `refresh_tokens`.

This was caught by `test_lookup_resolves_identity_despite_forced_rls`.

---

## 6. Logout and password change

**Logout** revokes the presented family and clears all three cookies. It always
returns 200 — a logout that errors leaves the user believing they are still
signed in, which is worse than a no-op.

**Password change** revokes *every* session for the user, not just the current
one. If the change was prompted by a suspected compromise, leaving other
sessions alive defeats the purpose.

---

## 7. CSRF

Cookie auth requires explicit CSRF defence because the browser attaches cookies
automatically.

- `SameSite=Lax` on access, `SameSite=Strict` on refresh
- **Double-submit token**: a non-httpOnly `vg_csrf` cookie must match the
  `X-CSRF-Token` header. A cross-site attacker can cause the cookie to be sent
  but cannot read it to set the header.
- Enforced in the BFF proxy and again by the `verify_csrf` dependency on
  state-changing API endpoints.
- `/auth/login` and `/auth/refresh` are exempt: they establish the session and
  cannot present a token yet. Login carries credentials; refresh is
  `SameSite=Strict`, so a cross-site request cannot send it.

---

## 8. Endpoints

| Method | Path | Auth | Notes |
| --- | --- | --- | --- |
| POST | `/api/v1/auth/login` | none | sets 3 cookies |
| POST | `/api/v1/auth/refresh` | refresh cookie | rotates; detects reuse |
| POST | `/api/v1/auth/logout` | refresh cookie | always 200 |
| GET | `/api/v1/auth/me` | access token | user + roles + permissions |
| POST | `/api/v1/auth/password/change` | access + CSRF | revokes all sessions |

No token appears in any response body — only in `Set-Cookie`. A token readable
by JavaScript is exfiltrable by any XSS.

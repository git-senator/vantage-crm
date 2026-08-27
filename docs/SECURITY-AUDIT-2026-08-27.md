# Security audit — 27 August 2026

The second full review, eighteen days after
[the first](SECURITY-AUDIT-2026-08-09.md). Twenty-four commits landed in
between: property translations, Instagram and Facebook sending, the access-request
queue, off-site backups, the Cloudflare origin lockdown, and a public showcase
that was built and then reverted.

Scope: application source, database, configuration, dependencies, and the
production edge **as seen from outside**. Production is frozen, so nothing on
the host itself was inspected this time — see *What was not checked* at the end.
That is the significant gap in this report and it is stated up front rather than
buried.

As before, findings were produced by running the check, not by reading the code
and assuming it does what the comment says.

---

## 1. MEDIUM — every per-IP rate limit can be bypassed by setting a header

The API decides who you are, for rate-limiting purposes, from the **first**
value of `X-Forwarded-For`:

```python
# app/api/v1/rate_limit_deps.py:32
forwarded = request.headers.get("x-forwarded-for")
if forwarded:
    return forwarded.split(",")[0].strip()
```

The same three-line pattern is repeated in `app/core/middleware.py:182` (the
anonymous global limiter) and `app/api/v1/dependencies.py:252` (the IP recorded
for forensics).

Nothing strips that header from a client request. The BFF proxy forwards every
header it receives except five that would break the transfer
(`frontend/src/app/api/[...path]/route.ts:106`), Caddy *appends* to the chain
rather than overwriting it, and Cloudflare preserves a client-supplied chain and
appends the real address behind it. So the first value — the one the API
reads — is whatever the caller typed.

**Demonstrated, not inferred.** Against the local stack, ten failed logins with
a fixed `X-Forwarded-For` and ten different email addresses:

```
401 401 401 401 401 401 401 401 401 401 429 429   <- limit works
```

Then fifteen more, incrementing the header by one each time:

```
401 401 401 401 401 401 401 401 401 401 401 401 401 401 401   <- no limit at all
```

**What it costs.** The per-*account* limit (5 attempts / 15 min, keyed on the
email) is untouched, so hammering one account is still stopped. What opens is
everything keyed on the address:

* **Password spraying.** One attempt against each of a thousand accounts, from
  one machine, unthrottled. This is how real credential attacks are run —
  common password, many accounts, under every per-account threshold.
* **The access-request form** (5/hour per IP) becomes an unbounded queue and
  mail flood aimed at whoever holds `users.manage`.
* **The anonymous global limit** (60/min) stops being a limit.
* **`refresh_tokens.ip_address` and the audit trail record an address the
  attacker chose**, so the one artefact an investigation would start from is
  writable by the subject of the investigation.

Invitation tokens are 256-bit, so removing their throttle changes nothing —
that one is theory, not risk.

**Fix — one line where it counts.** Caddy already knows the true client address:
`client_ip_headers Cf-Connecting-Ip` plus the trusted-proxy list is configured.
It simply does not pass that knowledge on. In each site block of
`deploy/compose/Caddyfile`:

```
reverse_proxy web:3000 {
    header_up X-Forwarded-For {client_ip}
}
```

`header_up` with a value **replaces** the header, so the chain the client
invented is discarded at the edge, and everything downstream — BFF, API,
rate limiter, audit log — sees one value that a visitor cannot forge.

Worth doing as well, because it costs nothing and covers the deployment that
forgets the Caddyfile: add `x-forwarded-for`, `x-real-ip` and `cf-connecting-ip`
to `STRIPPED_REQUEST_HEADERS` in the BFF proxy. Node's `fetch` does not add its
own, so the API would then fall back to `request.client.host`, which for a
request arriving through the proxy is the proxy — wrong, but wrong in the safe
direction: one shared bucket instead of a per-attacker bucket.

---

## 2. MEDIUM — the dependency fix is no longer a major upgrade

Both npm findings from August 9 are still open, and both still arrive through
Next.js:

| Package | Version | Advisories |
|---|---|---|
| postcss | ≤8.5.22 | 4, incl. arbitrary `.map` file read |
| sharp | <0.35.0 | 4 libvips CVEs |

What changed is the cost of fixing them. The last report parked this as "a
planned major bump". It is not one any more — `next` is pinned at **16.2.10**
and **16.3.3** is out, a minor release in the same line. `npm audit fix --force`
reports it as "outside the stated dependency range" only because the pin is
exact.

Neither package is reachable by an attacker today: postcss runs at build time
and sharp only matters where image optimisation is used, which the photo gallery
bypasses. The finding stays MEDIUM because it is now cheap enough that carrying
it is a choice.

**Python is clean.** The 61 packages installed *in the built API container* —
not the developer virtualenv that misled the last audit — were queried against
OSV: no known advisories.

---

## 3. MEDIUM — the page's script policy still allows inline scripts

Unchanged since August 9, verified live on production this morning:

```
script-src 'self' 'unsafe-inline'
```

Everything else in the policy is tight: `default-src 'self'`, `object-src
'none'`, `frame-ancestors 'none'`, `base-uri 'self'`, `form-action 'self'`, and
a scoped `img-src`/`connect-src` naming `files.rossacrm.tech` and nothing else.
`'unsafe-inline'` is the single directive doing the weakening, and it is exactly
what an injected payload needs in order to run.

There is no known injection point to pair it with — the one
`dangerouslySetInnerHTML` in the codebase (`src/components/ui/chart.tsx:95`) is
shadcn's chart-colour `<style>` block, fed from developer-defined config, not
user data. This is a missing seatbelt, not an open wound.

The nonce work `next.config.ts` describes was never finished.

---

## 4. LOW — the n8n editor is exposed to the whole internet

`https://n8n.rossacrm.tech` answers 200 to anyone. Authentication holds where it
matters — `/rest/workflows` and the public API both return 401 — and the
instance is configured sanely: email auth, secure cookie, setup completed, so no
first-run account grab is possible.

What is left is a password-only login form for an automation engine that holds
credentials for the CRM, Telegram and the AI provider, reachable by anyone who
guesses the subdomain. Its unauthenticated `/rest/settings` confirms the
configuration but leaks no version string.

**Fix.** Put it behind Cloudflare Access (an email one-time-PIN policy is free
and takes ten minutes), or restrict the hostname to the owner's address in a
Cloudflare WAF rule. Either removes the login form from public view entirely.

---

## 5. LOW — the nightly backup exposes its credentials and its plaintext

Two small things in `deploy/backup/backup-local.sh`, on a machine where root is
the only user, which is why neither is higher than LOW:

* **R2 keys are passed as `docker run -e`** (line 132), so they are visible in
  the process list for the life of the upload. `--env-file` avoids it.
* **The local dump is written with the default umask** — no `chmod` anywhere in
  the script — so `/var/backups/vantage/*.dump` is world-readable on the host,
  in the clear, for 14 days. The *off-site* copy is age-encrypted, which is the
  half that leaves the machine, so this is about local exposure only. `umask
  077` at the top of the script closes it.

---

## 6. LOW — three container images float on moving tags

`minio/minio:latest`, `minio/mc:latest` and `clamav/clamav:stable` are resolved
at build time, so a rebuild can silently change what runs — including into a
regression or a compromised upstream. `postgres:16-alpine`, `redis:7-alpine` and
`caddy:2-alpine` are pinned to a major line, which is the reasonable middle
ground; `latest` is not. Pin to a digest or at least a version tag.

---

## 7. LOW — `roles` still has no row-level security

Carried over from §8 of the last report and unchanged. Of **90** tables carrying
`organization_id`, 87 are under an RLS policy. `access_requests` and
`invitations` are exempt by design — both exist before a session does.
`roles` is scoped in the service layer only, and
`operational_policy_statements` in `sql_objects.py` was written for exactly its
shape. Defence in depth, not an open door.

Every policy that does exist was checked for the right predicate: all 87
reference `app.current_org`. None is permissive-by-accident.

---

## 8. Hygiene — four small things worth a decision

* **`ROSSA_DEMO_API_KEY` sits in `.env` and no code reads it.** `git grep` finds
  it nowhere in the repository. If it is the key the n8n orchestrator uses to
  create leads, it is a live credential with no owner in the codebase; it should
  be scoped, documented and rotated on a schedule, or deleted.
* **The R2 API token is still not IP-locked.** Noted as owed on 10 August. Until
  it is restricted to the server's address in the Cloudflare dashboard, a leaked
  token works from anywhere.
* **The age private key for backup encryption exists in exactly one place.** It
  is not on the server by design — but a key with one copy is a restore that
  fails on the day it is needed. It belongs in a password manager as well.
* **`.env.bak-20260811095648` is still on the development machine.** Gitignored,
  never committed, but it is a full second copy of every production secret from
  the day of the key rotation.

---

## What is right

The August 9 remediation held, and the new code did not undo it.

**The origin lockdown works.** `http://179.198.106.11` and
`https://179.198.106.11` both time out — the DOCKER-USER filter is dropping
non-Cloudflare traffic on 80/443 as designed, and the previously grey-clouded
subdomains are proxied. `files.rossacrm.tech` returns 403 rather than a bucket
listing.

**Headers on production are complete**: HSTS with preload, `X-Frame-Options:
DENY`, nosniff, a strict referrer policy, a permissions policy, and
`Cache-Control: private, no-store` on the session pages.

**The showcase revert is clean.** `git grep -i showcase` returns nothing outside
`docs/`. The public catalogue endpoint, its schemas, the config flags, the
enquiry form and the middleware exemptions are all gone — the surface it opened
is closed, not merely unlinked.

**Every new table brought its own RLS.** The `property_translations` migration
calls `tenant_policy_statements()` like every other tenant table, and the
`FORCE ROW LEVEL SECURITY` it lifts on `properties` to run one backfill UPDATE
is restored in a `finally` inside the migration's transaction.

**The four `SECURITY DEFINER` functions are exactly as narrow as they should
be.** All owned by `vantage_auth` (NOLOGIN, owns nothing else), all with
`search_path` pinned to `public, pg_temp` — the classic escalation vector,
closed — and all returning ids and nothing more. Neither application role has
`BYPASSRLS`.

**The new messaging code is sound.** Meta webhooks are HMAC-verified against the
raw body, with the unset-secret case refusing rather than defaulting open; the
subscription handshake uses a constant-time comparison; the outbound adapter
reads its token from settings, never logs it, and posts to a configured base URL
with no caller-controlled destination.

**Access requests do not leak.** The submit endpoint answers identically whether
or not the address already has an account. Invitation tokens are 256-bit CSPRNG
values stored as SHA-256; used, expired and unknown all raise the same
not-found.

**Auth internals check out**: JWT decoding pins the algorithm and requires
`exp`/`iat`/`sub`/`jti`; refresh tokens rotate within a family with reuse
detection that revokes the family; cookies are httpOnly with `SameSite=Strict`
on the refresh path; the login redirect honours relative paths only.

**No secrets in the repository or its history.** The only files ever added that
match a secret-shaped name are `.env.example`, `.env.prod.example` and source
modules with "secret" in the name. A scan for key material — private-key PEM
headers, `sk-`/`gsk_`/`AIza`/`ghp_` prefixes, Telegram bot-token shape — finds
nothing in tracked source.

**The AI assistant has no tools.** The registry exists but nothing registers
into it, so there is no model-driven action surface to abuse. Context is
re-fetched under the caller's scope every turn, and `ai.use` is required.

**The internal API is not reachable from the internet.** The BFF only ever
forwards to `/api/v1/*`; `/metrics`, `/health` and the mounted public API live
outside that prefix and return 404 through the proxy. Confirmed against both
localhost and production.

---

## What was not checked, and why

Production is frozen, so **nothing on the host was inspected** — this audit
touched `rossacrm.tech` only as an anonymous visitor would. That leaves
unverified, all of them things the last audit fixed and which nobody has
confirmed since:

* SSH hardening and fail2ban still in force (cloud-init has re-enabled password
  auth once already, and `authorized_keys` was rewritten from the Hostinger
  console on 12 August)
* the nightly backup timer actually firing, and its dumps restorable
* `MALWARE_SCAN_ENABLED` still true with the ClamAV engine
* `unattended-upgrades` and the current patch level
* what the production `.env` sets for `MFA_REQUIRED_ROLES`, `METRICS_TOKEN`,
  `INBOUND_WEBHOOK_SECRET` and `ENCRYPTION_KEYS`
* whether the owner and admin accounts have actually enrolled in TOTP

A read-only pass over that list takes about fifteen minutes and needs one
go-ahead.

---

## Order I would fix these in

1. **`header_up X-Forwarded-For {client_ip}`** in the Caddyfile — one line, and
   it is the only finding here that changes what an attacker can do from
   outside today.
2. **Bump Next to 16.3.3** — it is now a minor release, and it clears four high
   advisories.
3. **Cloudflare Access on `n8n.`** — ten minutes, removes a password form from
   the public internet.
4. **`umask 077` and `--env-file` in the backup script**, and pin the three
   floating image tags.
5. **The CSP nonce** and the `roles` policy as ordinary development work.
6. **The owner's items**: IP-lock the R2 token, duplicate the age key, decide
   what `ROSSA_DEMO_API_KEY` is.

Nothing found this round is architectural. The tenant isolation, injection
safety, session handling and secrets hygiene that the last audit praised are all
still true, and the new features arrived with their own RLS, their own signature
checks and their own rate limits. The one real finding is that those rate limits
trust a header they should not.

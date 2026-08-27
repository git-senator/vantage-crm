# Security audit — 27 August 2026

The second full review, eighteen days after
[the first](SECURITY-AUDIT-2026-08-09.md). Twenty-four commits landed in
between: property translations, Instagram and Facebook sending, the access-request
queue, off-site backups, the Cloudflare origin lockdown, and a public showcase
that was built and then reverted.

Scope: application source, database, configuration, dependencies, the production
edge as seen from outside, and — after an explicit go-ahead, production being
otherwise frozen — a **read-only** pass over the production host itself
(§9–§11). Nothing on the server was restarted, written or changed.

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

## The production host — read-only pass, same day

The owner gave a go-ahead for a strictly read-only inspection ("но только чтоб
ничего не поломалось"). Nothing was restarted, written or changed: the pass used
`sshd -T`, `systemctl cat/show/status`, `journalctl`, `ls`/`stat`/`grep`, `docker
ps`, and `SELECT` statements. Three findings came out of it.

### 9. MEDIUM — no account on production has MFA

```
email                       status   mfa_enabled  has_secret  last_login
ccrmbzl@gmail.com (Owner)   active   f            t           2026-08-14
boss@rossacrm.tech (Owner)  active   f            f           2026-08-27
admin@rossacrm.tech         active   f            f           2026-08-07
```

Two Owner accounts, neither with TOTP. `ccrmbzl@gmail.com` has an `mfa_secret`
but `mfa_enabled = false` — an enrolment that was started and never confirmed.
`MFA_REQUIRED_ROLES` is not set in the production `.env`, so the default
`["owner", "admin"]` applies, and enforcement is deliberately a nudge rather
than a lockout: nothing actually stops an Owner from staying on a password
alone.

This is the finding that compounds §1. A password-only Owner login, on an
internet-facing CRM, behind a per-IP throttle that can be sidestepped with a
header, is the shortest path an outsider has to everything in the system.

**Fix.** Enrol both Owner accounts. Ten minutes with an authenticator app, no
deploy.

### 10. MEDIUM — the seeded demo accounts are still live

`admin@rossacrm.tech` (Admin), `manager@rossacrm.tech` (Manager) and
`agent@rossacrm.tech` (Agent) are all `active`, all last used on 7 August — the
day the system was seeded — and none has been signed into since. The owner had
already decided these should go once the real accounts existed.

Three unused credentials, one of them Admin, with passwords set by a seeding
script and never rotated, are standing surface for no benefit. Deleting or
deactivating them removes it. `kardinal.kali.51@gmail.com` (Agent) and the two
Owners are the accounts that are actually used.

### 11. LOW — the weekly Cloudflare-range refresh has not run since 14 August

```
vantage-origin-firewall.timer   Trigger: n/a   last: Mon 2026-08-17 04:49
vantage-origin-firewall.service Active: active (exited) since Fri 2026-08-14
```

The service is `Type=oneshot` with `RemainAfterExit=yes`, so after its boot run
it stays *active* forever. Starting an already-active unit is a no-op, so when
the timer fired on 17 August nothing executed — and systemd then stopped
scheduling it at all (`Trigger: n/a`). The cached allow-lists are dated
14 August.

Nothing is open as a result: the rules are in force (21 IPv4 and 12 IPv6 rules
in `DOCKER-USER`) and Cloudflare's ranges have not changed since. The risk is
that the list is now frozen — if Cloudflare adds a range, legitimate visitors
are dropped and the site partially goes dark; if a retired range is reassigned,
a stale entry stays allowed. A safety net that silently stopped renewing itself
is worth fixing precisely because nobody will notice until it matters.

**Fix.** Drop `RemainAfterExit=yes` from the service — the iptables rules
outlive the unit's state, which is the only thing that flag was modelling.

### What production got right

Everything the August 9 remediation put in place is still in place, verified
rather than assumed:

* **SSH is key-only and single-key.** `permitrootlogin without-password`,
  `passwordauthentication no`, `kbdinteractive no`, `x11forwarding no`, and
  `authorized_keys` holds exactly one entry — `vantage-crm-deploy`. The
  `claude-code-pravosudie` key is gone, as expected since that project moved off
  the box.
* **fail2ban is active** with the sshd jail, and there were **zero** failed
  authentication attempts in the last 24 hours. The only interactive logins in
  the record are from `169.254.0.1`, the Hostinger console, on 12 and 14 August.
* **Backups run, encrypt, upload and verify themselves.** Three consecutive
  nights confirmed in the journal, each ending `offsite ok: r2://rossa-backups/
  db/… (verified)`, encrypted to the age recipient before leaving the machine.
  Fifteen dumps on disk under a 14-day retention; the disk is 5% full.
* **Malware scanning is genuinely on**: `MALWARE_SCAN_ENABLED=true`,
  `MALWARE_SCANNER=clamav`, and `vantage-clamav-1` is up and healthy.
* **Production configuration is correct**: `ENVIRONMENT=production`,
  `COOKIE_SECURE=true`, `CORS_ORIGINS=[]`, `STORAGE_PROVIDER=s3`, explicit
  `ENCRYPTION_KEYS` with an active key id, and `.env` at mode 600.
* **Only 22, 80 and 443 answer from outside.** Postgres, Redis and MinIO are on
  loopback; the web, API, worker, n8n and ClamAV containers publish nothing at
  all. Caddy alone holds 80/443, and `DOCKER-USER` drops anything on them that
  did not come from Cloudflare.
* `unattended-upgrades` is active. Nine package updates are pending — worth a
  look, but no security backlog was reported.
* All four Telegram listeners (`rognar`, `rognar-rossa`, `toplevel`, `usa`) are
  running, and nothing unexpected is on the machine.

Both open questions from this pass were answered by the owner the same day, and
both are closed:

* **`boss@rossacrm.tech` is the owner's own account.** Not a finding. It is
  recorded here because an Owner-role account that appears in no prior access
  record should always be asked about rather than assumed.
* **`/opt/webscout` was removed.** It appeared on 26 August — a
  Reddit/OpenRouter/Telegram scanner with its own `.env` — and was a sixth
  project's credentials sitting on the machine that serves the CRM. Nothing
  referenced it: no systemd unit, no cron entry, no container, no built image,
  and its `data/` directory was empty, so it had never run. Deleted on the
  owner's instruction after its code (without the `.env`) was archived off the
  server. The credentials in that file — Reddit client id and secret, an
  OpenRouter key, a Telegram bot token — went with it and should be revoked at
  their sources if the project is not coming back.

The pravosudie lesson stands regardless: five listener projects still share this
box with the CRM, and shared root is how one project's routine mistake becomes
another project's outage.

---

## Order I would fix these in

1. **Enrol both Owner accounts in TOTP** (§9) and **delete the three seeded demo
   accounts** (§10). Minutes each, no deploy, and together they close the
   shortest path into the system.
2. **`header_up X-Forwarded-For {client_ip}`** in the Caddyfile (§1) — one line,
   and it is the only code-side finding that changes what an attacker can do
   from outside today.
3. **Bump Next to 16.3.3** (§2) — it is now a minor release, and it clears four
   high advisories.
4. **`RemainAfterExit=yes` off the origin-firewall service** (§11), so the
   allow-list resumes refreshing itself.
5. **Cloudflare Access on `n8n.`** (§4) — ten minutes, removes a password form
   from the public internet.
6. **`umask 077` and `--env-file` in the backup script** (§5), and pin the three
   floating image tags (§6).
7. **The CSP nonce** (§3) and the `roles` policy (§7) as ordinary development
   work.
8. **The owner's items**: IP-lock the R2 token, duplicate the age key, confirm
   who `boss@rossacrm.tech` is.

Items 2, 3, 4 and 6 are code and land through a normal deploy. Items 1 and 5 are
console work on a running system and touch no code at all.

Nothing found this round is architectural. The tenant isolation, injection
safety, session handling and secrets hygiene that the last audit praised are all
still true, the new features arrived with their own RLS, their own signature
checks and their own rate limits, and every fix from 9 August is still in force
on the host.

What this round found instead is a pattern of controls that are *present but not
load-bearing*: rate limits keyed on a header anyone can set, an MFA policy that
asks rather than requires, a firewall refresh that has quietly stopped
refreshing, and seeded accounts that outlived their purpose. None is an open
door. Each is a lock that would not hold if someone leaned on it.

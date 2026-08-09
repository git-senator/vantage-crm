# Security audit — 9 August 2026

Full review of the Vantage/Rossa CRM: source, configuration, the production
host, and the Rognar scanner beside it. Findings are ordered by what an attacker
would reach first, not by where they live in the stack.

Everything below was verified by running the check, not by reading the code and
assuming. Where a claim could not be verified, it says so.

---

## 1. CRITICAL — the CRM answers on plain HTTP, to the whole internet

`http://179.198.106.11:3000/login` returns the login page. No TLS, no Caddy.

```
$ curl -o /dev/null -w '%{http_code}' http://179.198.106.11:3000/
307
```

**Why it happened.** `docker-compose.yml` publishes the web container as
`0.0.0.0:3000`, and Docker writes its own iptables rules *ahead* of UFW. The
firewall says only 22, 80 and 443 are open, and the firewall is telling the
truth about itself — Docker simply is not asking it.

**What it costs.** A password typed into that page crosses the network in
clear text. Anyone on the path — a café router, an ISP, a compromised hop —
reads it. `COOKIE_SECURE=true` means the session cookie itself will not travel
over HTTP, so the *session* is protected; the *credentials* are not, and they
are the more valuable of the two because they also unlock the recovery email.

Every other port is bound correctly: Postgres, Redis and MinIO all listen on
`127.0.0.1` only, and the API, worker and n8n publish nothing at all. This is
one line out of place, not a pattern.

**Fix.** Bind the mapping to loopback (`127.0.0.1:3000:3000`) or remove it
entirely — Caddy reaches the web container over the compose network and does not
need a published port. One line, one `docker compose up -d`, no rebuild.

---

## 2. HIGH — SSH accepts root with a password, and nothing counts the attempts

```
permitrootlogin      yes
passwordauthentication yes
fail2ban             not installed
```

Three facts that are each defensible alone and dangerous together: the most
privileged account on the box, reachable by guessing, with no lockout. The host
holds the database, every customer record, the object store and the `.env` that
unlocks Google, Groq, Telegram and Resend.

One failed password attempt was logged in the last 24 hours, which says the box
has not been found yet — not that it is safe.

**Fix.** `PasswordAuthentication no` and `PermitRootLogin prohibit-password`,
then install fail2ban. The key you already use keeps working; nothing else
changes. `x11forwarding yes` is also on and is surface for nothing you use.

---

## 3. HIGH — nothing backs up the database on a schedule

There are exactly two dumps on the server, both made by hand during deploys
(6 and 9 August). `crontab` is empty. The only systemd backup timer belongs to
Debian's package database, not to yours.

`deploy/backup/backup.sh` exists and even knows how to push to S3 — it is simply
never run.

**What it costs.** A disk failure, a bad migration or a `DROP` in the wrong
window loses every lead, client, deal and document since 6 August, permanently.
This is the finding most likely to actually hurt you, and the cheapest to close.

**Fix.** A systemd timer or cron entry calling the existing script, nightly,
plus one restore rehearsal to prove the dump is readable. Off-site matters:
a backup that lives only on the machine it protects is not a backup.

---

## 4. HIGH — the leaked Google service-account key is still valid

On 7 August I printed `.env` lines with a mask that only covered names ending in
`KEY`, `TOKEN` or `SECRET`. `GOOGLE_CALENDAR_SA_B64` did not match and came
through in full. It decodes to the complete service-account JSON for
`rossa-crm-calendar@rossa-crm.iam.gserviceaccount.com`, **RSA private key
included**. It reached this conversation only — no third party — but it has not
been rotated since.

**Fix.** Google Cloud Console → IAM & Admin → Service Accounts →
`rossa-crm-calendar` → Keys → create a new key, update `GOOGLE_CALENDAR_SA_B64`
in both `.env` files, then **delete the old key** — the last step is the one
that matters.

The Telegram bot token and the `my.telegram.org` credentials were also pasted
into chat during setup. They are lower value and rotating them is easy
(`/revoke` in BotFather), but they belong on the same list.

---

## 5. MEDIUM — uploads are stored and served without being scanned

`MALWARE_SCAN_ENABLED` is unset in production, and the default is off. Files
land in object storage and are served back from signed URLs with no scan in
between.

The pipeline itself is sound — quarantine states exist, `ClamAvScanner` is
written and the config refuses to boot production with the EICAR test scanner
enabled. Nothing is missing but the switch and a ClamAV container.

Partly mitigated: the type allowlist verifies magic bytes rather than trusting
the declared type, and SVG is not on it, so the stored-XSS route is closed. The
residual risk is a malicious document reaching a colleague's machine through
your CRM.

---

## 6. MEDIUM — the page's script policy still allows inline scripts

Live header on `rossacrm.tech`:

```
script-src 'self' 'unsafe-inline'
```

`'unsafe-inline'` is what a cross-site-scripting payload needs to execute. The
rest of the policy is tight — `default-src 'self'`, `object-src 'none'`,
`frame-ancestors 'none'`, a scoped `img-src` — so this one directive is doing
most of the weakening. `next.config.ts` already says a per-request nonce was the
plan; it was never finished.

---

## 7. MEDIUM — known vulnerabilities in dependencies

| Package | Version | Advisory | Fix |
|---|---|---|---|
| cryptography | 49.0.0 | PYSEC-2026-3552 | 50.0.0 |
| postcss | ≤8.5.22 (via Next) | 4 advisories, incl. arbitrary `.map` file read | Next 16.3 |
| sharp | <0.35.0 (via Next) | libvips CVEs (4) | Next 16.3 |

`cryptography` is the one to take seriously — it is in the authentication path.
The two npm packages arrive through Next.js and both fixes require a Next major
bump, so they are a planned upgrade rather than a quick patch. postcss runs at
build time; sharp only matters if image optimisation is used, which the photo
gallery deliberately bypasses.

---

## 8. LOW — three tenant tables have no row-level security

Of 89 tables carrying `organization_id`, 86 are under an RLS policy. The three
that are not:

* `access_requests` — **by design.** A request arrives before the person has an
  account, so there is no tenant to scope by. Gated on `users.manage`.
* `invitations` — **by design.** Resolved by an unguessable token before any
  session exists, and there is no listing endpoint.
* `roles` — scoped in the service layer
  (`organization_id = current OR organization_id IS NULL`, so system roles stay
  shared), but with no database policy behind it. `operational_policy_statements`
  in `sql_objects.py` was written for exactly this shape and is not applied here.
  A defence-in-depth gap, not an open door.

---

## What is already right

Worth stating plainly, because a report that lists only problems misrepresents
the system.

**Secrets.** Nothing but `.env.example` has ever entered git history. No
credentials are hardcoded anywhere in the source. Both `.env` files on the
server are mode 600.

**Tenant isolation.** 86 of 89 tables enforce it in the database, not just in
code. The application role owns nothing and has no `BYPASSRLS`; migrations run
as a separate role. The bootstrap paths that must run before a tenant is known
use `SECURITY DEFINER` functions that return ids and nothing else, rather than a
blanket exemption.

**Injection.** No string-interpolated SQL anywhere. No `os.system`, no
`subprocess`, no `shell=True`.

**SSRF.** The webhook action resolves the hostname and rejects private,
loopback and link-local addresses — including `169.254.169.254`, the cloud
metadata endpoint, by name in a comment. This is the check most codebases skip.

**Uploads.** Content type is verified against magic bytes, size is enforced from
what storage reports rather than what the client claims, and the API never
accepts file bytes at all.

**Sessions.** Cookies are httpOnly with SameSite Lax/Strict, `Secure` is
enforced in production by a startup check that refuses to boot without it, and
CSRF uses double-submit. Login is throttled at 5 attempts per 15 minutes.

**Headers.** HSTS with preload, `X-Frame-Options: DENY`, nosniff, a strict
referrer policy and a permissions policy — all live.

**MFA secrets are encrypted.** My own notes said otherwise; they were out of
date. Envelope encryption is configured and active in production.

**n8n** requires authentication — its REST API answers 401 unauthenticated.

**Rognar** runs on its own Docker network with no published ports and cannot
reach the CRM or n8n.

**Patching.** `unattended-upgrades` is active and no security updates are
pending.

**The repository is private.**

---

## Order I would fix these in

1. **Close port 3000** — minutes, and it is the only finding an attacker can
   use from the outside today.
2. **Schedule backups** — an hour, and it is the finding most likely to cost
   you everything.
3. **Harden SSH** — minutes, and it removes the brute-force path to the host.
4. **Rotate the Google key** — minutes, in the console.
5. Turn on scanning, upgrade `cryptography`, then the CSP nonce and the Next
   bump as ordinary work.

Nothing here requires re-architecting anything. The foundations — tenant
isolation, injection safety, session handling — are in good shape; what is
exposed is configuration at the edges.

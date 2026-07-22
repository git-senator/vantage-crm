# Multi-Factor Authentication

Status: **Phase 3.7 implemented.**
Related: [AUTHENTICATION.md](./AUTHENTICATION.md) · [SECURITY.md](./SECURITY.md) · [PERMISSIONS.md](./PERMISSIONS.md) · [ROADMAP.md](./ROADMAP.md)

TOTP (RFC 6238) with single-use recovery codes, a two-step login, and role-based
enforcement that nudges rather than locks out.

---

## 1. Why TOTP is implemented, not imported

Rolling your own crypto is usually a mistake. This is the narrow case where it
is not.

TOTP is HMAC-SHA1 over a counter, truncated — about thirty lines of `hmac` and
`struct`. There is no key agreement, no padding, no parsing of
attacker-controlled structure, and the RFC ships an official conformance vector
table, which the test suite checks against. `pyotp` is a thin wrapper over
exactly this. Taking the dependency would add supply-chain surface to avoid code
that fits on one screen and has an authoritative correctness oracle.

SHA-1 is correct here, and that is not an oversight. HMAC does not rely on
collision resistance, so SHA-1's weaknesses do not apply — and every
authenticator app in circulation assumes SHA-1 for `otpauth://` URIs. Choosing
SHA-256 would be a marginally stronger primitive that a large fraction of users
could not enrol with.

---

## 2. Enrolment is two steps

```
POST /auth/mfa/enroll    →  secret + otpauth:// URI      (nothing is enabled)
POST /auth/mfa/activate  →  verifies a code, turns it on, returns recovery codes
```

A one-step version — issue the secret and flip the flag — locks people out of
their own accounts with a secret they never successfully scanned. That is the
single most common way an MFA rollout generates support tickets.

**Re-enrolling while already enabled is refused.** Otherwise anyone holding a
live session could silently swap the second factor to a device they control,
which is precisely the escalation MFA exists to prevent. Turning it off first
requires the password.

Recovery codes are returned **once**, at activation. They are stored hashed, so
there is no endpoint that can show them again — one that could would mean
storing them recoverably, which defeats hashing them at all.

---

## 3. Login becomes two steps

```
POST /auth/login         →  { mfa_required: true, challenge_token, expires_at }
POST /auth/mfa/verify    →  cookies + session
```

The challenge response carries **no profile** — no name, no avatar, no
permissions. Until the second factor is proved the caller has not
authenticated, and returning any of that would confirm the password was correct,
which is the one bit a credential-stuffing attacker is looking for.

The challenge token is typed `mfa`. `decode_access_token` requires
`typ == "access"`, so the challenge cannot be replayed as a session token; and
`decode_challenge` requires `typ == "mfa"`, so a session token cannot be used to
skip the factor it was issued after. Both directions have a test.

**The login rate limit is not cleared when a challenge is issued.** The login is
not complete. Clearing it there would let an attacker who has the password keep
the account unthrottled while grinding at the second factor.

`/auth/mfa/verify` carries its own per-IP limit. Six digits is a space of 10⁶,
which an unthrottled endpoint would let someone walk in an afternoon once they
hold a valid challenge token.

---

## 4. Replay

A TOTP code is valid for its entire 30-second step. Without a memory of the last
accepted step, an attacker who observes one — over a shoulder, in a screenshot,
in a phished form — can reuse it for the remainder.

`users.mfa_last_counter` stores the last accepted counter and verification
refuses anything at or before it. The trade-off is explicit: the legitimate user
cannot submit the same code twice either, and must wait for the next one. A
duplicate-submit annoyance against a live replay window is not a close call.

The accepted window is ±1 step (30 seconds either side) for clock drift. Wider
tolerance means an observed code stays useful for longer.

Comparison is constant-time. A timing oracle over 10⁶ candidates is genuinely
exploitable.

---

## 5. Recovery codes

Ten codes, ~50 bits each, formatted `XXXXX-XXXXX` because they get written on
paper and a grouped string is transcribed correctly far more often than a run of
ten characters. The alphabet excludes `I`, `L`, `O` and `U` — `0`/`O` and `1`/`I`
are where reading a code off a screen goes wrong.

**Stored in a table, not a JSON column, because single use has to be enforced by
the database.** Spending a code is an UPDATE with `used_at IS NULL` in its
predicate, so two concurrent attempts cannot both succeed. A read-modify-write
over a JSON array has a race that testing does not catch and an attacker with a
captured code can.

Hashed with SHA-256 rather than Argon2. A recovery code is 50 bits of uniform
randomness that this server generated — there is no dictionary to defend
against, and making verification expensive would hand an attacker a cheap
denial-of-service on the login path.

**A spent code is kept, not deleted.** "One of your recovery codes was used on
Tuesday" is exactly the signal that tells someone their phone was compromised,
and a deleted row cannot say it. `auth.mfa.recovery_used` is audited as high
severity for the same reason: someone losing their phone looks identical to
someone else using a stolen code.

---

## 6. Removing a factor requires the password

`/auth/mfa/disable` and `/auth/mfa/recovery-codes` both re-authenticate. A
session alone is not enough, because weakening the account is exactly what an
attacker holding a stolen session would do to make their access durable.

Both are audited; `auth.mfa.disabled` is high severity.

---

## 7. Enforcement is a nudge with teeth

`MFA_REQUIRED_ROLES` defaults to `owner, admin`. A user holding one of those who
has not enrolled **still gets a session**, flagged `setup_required`, and the
frontend routes them to enrolment.

Refusing the login outright was the alternative and is wrong: it locks out the
owner the moment somebody grants them the role — including, in the worst case,
the only owner of the workspace, with no one able to undo it.

`setup_required` is computed server-side so the role list lives in one place
rather than being duplicated into the frontend where it can drift.

---

## 8. Known limitation: the secret at rest

`users.mfa_secret` is stored as issued. That is a stated trade-off, not an
oversight.

Encryption at rest for this column needs a key managed somewhere other than
beside the data — a KMS — which is Phase 5's secrets work. What is in place
meanwhile:

* the column is in `NEVER_DIFF_FIELDS`, so it cannot reach the audit log;
* it appears in no read schema and no API response after enrolment;
* the logger's redaction list covers any key containing `secret`.

The residual exposure is a database dump, which is the same exposure the
password hashes already have.

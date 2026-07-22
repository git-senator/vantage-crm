"""Object-key construction and filename hygiene.

The key layout is:

    org/<organization_id>/<entity_type>/<entity_id>/<attachment_id>/<safe_filename>

Every component except the last is a UUID or a constrained vocabulary word, so
the only part a user influences is the trailing filename — and that is
sanitised here rather than at the call site, because "sanitise the filename"
enforced by convention is sanitisation that will eventually be forgotten.

**Why the organization id leads.** Tenant-first ordering makes a per-tenant
lifecycle rule, a per-tenant deletion, or a per-tenant cost report a prefix
operation rather than a bucket scan. It also means an object key literally
carries the tenant it belongs to: `key_belongs_to_organization` is a cheap,
independent second check that a request cannot reach across tenants even if a
row were somehow mis-scoped. Defence in depth behind RLS, not instead of it.

**Why the attachment id is in the path.** Two files called `contract.pdf` on
the same deal must not collide, and reusing a key would let a re-upload silently
overwrite an already-scanned, already-audited object.
"""

from __future__ import annotations

import posixpath
import re
import unicodedata
from uuid import UUID

#: Anything outside this set is replaced. Deliberately strict: storage keys end
#: up in URLs, log lines, `Content-Disposition` headers and shell-based ops
#: tooling, and each of those has its own quoting hazards.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_REPEATED_DOTS = re.compile(r"\.{2,}")

#: Filesystem-style names that are still special on Windows, where a support
#: engineer may well be the one downloading a batch of these.
_RESERVED_STEMS = frozenset(
    {
        "con", "prn", "aux", "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
)

MAX_FILENAME_LENGTH = 200


def sanitize_filename(filename: str) -> str:
    """Reduce a client-supplied filename to something safe to put in a key.

    Path traversal is neutralised by taking the basename of both separator
    conventions before anything else — a client that sends
    `../../etc/passwd` or `..\\..\\secrets` must not be able to steer the key,
    and stripping only after replacing characters would leave the traversal
    already fused into the string.

    Returns `"file"` when nothing usable survives, rather than an empty
    component that would produce a key ending in `/`.
    """
    # Both separators, in this order: a Windows client sends backslashes, and
    # posixpath does not treat them as separators.
    candidate = filename.replace("\\", "/")
    candidate = posixpath.basename(candidate)

    # Decompose accents to ASCII rather than deleting them: "résumé.pdf"
    # becoming "rsum.pdf" is a worse outcome than "resume.pdf".
    candidate = (
        unicodedata.normalize("NFKD", candidate)
        .encode("ascii", "ignore")
        .decode("ascii")
    )

    candidate = _UNSAFE.sub("_", candidate).strip("._-")
    # `a..b` is harmless, but `..` as a whole component is not, and collapsing
    # runs removes the class rather than the instance.
    candidate = _REPEATED_DOTS.sub(".", candidate)

    if not candidate:
        return "file"

    stem, dot, extension = candidate.rpartition(".")
    if dot and stem.lower() in _RESERVED_STEMS:
        stem = f"{stem}_file"
    elif not dot and candidate.lower() in _RESERVED_STEMS:
        return f"{candidate}_file"

    candidate = f"{stem}{dot}{extension}" if dot else candidate

    if len(candidate) > MAX_FILENAME_LENGTH:
        # Truncate the stem, never the extension — the extension is what tells
        # a human (and a download handler) what the file is.
        stem, dot, extension = candidate.rpartition(".")
        if dot and len(extension) <= 12:
            keep = MAX_FILENAME_LENGTH - len(extension) - 1
            candidate = f"{stem[:keep]}.{extension}"
        else:
            candidate = candidate[:MAX_FILENAME_LENGTH]

    return candidate or "file"


def organization_prefix(organization_id: UUID) -> str:
    return f"org/{organization_id}/"


def build_storage_key(
    *,
    organization_id: UUID,
    entity_type: str,
    entity_id: UUID,
    attachment_id: UUID,
    filename: str,
) -> str:
    """The deterministic key an attachment's bytes occupy."""
    return (
        f"{organization_prefix(organization_id)}"
        f"{entity_type}/{entity_id}/{attachment_id}/"
        f"{sanitize_filename(filename)}"
    )


def key_belongs_to_organization(key: str, organization_id: UUID) -> bool:
    """Independent tenant check on a key, for use before any storage call.

    RLS already guarantees the row came from the caller's tenant. This asserts
    the *key on that row* also does, which is what stops a corrupted or
    hand-edited `storage_key` from being signed into a cross-tenant URL.
    """
    return key.startswith(organization_prefix(organization_id))

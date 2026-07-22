"""Content verification for uploaded objects.

Three separate questions, deliberately not collapsed:

1. **Is this a type we accept at all?** An allowlist, not a blocklist. A
   blocklist of "dangerous" extensions is a losing game — it has to be complete,
   and it never is.
2. **Do the bytes agree with the declaration?** The client says
   `application/pdf`; the first bytes say otherwise. The declaration is a hint
   from an untrusted party and the bytes are evidence, so the bytes win. This is
   what stops `invoice.pdf` from actually being an HTML document that runs
   script when someone opens the download URL.
3. **Is it small enough?** Checked against `head` at finalization, from the
   size storage reports — never from a client-declared number.

**Why sniffing here rather than libmagic.** `python-magic` needs a native
`libmagic` on every machine that runs the API, including CI and Windows dev
boxes, and it is a large C surface parsing hostile input. The set of types a
CRM actually accepts is small and their signatures are stable and short, so a
table beats a dependency. If the accepted set ever grows into genuinely
ambiguous territory, this is the one module that changes.

**What this is not.** It is not a virus scanner. A well-formed PDF carrying a
malicious payload passes every check here — that is what the quarantine
lifecycle and the scan job exist for.
"""

from __future__ import annotations

from dataclasses import dataclass

#: MIME types the product accepts, mapped to the byte signatures that confirm
#: them. An empty tuple means "no reliable signature" — see `_verify_textual`.
#:
#: Office formats are ZIP containers (`PK\x03\x04`), and the legacy ones are
#: OLE2 compound files; neither signature distinguishes docx from xlsx, which
#: is fine. The question this answers is "are these bytes plausibly the family
#: that was declared", not "is this exactly a Word document".
_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    # --- documents ---
    "application/pdf": (b"%PDF-",),
    "application/msword": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    "application/vnd.ms-excel": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    "application/vnd.ms-powerpoint": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        b"PK\x03\x04",
    ),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (
        b"PK\x03\x04",
    ),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (
        b"PK\x03\x04",
    ),
    "application/vnd.oasis.opendocument.text": (b"PK\x03\x04",),
    "application/vnd.oasis.opendocument.spreadsheet": (b"PK\x03\x04",),
    "application/zip": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    # --- images ---
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),
    "image/tiff": (b"II*\x00", b"MM\x00*"),
    "image/heic": (),  # `ftyp` box sits at offset 4; handled below
    # --- textual: no signature exists, by definition ---
    "text/plain": (),
    "text/csv": (),
    "text/markdown": (),
    "application/json": (),
}

ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(_SIGNATURES)

#: Byte prefixes that mean "this will be interpreted as markup or script by
#: something, somewhere". None of these may masquerade as a textual upload,
#: because a text/plain that is actually HTML is the stored-XSS path: a browser
#: that sniffs the download would render it in whatever origin served it.
_MARKUP_PREFIXES: tuple[bytes, ...] = (
    b"<!doctype html",
    b"<html",
    b"<?xml",
    b"<svg",
    b"<script",
    b"<%",
    b"#!",
)

#: Executable/installer signatures, rejected regardless of what was declared.
#: The allowlist already excludes them; this makes a *mislabelled* one fail on
#: the evidence rather than on the label.
_EXECUTABLE_SIGNATURES: tuple[bytes, ...] = (
    b"MZ",              # PE / DOS
    b"\x7fELF",         # ELF
    b"\xca\xfe\xba\xbe",  # Mach-O fat / Java class
    b"\xcf\xfa\xed\xfe",  # Mach-O 64
    b"\xfe\xed\xfa\xce",  # Mach-O 32
    b"\xd4\xc3\xb2\xa1",  # pcap — not executable, but never a CRM document
)

#: How many bytes the verifier needs. Every signature above is well inside
#: this, and it bounds what a hostile object can make the API read.
SNIFF_BYTES = 4096


@dataclass(frozen=True, slots=True)
class ContentVerdict:
    ok: bool
    #: What to store as the authoritative content type. On success this is the
    #: declared type, normalised; on failure it is whatever was actually seen,
    #: for the audit trail.
    content_type: str
    reason: str | None = None


def normalise_content_type(declared: str) -> str:
    """Strip parameters and case: `Text/CSV; charset=utf-8` -> `text/csv`."""
    return declared.split(";", 1)[0].strip().lower()


def is_allowed_content_type(declared: str) -> bool:
    return normalise_content_type(declared) in ALLOWED_CONTENT_TYPES


def _looks_executable(head: bytes) -> bool:
    return any(head.startswith(signature) for signature in _EXECUTABLE_SIGNATURES)


def _looks_like_markup(head: bytes) -> bool:
    stripped = head.lstrip()[:64].lower()
    return any(stripped.startswith(prefix) for prefix in _MARKUP_PREFIXES)


def _verify_textual(head: bytes) -> ContentVerdict | None:
    """Textual types have no signature, so they are verified by exclusion.

    Rejected if the bytes are markup (the stored-XSS path), executable, or not
    decodable as UTF-8 — a "text/plain" full of NUL bytes is not text, and
    whatever it is was not declared honestly.
    """
    if _looks_like_markup(head):
        return ContentVerdict(
            ok=False,
            content_type="text/html",
            reason="Declared as text but the content is markup or script.",
        )
    if _looks_executable(head):
        return ContentVerdict(
            ok=False,
            content_type="application/octet-stream",
            reason="Declared as text but the content is an executable.",
        )
    if b"\x00" in head:
        # No text encoding this product accepts embeds NUL, and UTF-16 (which
        # does) is not in the allowlist. A "text/plain" containing one is
        # binary wearing a label.
        return ContentVerdict(
            ok=False,
            content_type="application/octet-stream",
            reason="Declared as text but the content is binary.",
        )
    try:
        # A truncated read can split a multi-byte character at the boundary, so
        # the last few bytes are dropped — but only when the buffer really is a
        # truncation. Trimming a short whole file would hide its tail.
        candidate = head[:-3] if len(head) >= SNIFF_BYTES else head
        candidate.decode("utf-8")
    except UnicodeDecodeError:
        return ContentVerdict(
            ok=False,
            content_type="application/octet-stream",
            reason="Declared as text but the content is binary.",
        )
    return None


def verify_content(declared: str, head: bytes) -> ContentVerdict:
    """Do these bytes support the declared type?

    `head` is the object's first bytes as read back *from storage*, never from
    the request — the whole point is to check what was actually stored.
    """
    content_type = normalise_content_type(declared)

    if content_type not in ALLOWED_CONTENT_TYPES:
        return ContentVerdict(
            ok=False,
            content_type=content_type,
            reason=f"Content type is not accepted: {content_type}",
        )

    if not head:
        return ContentVerdict(
            ok=False,
            content_type=content_type,
            reason="The uploaded object is empty.",
        )

    if _looks_executable(head):
        return ContentVerdict(
            ok=False,
            content_type="application/octet-stream",
            reason="The content is an executable, whatever it was declared as.",
        )

    signatures = _SIGNATURES[content_type]

    if not signatures:
        if content_type == "image/heic":
            # ISO-BMFF: a 4-byte box length, then `ftyp`, then the brand.
            if head[4:8] == b"ftyp":
                return ContentVerdict(ok=True, content_type=content_type)
            return ContentVerdict(
                ok=False,
                content_type="application/octet-stream",
                reason="Not an ISO base-media file.",
            )
        failure = _verify_textual(head)
        return failure or ContentVerdict(ok=True, content_type=content_type)

    if any(head.startswith(signature) for signature in signatures):
        # WEBP is RIFF-with-a-brand; RIFF alone is also WAV and AVI.
        if content_type == "image/webp" and head[8:12] != b"WEBP":
            return ContentVerdict(
                ok=False,
                content_type="application/octet-stream",
                reason="RIFF container is not WEBP.",
            )
        return ContentVerdict(ok=True, content_type=content_type)

    return ContentVerdict(
        ok=False,
        content_type="application/octet-stream",
        reason=(
            f"The stored bytes do not match the declared type ({content_type})."
        ),
    )

"""Malware scanning — the seam, and the adapters behind it.

Two scanners ship, and the difference between them matters:

  * **`ClamAvScanner`** — a real engine, spoken to over clamd's INSTREAM
    protocol. This is the production answer.
  * **`EicarSignatureScanner`** — detects the **EICAR test file** and nothing
    else. It is not antivirus, it will not catch a real sample, and it must
    never be mistaken for protection. It exists because the quarantine pipeline
    — scan, verdict, quarantine, delete the bytes, audit, never serve — is real
    code that needs proving end to end, and EICAR is the standard, harmless,
    universally recognised way to prove it.

`assert_production_ready` refuses to start a production process with scanning
enabled while `eicar` is the configured engine, so the gap cannot be closed by
accident and cannot be forgotten either.

**Why the interface takes bytes rather than a key.** Whichever engine is
configured, the scan happens on content; keeping storage out of the contract
means a scanner can be tested with a byte string and no bucket at all.

**Why a failure is a verdict rather than an exception.** A scanner being down is
an operational condition the pipeline already handles — leave the file
unpublished, retry later — not a crash. `failed` is a third outcome precisely so
"we could not tell" never collapses into "clean", which is the one substitution
that turns a scanner into a liability.
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

ScanVerdict = Literal["clean", "infected", "failed"]

# The EICAR standard anti-malware test string, split so this source file does
# not itself trip a scanner watching the repository — which is exactly the
# accident the split prevents, and the reason it is written this way.
_EICAR = (
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-"
    b"STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)

#: clamd streams in chunks with a 4-byte big-endian length prefix. 64 KiB is
#: clamd's own default read size; larger chunks gain nothing and risk tripping
#: StreamMaxLength on a conservatively tuned server.
_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class ScanResult:
    verdict: ScanVerdict
    #: What was found, for the audit entry and the user-facing failure reason.
    signature: str | None = None
    scanner: str = "unknown"


@runtime_checkable
class MalwareScanner(Protocol):
    name: str

    async def scan(self, content: bytes) -> ScanResult:
        """Verdict on these bytes.

        Returning `failed` rather than raising is deliberate: a scanner being
        down is an operational condition the pipeline handles (leave the file
        unpublished, retry later), not an exception the job should crash on.
        """
        ...

    async def ping(self) -> bool:
        """Whether the engine is reachable.

        Separate from `scan` so the admin health panel can report "the scanner
        is down" before a user's upload discovers it. An unreachable scanner
        means uploads silently stop being published, which presents as the
        upload feature being broken rather than as a dependency being down.
        """
        ...


class EicarSignatureScanner:
    """Detects the EICAR test file. Nothing else. See the module docstring."""

    name = "eicar"

    async def scan(self, content: bytes) -> ScanResult:
        if _EICAR in content:
            return ScanResult(
                verdict="infected",
                signature="EICAR-Test-File",
                scanner=self.name,
            )
        return ScanResult(verdict="clean", scanner=self.name)

    async def ping(self) -> bool:
        # Always reachable: it is a substring check in this process. That is an
        # honest answer about reachability and says nothing about usefulness,
        # which the production guard covers separately.
        return True


class ClamAvScanner:
    """A real engine, over clamd's INSTREAM protocol.

    INSTREAM rather than a path-based `SCAN` because the path commands require
    clamd to share a filesystem with the caller. Streaming works when clamd is a
    separate container, a sidecar, or a shared service — which is every
    deployment this application actually has.

    The protocol, precisely: send `zINSTREAM\\0`, then length-prefixed chunks,
    then a zero-length chunk to end the stream. clamd replies `stream: OK`,
    `stream: <signature> FOUND`, or an error string.

    A timeout wraps the whole exchange. A scanner that hangs holds a worker slot
    indefinitely, and a queue of stalled scan jobs is a worse outcome than a
    scan that gives up and is retried.
    """

    name = "clamav"

    def __init__(self, host: str, port: int, timeout: float = 30.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout

    async def scan(self, content: bytes) -> ScanResult:
        try:
            response = await asyncio.wait_for(
                self._instream(content), timeout=self.timeout
            )
        except TimeoutError:
            logger.warning(
                "clamav_scan_timed_out",
                extra={"host": self.host, "bytes": len(content)},
            )
            return ScanResult(verdict="failed", signature="timeout", scanner=self.name)
        except Exception as exc:
            # Broad on purpose: connection refused, DNS failure, reset mid-stream
            # and a protocol surprise all mean the same thing to the pipeline —
            # nobody looked at this file — and all have the same correct
            # response, which is to leave it unpublished and retry.
            logger.warning(
                "clamav_scan_failed",
                extra={"host": self.host, "error": type(exc).__name__},
                exc_info=True,
            )
            return ScanResult(
                verdict="failed", signature=type(exc).__name__, scanner=self.name
            )

        return self._interpret(response)

    def _interpret(self, response: str) -> ScanResult:
        """clamd's reply, as a verdict.

        `ERROR` maps to `failed`, never to `infected`. clamd returns it for
        conditions like "size limit exceeded" — a file nobody looked at, which
        is not the same as a file found to be malicious, and quarantining on it
        would delete a user's legitimate document.
        """
        cleaned = response.strip().rstrip("\0").strip()

        if cleaned.endswith("OK"):
            return ScanResult(verdict="clean", scanner=self.name)

        if cleaned.endswith("FOUND"):
            # `stream: Eicar-Signature FOUND` → the middle is the signature.
            signature = cleaned.removesuffix("FOUND").strip()
            if ":" in signature:
                signature = signature.split(":", 1)[1].strip()
            return ScanResult(
                verdict="infected",
                signature=signature or "unknown",
                scanner=self.name,
            )

        logger.warning("clamav_unexpected_response", extra={"response": cleaned[:200]})
        return ScanResult(verdict="failed", signature=cleaned[:100], scanner=self.name)

    async def _instream(self, content: bytes) -> str:
        reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            writer.write(b"zINSTREAM\0")
            for start in range(0, len(content), _CHUNK_BYTES):
                chunk = content[start : start + _CHUNK_BYTES]
                writer.write(struct.pack("!I", len(chunk)) + chunk)
            # The zero-length chunk terminates the stream. Without it clamd
            # waits for more data until its own timeout, and the scan looks like
            # a hang rather than a protocol mistake.
            writer.write(struct.pack("!I", 0))
            await writer.drain()

            raw = await reader.read(4096)
            return raw.decode("utf-8", errors="replace")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # pragma: no cover — transport-dependent
                # The verdict has already been read. A connection that fails to
                # close cleanly must not turn a completed scan into a failed one.
                logger.debug("clamav_close_failed", exc_info=True)

    async def ping(self) -> bool:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=self.timeout
            )
        except Exception:
            logger.warning("clamav_unreachable", extra={"host": self.host})
            return False
        try:
            writer.write(b"zPING\0")
            await writer.drain()
            raw = await asyncio.wait_for(reader.read(64), timeout=self.timeout)
            return raw.strip().rstrip(b"\0") == b"PONG"
        except Exception:
            return False
        finally:
            writer.close()


def build_scanner(settings: Settings) -> MalwareScanner:
    match settings.MALWARE_SCANNER:
        case "clamav":
            return ClamAvScanner(
                settings.CLAMAV_HOST,
                settings.CLAMAV_PORT,
                settings.CLAMAV_TIMEOUT_SECONDS,
            )
        case "eicar":
            return EicarSignatureScanner()
        case unknown:  # pragma: no cover — the Literal makes this unreachable
            raise ValueError(f"Unsupported MALWARE_SCANNER: {unknown}")


__all__ = [
    "ClamAvScanner",
    "EicarSignatureScanner",
    "MalwareScanner",
    "ScanResult",
    "ScanVerdict",
    "build_scanner",
]

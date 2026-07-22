"""Malware scanning — the seam, and the one adapter that exists so far.

Be clear about what ships here. `EicarSignatureScanner` detects the **EICAR
test file** and nothing else. It is not antivirus, it will not catch a real
sample, and it must never be mistaken for protection. It exists because the
quarantine pipeline — scan, verdict, quarantine, delete the bytes, audit, never
serve — is real code that needs proving end to end, and EICAR is the standard,
harmless, universally recognised way to prove it.

Wiring a real engine is one adapter against `MalwareScanner` and one arm of
`build_scanner`. Until that lands, `assert_production_ready` refuses to start a
production process with scanning enabled *and* this scanner configured, so the
gap cannot be closed by accident and cannot be forgotten either.

Why the interface takes bytes rather than a key: whichever engine arrives, the
scan happens on content, and keeping storage out of the contract means a
scanner can be tested with a byte string and no bucket at all.
"""

from __future__ import annotations

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


def build_scanner(settings: Settings) -> MalwareScanner:
    match settings.MALWARE_SCANNER:
        case "eicar":
            return EicarSignatureScanner()
        case unknown:  # pragma: no cover — the Literal makes this unreachable
            raise ValueError(f"Unsupported MALWARE_SCANNER: {unknown}")

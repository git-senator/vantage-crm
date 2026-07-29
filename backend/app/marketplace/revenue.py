"""Revenue calculations — the platform/developer split, as pure arithmetic.

Phase 9.4. Every marketplace charge is split between the platform (its take rate)
and the integration's developer (the remainder). The split is deterministic
integer arithmetic in basis points, so it never drifts and always reconciles:
``platform + developer == gross`` exactly, with the platform taking the floor and
the developer the remainder (the customer-friendly rounding). This is the
foundation for developer revenue attribution — it computes the shares; the
service records which developer they belong to.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The kinds of revenue a marketplace event can represent.
REVENUE_KINDS: tuple[str, ...] = ("subscription", "usage", "one_time")

#: Basis points denominator (100% = 10_000 bps).
_BPS_DENOMINATOR = 10_000


def validate_kind(kind: str) -> str:
    if kind not in REVENUE_KINDS:
        raise ValueError(f"Unknown revenue kind '{kind}'.")
    return kind


@dataclass(frozen=True, slots=True)
class RevenueSplit:
    """A gross amount divided into platform and developer shares (cents)."""

    gross_cents: int
    platform_cents: int
    developer_cents: int


def split_revenue(gross_cents: int, platform_fee_bps: int) -> RevenueSplit:
    """Split a gross amount by the platform's take rate in basis points.

    The platform takes the floor of its share and the developer takes the exact
    remainder, so the two always sum back to the gross with no lost or invented
    cent. A negative gross is clamped to zero — a refund is not modelled here.
    """
    gross = max(0, gross_cents)
    bps = max(0, min(platform_fee_bps, _BPS_DENOMINATOR))
    platform = gross * bps // _BPS_DENOMINATOR
    return RevenueSplit(
        gross_cents=gross,
        platform_cents=platform,
        developer_cents=gross - platform,
    )


__all__ = [
    "REVENUE_KINDS",
    "RevenueSplit",
    "split_revenue",
    "validate_kind",
]

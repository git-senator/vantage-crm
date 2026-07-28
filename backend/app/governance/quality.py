"""The data-quality rules framework — dimensions, thresholds, and scoring.

A quality rule measures one dimension of an asset (completeness, validity, …) as
a percentage and asserts a threshold. A measured value at or above the threshold
passes; within a warn margin below it warns; further below it fails. A rule is
mandatory or advisory, and the rollup mirrors the compliance posture: a mandatory
failure fails the asset, an advisory one only warns.

All pure: a value and a threshold in, a status out; a list of results in, a score
out. The same measurements always yield the same score, which is what a quality
gate needs to be.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The dimensions a rule can measure (DAMA data-quality dimensions).
QUALITY_DIMENSIONS: tuple[str, ...] = (
    "completeness",
    "validity",
    "uniqueness",
    "timeliness",
    "consistency",
    "accuracy",
)

#: A measured rule's verdict.
QUALITY_STATUSES: tuple[str, ...] = ("pass", "warn", "fail", "not_measured")

#: Default points below the threshold within which a value warns rather than fails.
DEFAULT_WARN_MARGIN = 5.0


def evaluate_measurement(
    value: float, threshold: float, *, warn_margin: float = DEFAULT_WARN_MARGIN
) -> str:
    """Grade one measurement against its threshold. Pure and monotonic in
    `value`: a higher value never yields a worse status."""
    if value >= threshold:
        return "pass"
    if value >= threshold - warn_margin:
        return "warn"
    return "fail"


@dataclass(frozen=True, slots=True)
class QualityResult:
    dimension: str
    name: str
    status: str
    mandatory: bool
    value: float | None = None
    threshold: float | None = None


@dataclass(frozen=True, slots=True)
class QualityScore:
    status: str
    passed: int
    warned: int
    failed: int
    not_measured: int
    total: int


def score_quality(results: list[QualityResult]) -> QualityScore:
    """Roll per-rule results up into an asset (or tenant) quality score.

    A mandatory failure fails; otherwise any warn or advisory failure warns;
    otherwise pass. A rule that has never been measured does not count toward the
    verdict — an ungraded rule is not evidence of a problem — but it is surfaced
    so the gap is visible.
    """
    passed = sum(1 for r in results if r.status == "pass")
    warned = sum(1 for r in results if r.status == "warn")
    failed = sum(1 for r in results if r.status == "fail")
    not_measured = sum(1 for r in results if r.status == "not_measured")

    if any(r.status == "fail" and r.mandatory for r in results):
        status = "fail"
    elif any(r.status in ("warn", "fail") for r in results):
        status = "warn"
    else:
        status = "pass"

    return QualityScore(
        status=status,
        passed=passed,
        warned=warned,
        failed=failed,
        not_measured=not_measured,
        total=len(results),
    )


__all__ = [
    "DEFAULT_WARN_MARGIN",
    "QUALITY_DIMENSIONS",
    "QUALITY_STATUSES",
    "QualityResult",
    "QualityScore",
    "evaluate_measurement",
    "score_quality",
]

"""Enterprise data governance & advanced privacy (Phase 8.5).

The deterministic core of the data-governance layer: the classification framework
(sensitivity levels and the privacy labels that imply them) and the data-quality
rules framework (dimensions, thresholds, and the scoring rollup).

Pure by design — a set of labels or a measurement in, a level or a verdict out —
so a classification recommendation and a quality score are reproducible rather
than a matter of judgement, which is what makes a governance dashboard
defensible. The catalog, lineage, ownership, and sensitive-data registry are the
persisted surface built on top; they reuse the compliance records-of-processing
and the trust rating rather than restating either.
"""

from __future__ import annotations

"""Enterprise trust & risk management (Phase 8.4).

The deterministic core of the Trust Center: the certification-framework registry,
the risk-scoring framework (likelihood x impact -> level, inherent and residual),
and the posture aggregation that folds the existing security and compliance
signals plus the risk register into one defensible trust rating.

Everything here is pure — a snapshot in, a verdict out — so a trust rating is
reproducible rather than a guess, which is the whole point of a page a customer
is asked to rely on.
"""

from __future__ import annotations

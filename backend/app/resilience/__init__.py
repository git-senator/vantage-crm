"""Business continuity & operational resilience (Phase 8.6).

The deterministic core of the resilience layer: the vocabularies (criticality
tiers, incident severities and statuses, plan types), the recovery-objective
assessment (RTO/RPO target vs actual), and the readiness aggregation that folds
plan coverage, incident load, and recovery breaches into one operational
readiness rating.

Pure by design — targets and actuals in, a verdict out — so a readiness rating is
reproducible rather than a matter of who last looked at the dashboard. The
service and dependency registry, continuity plans, incidents, and post-incident
reviews are the persisted surface built on top; they reuse observability, tenant
health, the trust rating, and governance rather than restating any of them, and
they add no monitoring pipeline of their own.
"""

from __future__ import annotations

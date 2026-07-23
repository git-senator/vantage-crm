"""Reporting: the dataset registry, the query builder, and the exporters.

Split from `app/services/report.py` for the same reason `app/automation` is
split from its service — the registry and the builder are pure, importable and
testable without a session, and keeping them that way is what stops the
validation rules migrating into request handlers one convenience at a time.
"""

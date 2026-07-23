"""Analytics.

    metrics.py   the metric registry: what each number is, and how it aggregates

The `flow` versus `level` distinction in the registry is the load-bearing part —
summing a level across days produces a plausible, wrong number, so the kind is
declared once and enforced by the aggregation layer rather than remembered by
every caller.
"""

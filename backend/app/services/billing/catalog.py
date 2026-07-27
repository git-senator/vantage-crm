"""The default plan catalogue.

One definition, used two ways: the migration seeds these rows, and tests seed
the same rows through the ORM. Keeping the data here — not inline in the
migration — means the plan shape is reviewable in one place, and a quota or
feature the code checks for is visible next to the plan that grants it.

Quota semantics: a key that is present caps that resource; a key that is
**absent** (or null) is unlimited. So the enterprise plan lists no quotas at all
and the free plan lists tight ones, and the check is the same either way.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import Plan

#: Feature entitlements a plan may grant. The app gates optional capabilities on
#: these; a plan simply lists the ones it includes.
FEATURE_WEBHOOKS = "webhooks"
FEATURE_API_ACCESS = "api_access"
FEATURE_ADVANCED_REPORTS = "advanced_reports"
FEATURE_INTEGRATIONS = "integrations"
FEATURE_SSO = "sso"

#: Quota keys. A missing key means unlimited.
QUOTA_SEATS = "seats"
QUOTA_API_KEYS = "api_keys"
QUOTA_STORAGE_GB = "storage_gb"
QUOTA_AI_COST_USD = "monthly_ai_cost_usd"
QUOTA_API_CALLS = "monthly_api_calls"

PLAN_DEFS: list[dict[str, Any]] = [
    {
        "key": "free",
        "name": "Free",
        "description": "For evaluating Vantage CRM.",
        "price_cents": 0,
        "currency": "USD",
        "included_seats": 2,
        "price_per_seat_cents": 0,
        "features": {},
        "quotas": {
            QUOTA_SEATS: 2,
            QUOTA_API_KEYS: 1,
            QUOTA_STORAGE_GB: 1,
            QUOTA_AI_COST_USD: 5,
            QUOTA_API_CALLS: 1000,
        },
        "is_public": True,
    },
    {
        "key": "pro",
        "name": "Pro",
        "description": "For a growing brokerage.",
        "price_cents": 4900,
        "currency": "USD",
        "included_seats": 5,
        "price_per_seat_cents": 1500,
        "features": {
            FEATURE_WEBHOOKS: True,
            FEATURE_API_ACCESS: True,
            FEATURE_ADVANCED_REPORTS: True,
            FEATURE_INTEGRATIONS: True,
        },
        "quotas": {
            QUOTA_SEATS: 25,
            QUOTA_API_KEYS: 20,
            QUOTA_STORAGE_GB: 50,
            QUOTA_AI_COST_USD: 100,
            QUOTA_API_CALLS: 100_000,
        },
        "is_public": True,
    },
    {
        "key": "enterprise",
        "name": "Enterprise",
        "description": "Custom limits and SSO.",
        "price_cents": 0,
        "currency": "USD",
        "included_seats": 50,
        "price_per_seat_cents": 0,
        "features": {
            FEATURE_WEBHOOKS: True,
            FEATURE_API_ACCESS: True,
            FEATURE_ADVANCED_REPORTS: True,
            FEATURE_INTEGRATIONS: True,
            FEATURE_SSO: True,
        },
        "quotas": {},  # unlimited
        "is_public": False,
    },
]


async def seed_plans(session: AsyncSession) -> None:
    """Insert the default plans through the ORM. For tests and fixtures.

    The migration seeds the same rows in SQL; this is the ORM path so a test can
    stand the catalogue up without running migrations.
    """
    for definition in PLAN_DEFS:
        session.add(Plan(**definition))
    await session.flush()

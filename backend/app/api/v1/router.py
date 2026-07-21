"""Aggregate router for API v1.

One aggregation point so `main.py` does not accumulate a list of imports as the
surface grows.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, organizations

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(
    organizations.router, prefix="/organizations", tags=["organizations"]
)

# Phase 1.3: roles, permissions
# Phase 2:    leads, clients, properties, deals

"""Aggregate router for API v1.

Phase 1 mounts auth and leads here. Keeping one aggregation point means
`main.py` never grows a list of imports as the surface expands.
"""

from __future__ import annotations

from fastapi import APIRouter

api_router = APIRouter()

# Phase 1:
#   from app.api.v1 import auth, leads
#   api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
#   api_router.include_router(leads.router, prefix="/leads", tags=["leads"])

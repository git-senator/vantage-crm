"""Aggregate router for public API v1."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.public.v1 import (
    activities,
    clients,
    deals,
    leads,
    properties,
    tasks,
)

public_v1_router = APIRouter()

public_v1_router.include_router(leads.router)
public_v1_router.include_router(clients.router)
public_v1_router.include_router(properties.router)
public_v1_router.include_router(deals.router)
public_v1_router.include_router(activities.router)
public_v1_router.include_router(tasks.router)

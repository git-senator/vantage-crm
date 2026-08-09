"""What the sidebar dots are made of."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AttentionRead(BaseModel):
    """Counts of work waiting on the signed-in person, one per navigation item.

    Counts rather than booleans, even though the sidebar draws a plain dot: the
    number costs nothing extra to compute, and a caller that wants to show "3"
    later does not need the endpoint changed. Zero means the dot is dark — and
    also means "you may not see this section", which is the same answer as far
    as the sidebar is concerned.
    """

    messages: int = Field(ge=0)
    tasks: int = Field(ge=0)
    requests: int = Field(ge=0)
    notifications: int = Field(ge=0)

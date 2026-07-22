"""ORM models.

Every model must be imported here. Alembic autogenerate compares
`Base.metadata` against the live schema, and a model that is never imported is
absent from that metadata — autogenerate would then emit a migration that drops
its table.
"""

from app.models.activity import Activity
from app.models.attachment import Attachment
from app.models.audit import AuditLog
from app.models.calendar import CalendarEvent, EventAttendee
from app.models.client import Client
from app.models.conversation import Conversation, Message
from app.models.deal import Deal, DealStageHistory
from app.models.job import JobFailure
from app.models.lead import Lead
from app.models.note import Note
from app.models.notification import Notification, NotificationPreference
from app.models.organization import Organization
from app.models.pipeline import Pipeline, PipelineStage
from app.models.property import Property
from app.models.rbac import (
    Permission,
    Role,
    RolePermission,
    Team,
    TeamMember,
    UserRole,
)
from app.models.refresh_token import RefreshToken
from app.models.task import Task
from app.models.user import User

__all__ = [
    "Activity",
    "Attachment",
    "AuditLog",
    "CalendarEvent",
    "Client",
    "Conversation",
    "Deal",
    "DealStageHistory",
    "EventAttendee",
    "JobFailure",
    "Lead",
    "Message",
    "Note",
    "Notification",
    "NotificationPreference",
    "Organization",
    "Permission",
    "Pipeline",
    "PipelineStage",
    "Property",
    "RefreshToken",
    "Role",
    "RolePermission",
    "Task",
    "Team",
    "TeamMember",
    "User",
    "UserRole",
]

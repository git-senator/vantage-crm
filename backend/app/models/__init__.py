"""ORM models.

Every model must be imported here. Alembic autogenerate compares
`Base.metadata` against the live schema, and a model that is never imported is
absent from that metadata — autogenerate would then emit a migration that drops
its table.
"""

from app.models.activity import Activity
from app.models.ai import AiJob
from app.models.ai_conversation import AiConversation, AiMessage
from app.models.analytics import Goal, MetricSnapshot
from app.models.attachment import Attachment
from app.models.audit import AuditLog
from app.models.automation import (
    Workflow,
    WorkflowEvent,
    WorkflowRun,
    WorkflowRunStep,
    WorkflowVersion,
)
from app.models.calendar import CalendarEvent, EventAttendee
from app.models.client import Client
from app.models.conversation import Conversation, Message
from app.models.deal import Deal, DealStageHistory
from app.models.deal_score import DealScore
from app.models.growth_score import GrowthScore
from app.models.job import JobFailure
from app.models.lead import Lead
from app.models.lead_score import LeadScore
from app.models.mfa import MfaRecoveryCode
from app.models.note import Note
from app.models.notification import Notification, NotificationPreference
from app.models.organization import Organization
from app.models.pipeline import Pipeline, PipelineStage
from app.models.property import Property
from app.models.property_score import PropertyScore
from app.models.rbac import (
    Permission,
    Role,
    RolePermission,
    Team,
    TeamMember,
    UserRole,
)
from app.models.refresh_token import RefreshToken
from app.models.report import ReportDefinition, ReportRun
from app.models.task import Task
from app.models.user import User

__all__ = [
    "Activity",
    "AiConversation",
    "AiJob",
    "AiMessage",
    "Attachment",
    "AuditLog",
    "CalendarEvent",
    "Client",
    "Conversation",
    "Deal",
    "DealScore",
    "DealStageHistory",
    "EventAttendee",
    "Goal",
    "GrowthScore",
    "JobFailure",
    "Lead",
    "LeadScore",
    "Message",
    "MetricSnapshot",
    "MfaRecoveryCode",
    "Note",
    "Notification",
    "NotificationPreference",
    "Organization",
    "Permission",
    "Pipeline",
    "PipelineStage",
    "Property",
    "PropertyScore",
    "RefreshToken",
    "ReportDefinition",
    "ReportRun",
    "Role",
    "RolePermission",
    "Task",
    "Team",
    "TeamMember",
    "User",
    "UserRole",
    "Workflow",
    "WorkflowEvent",
    "WorkflowRun",
    "WorkflowRunStep",
    "WorkflowVersion",
]

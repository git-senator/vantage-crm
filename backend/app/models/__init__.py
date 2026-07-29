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
from app.models.api_key import ApiKey
from app.models.attachment import Attachment
from app.models.audit import AuditLog
from app.models.automation import (
    Workflow,
    WorkflowEvent,
    WorkflowRun,
    WorkflowRunStep,
    WorkflowVersion,
)
from app.models.billing import Invoice, Plan, Subscription
from app.models.calendar import CalendarEvent, EventAttendee
from app.models.client import Client
from app.models.compliance_ops import ComplianceEvidence, DataProcessingActivity
from app.models.conversation import Conversation, Message
from app.models.deal import Deal, DealStageHistory
from app.models.deal_score import DealScore
from app.models.developer import (
    ApplicationVersionReview,
    DeveloperApiCredential,
    DeveloperOrganization,
    MarketplaceApplication,
)
from app.models.enterprise import (
    CompliancePolicy,
    DataRequest,
    FeatureFlag,
    OrganizationBranding,
    SecurityPolicy,
    SsoConnection,
)
from app.models.governance import (
    DataAsset,
    DataLineageEdge,
    DataQualityRule,
)
from app.models.growth_score import GrowthScore
from app.models.integration import (
    IntegrationConnection,
    IntegrationSubscription,
    IntegrationSyncRun,
)
from app.models.job import JobFailure
from app.models.lead import Lead
from app.models.lead_score import LeadScore
from app.models.marketplace import (
    IntegrationEntitlement,
    IntegrationInstallation,
    IntegrationListing,
    IntegrationPlan,
    IntegrationReview,
    IntegrationVersion,
    RevenueEvent,
    UsageRecord,
)
from app.models.marketplace_sdk import (
    MarketplaceApiAccessGrant,
    MarketplaceEventSubscription,
    MarketplaceSdkApplication,
)
from app.models.mfa import MfaRecoveryCode
from app.models.note import Note
from app.models.notification import Notification, NotificationPreference
from app.models.organization import Organization
from app.models.pipeline import Pipeline, PipelineStage
from app.models.plugin import (
    Plugin,
    PluginEventSubscription,
    PluginInstallation,
)
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
from app.models.resilience import (
    BusinessService,
    ContinuityPlan,
    OperationalIncident,
    PostIncidentReview,
    ServiceDependency,
)
from app.models.security_ops import SecurityAlert, SecurityEvent, TrustedDevice
from app.models.task import Task
from app.models.trust import (
    Certification,
    QuestionnaireItem,
    Risk,
    TrustProfile,
)
from app.models.user import User
from app.models.webhook import WebhookDelivery, WebhookEndpoint

__all__ = [
    "Activity",
    "AiConversation",
    "AiJob",
    "AiMessage",
    "ApiKey",
    "ApplicationVersionReview",
    "Attachment",
    "AuditLog",
    "BusinessService",
    "CalendarEvent",
    "Certification",
    "Client",
    "ComplianceEvidence",
    "CompliancePolicy",
    "ContinuityPlan",
    "Conversation",
    "DataAsset",
    "DataLineageEdge",
    "DataProcessingActivity",
    "DataQualityRule",
    "DataRequest",
    "Deal",
    "DealScore",
    "DealStageHistory",
    "DeveloperApiCredential",
    "DeveloperOrganization",
    "EventAttendee",
    "FeatureFlag",
    "Goal",
    "GrowthScore",
    "IntegrationConnection",
    "IntegrationEntitlement",
    "IntegrationInstallation",
    "IntegrationListing",
    "IntegrationPlan",
    "IntegrationReview",
    "IntegrationSubscription",
    "IntegrationSyncRun",
    "IntegrationVersion",
    "Invoice",
    "JobFailure",
    "Lead",
    "LeadScore",
    "MarketplaceApiAccessGrant",
    "MarketplaceApplication",
    "MarketplaceEventSubscription",
    "MarketplaceSdkApplication",
    "Message",
    "MetricSnapshot",
    "MfaRecoveryCode",
    "Note",
    "Notification",
    "NotificationPreference",
    "OperationalIncident",
    "Organization",
    "OrganizationBranding",
    "Permission",
    "Pipeline",
    "PipelineStage",
    "Plan",
    "Plugin",
    "PluginEventSubscription",
    "PluginInstallation",
    "PostIncidentReview",
    "Property",
    "PropertyScore",
    "QuestionnaireItem",
    "RefreshToken",
    "ReportDefinition",
    "ReportRun",
    "RevenueEvent",
    "Risk",
    "Role",
    "RolePermission",
    "SecurityAlert",
    "SecurityEvent",
    "SecurityPolicy",
    "ServiceDependency",
    "SsoConnection",
    "Subscription",
    "Task",
    "Team",
    "TeamMember",
    "TrustProfile",
    "TrustedDevice",
    "UsageRecord",
    "User",
    "UserRole",
    "WebhookDelivery",
    "WebhookEndpoint",
    "Workflow",
    "WorkflowEvent",
    "WorkflowRun",
    "WorkflowRunStep",
    "WorkflowVersion",
]

"""Security operations services.

The orchestration around the deterministic security-ops core. It records events,
tracks devices, scores logins, runs the detection framework, and raises and
manages alerts — reusing the audit log, the notification centre, and the metrics
registry. It never authenticates a user or rewrites a security module; it
observes and reacts.

Everything is gated on `settings.manage` — the security surface is
administrative — and scoped to the caller's tenant under RLS.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError
from app.core.logging import get_logger
from app.models.security_ops import SecurityAlert, SecurityEvent, TrustedDevice
from app.models.user import User
from app.observability import metrics
from app.repositories.security_ops import (
    SecurityAlertRepository,
    SecurityEventRepository,
    TrustedDeviceRepository,
)
from app.schemas.common import Cursor
from app.schemas.security_ops import (
    GateDecisionRead,
    LoginRiskRequest,
    LoginRiskResult,
    RiskAssessmentRead,
    SecurityAlertRead,
    SecurityDashboard,
    SecurityEventRead,
    TrustedDeviceRead,
)
from app.security_ops.detectors import AlertProposal, DetectionContext, run_detectors
from app.security_ops.registry import event_type, max_severity
from app.security_ops.risk import (
    RiskSignals,
    derive_fingerprint,
    login_gate,
    score_login,
)
from app.services.audit import AuditService
from app.services.rbac import AuthorizationContext

logger = get_logger(__name__)

MANAGE_PERMISSION = "settings.manage"
_HIGH_SEVERITIES = ("high", "critical")


# =========================================================== events


class SecurityEventService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.repo = SecurityEventRepository(session)

    async def record(
        self,
        *,
        event_type_key: str,
        user_id: UUID | None = None,
        source_ip: str | None = None,
        user_agent: str | None = None,
        device_fingerprint: str | None = None,
        severity: str | None = None,
        risk_score: int = 0,
        details: dict[str, Any] | None = None,
    ) -> SecurityEvent:
        """Append one security event. Validates the type against the registry,
        so an unknown type is a 400 rather than an un-queryable row."""
        self.auth.require(MANAGE_PERMISSION)
        try:
            registered = event_type(event_type_key)
        except KeyError as exc:
            raise AppError(str(exc)) from exc

        chosen_severity = severity or registered.default_severity
        event = SecurityEvent(
            organization_id=self.auth.organization_id,
            user_id=user_id,
            event_type=event_type_key,
            category=registered.category,
            severity=chosen_severity,
            source_ip=source_ip,
            user_agent=user_agent[:400] if user_agent else None,
            device_fingerprint=device_fingerprint,
            risk_score=risk_score,
            details=details or {},
        )
        self.session.add(event)
        await self.session.flush()
        metrics.record_security_event(
            event_type=event_type_key,
            severity=chosen_severity,
            organization_id=self.auth.organization_id,
        )
        return event

    async def list_events(
        self,
        *,
        limit: int,
        cursor: Cursor | None,
        event_type_filter: str | None = None,
        severity: str | None = None,
        user_id: UUID | None = None,
    ) -> tuple[list[SecurityEventRead], bool]:
        self.auth.require(MANAGE_PERMISSION)
        rows, has_more = await self.repo.list_for_org(
            self.auth.organization_id,
            limit=limit,
            cursor=cursor,
            event_type=event_type_filter,
            severity=severity,
            user_id=user_id,
        )
        return [_event_read(row) for row in rows], has_more


def _event_read(row: SecurityEvent) -> SecurityEventRead:
    return SecurityEventRead(
        id=row.id,
        event_type=row.event_type,
        category=row.category,
        severity=row.severity,
        user_id=row.user_id,
        source_ip=row.source_ip,
        user_agent=row.user_agent,
        device_fingerprint=row.device_fingerprint,
        risk_score=row.risk_score,
        details=dict(row.details),
        created_at=row.created_at,
    )


# =========================================================== devices


class DeviceIntelligenceService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.repo = TrustedDeviceRepository(session)
        self.audit = AuditService(session)

    async def observe(
        self,
        *,
        user_id: UUID,
        fingerprint: str,
        source_ip: str | None,
        country: str | None,
        user_agent: str | None,
    ) -> tuple[TrustedDevice, bool, bool]:
        """Record a device sighting. Returns (device, is_new_device, is_new_location).

        The first sighting of a fingerprint for an account is a new device; a
        sighting from a different country than the device last reported is a new
        location. The comparison happens before the row is updated, so "new
        location" reflects the change, not the value just written.
        """
        now = datetime.now(UTC)
        device = await self.repo.get_for_fingerprint(
            self.auth.organization_id, user_id, fingerprint
        )
        if device is None:
            device = TrustedDevice(
                organization_id=self.auth.organization_id,
                user_id=user_id,
                device_fingerprint=fingerprint,
                user_agent=user_agent[:400] if user_agent else None,
                last_ip=source_ip,
                last_country=country,
                first_seen_at=now,
                last_seen_at=now,
            )
            self.session.add(device)
            await self.session.flush()
            return device, True, False

        new_location = bool(
            country and device.last_country and device.last_country != country
        )
        device.last_seen_at = now
        if source_ip:
            device.last_ip = source_ip
        if country:
            device.last_country = country
        if user_agent:
            device.user_agent = user_agent[:400]
        await self.session.flush()
        return device, False, new_location

    async def list_devices(
        self, *, user_id: UUID | None = None
    ) -> list[TrustedDeviceRead]:
        self.auth.require(MANAGE_PERMISSION)
        rows = await self.repo.list_for_org(
            self.auth.organization_id, user_id=user_id
        )
        return [_device_read(row) for row in rows]

    async def set_trust(
        self, actor: User, device_id: UUID, *, trusted: bool, label: str | None
    ) -> TrustedDeviceRead:
        self.auth.require(MANAGE_PERMISSION)
        device = await self.repo.get(device_id, self.auth.organization_id)
        if device is None:
            raise NotFoundError("Device not found.")
        device.trusted = trusted
        if label is not None:
            device.label = label
        await self.session.flush()
        await self.audit.record(
            action=(
                AuditAction.SECURITY_DEVICE_TRUSTED
                if trusted
                else AuditAction.SECURITY_DEVICE_UNTRUSTED
            ),
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="trusted_device",
            entity_id=device.id,
            metadata={"trusted": trusted, "user_id": str(device.user_id)},
        )
        return _device_read(device)


def _device_read(row: TrustedDevice) -> TrustedDeviceRead:
    return TrustedDeviceRead(
        id=row.id,
        user_id=row.user_id,
        device_fingerprint=row.device_fingerprint,
        label=row.label,
        user_agent=row.user_agent,
        last_ip=row.last_ip,
        last_country=row.last_country,
        trusted=row.trusted,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
    )


# =========================================================== alerts


class SecurityAlertService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.repo = SecurityAlertRepository(session)
        self.audit = AuditService(session)

    async def raise_from_proposal(
        self,
        proposal: AlertProposal,
        *,
        subject_user_id: UUID | None,
        source_ip: str | None,
        risk_score: int,
    ) -> SecurityAlert:
        """Raise a detection as an alert, folding a repeat into the open one.

        A repeated detection of the same thing increments the open alert's count
        and refreshes it rather than creating a duplicate; a new finding creates
        a fresh alert, audits it, and notifies the affected account on a
        high-severity finding.
        """
        now = datetime.now(UTC)
        existing = await self.repo.find_open_by_dedup(
            self.auth.organization_id, proposal.dedup_key
        )
        if existing is not None:
            existing.occurrences += 1
            existing.last_seen_at = now
            existing.severity = max_severity(existing.severity, proposal.severity)
            existing.risk_score = max(existing.risk_score, risk_score)
            await self.session.flush()
            return existing

        alert = SecurityAlert(
            organization_id=self.auth.organization_id,
            category=proposal.category,
            event_type=proposal.event_type,
            severity=proposal.severity,
            status="open",
            title=proposal.title,
            description=proposal.description,
            subject_user_id=subject_user_id,
            source_ip=source_ip,
            risk_score=risk_score,
            dedup_key=proposal.dedup_key,
            details=dict(proposal.details),
            first_seen_at=now,
            last_seen_at=now,
        )
        self.session.add(alert)
        await self.session.flush()

        metrics.record_security_alert(
            severity=proposal.severity, organization_id=self.auth.organization_id
        )
        await self.audit.record(
            action=AuditAction.SECURITY_ALERT_RAISED,
            organization_id=self.auth.organization_id,
            actor_id=None,
            actor_email="security",
            entity_type="security_alert",
            entity_id=alert.id,
            metadata={"event_type": proposal.event_type, "severity": proposal.severity},
        )
        if proposal.severity in _HIGH_SEVERITIES:
            await self._notify_subject(alert)
        logger.warning(
            "security_alert_raised",
            extra={"alert_id": str(alert.id), "severity": proposal.severity},
        )
        return alert

    async def list_alerts(
        self,
        *,
        limit: int,
        cursor: Cursor | None,
        status: str | None = None,
        severity: str | None = None,
    ) -> tuple[list[SecurityAlertRead], bool]:
        self.auth.require(MANAGE_PERMISSION)
        rows, has_more = await self.repo.list_for_org(
            self.auth.organization_id,
            limit=limit,
            cursor=cursor,
            status=status,
            severity=severity,
        )
        return [_alert_read(row) for row in rows], has_more

    async def acknowledge(self, actor: User, alert_id: UUID) -> SecurityAlertRead:
        alert = await self._transition(
            actor, alert_id, status="acknowledged",
            action=AuditAction.SECURITY_ALERT_ACKNOWLEDGED,
        )
        return _alert_read(alert)

    async def resolve(
        self, actor: User, alert_id: UUID, *, dismissed: bool = False
    ) -> SecurityAlertRead:
        alert = await self._transition(
            actor,
            alert_id,
            status="dismissed" if dismissed else "resolved",
            action=AuditAction.SECURITY_ALERT_RESOLVED,
        )
        return _alert_read(alert)

    async def _transition(
        self, actor: User, alert_id: UUID, *, status: str, action: str
    ) -> SecurityAlert:
        self.auth.require(MANAGE_PERMISSION)
        alert = await self.repo.get(alert_id, self.auth.organization_id)
        if alert is None:
            raise NotFoundError("Alert not found.")
        now = datetime.now(UTC)
        alert.status = status
        if status == "acknowledged":
            alert.acknowledged_by = actor.id
            alert.acknowledged_at = now
        else:
            alert.resolved_by = actor.id
            alert.resolved_at = now
        await self.session.flush()
        await self.audit.record(
            action=action,
            organization_id=self.auth.organization_id,
            actor_id=actor.id,
            actor_email=actor.email,
            entity_type="security_alert",
            entity_id=alert.id,
            metadata={"status": status},
        )
        return alert

    async def _notify_subject(self, alert: SecurityAlert) -> None:
        if alert.subject_user_id is None:
            return
        from app.services.notification_center import NotificationCenter

        await NotificationCenter(self.session).raise_notification(
            organization_id=self.auth.organization_id,
            recipient_id=alert.subject_user_id,
            category="system",
            type="security.alert",
            title=alert.title,
            body=alert.description,
            entity_type="security_alert",
            entity_id=alert.id,
        )


def _alert_read(row: SecurityAlert) -> SecurityAlertRead:
    return SecurityAlertRead(
        id=row.id,
        category=row.category,
        event_type=row.event_type,
        severity=row.severity,
        status=row.status,
        title=row.title,
        description=row.description,
        subject_user_id=row.subject_user_id,
        source_ip=row.source_ip,
        risk_score=row.risk_score,
        occurrences=row.occurrences,
        details=dict(row.details),
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        acknowledged_at=row.acknowledged_at,
        resolved_at=row.resolved_at,
        created_at=row.created_at,
    )


# =============================================== orchestration


class SecurityOpsService:
    """Ties the pieces together for the two entry points the auth BFF calls:
    ingesting a discrete security event, and scoring a sign-in end to end."""

    def __init__(
        self, session: AsyncSession, auth: AuthorizationContext, settings: Settings
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings
        self.events = SecurityEventService(session, auth)
        self.devices = DeviceIntelligenceService(session, auth)
        self.alerts = SecurityAlertService(session, auth)

    async def evaluate_login(self, request: LoginRiskRequest) -> LoginRiskResult:
        """Score a sign-in, record it, track the device, and raise any alerts."""
        self.auth.require(MANAGE_PERMISSION)
        now = datetime.now(UTC)
        fingerprint = derive_fingerprint(
            user_agent=request.user_agent, client_hint=request.client_hint
        )

        new_device = False
        new_location = False
        if request.user_id is not None:
            _device, new_device, new_location = await self.devices.observe(
                user_id=request.user_id,
                fingerprint=fingerprint,
                source_ip=request.source_ip,
                country=request.country,
                user_agent=request.user_agent,
            )

        window = timedelta(minutes=self.settings.SECURITY_FAILED_LOGIN_WINDOW_MINUTES)
        recent_failed = await self.events.repo.count_recent_failed_logins(
            self.auth.organization_id, request.user_id, since=now - window
        )
        effective_failed = recent_failed + (0 if request.success else 1)

        signals = RiskSignals(
            new_device=new_device,
            new_location=new_location,
            failed_attempts=effective_failed,
            mfa_satisfied=request.mfa_satisfied,
            off_hours=request.off_hours,
            impossible_travel=request.impossible_travel,
        )
        assessment = score_login(signals)

        key = "login.succeeded" if request.success else "login.failed"
        severity = max_severity(event_type(key).default_severity, assessment.level)
        event = await self.events.record(
            event_type_key=key,
            user_id=request.user_id,
            source_ip=request.source_ip,
            user_agent=request.user_agent,
            device_fingerprint=fingerprint,
            severity=severity,
            risk_score=assessment.score,
            details={
                "reasons": assessment.reasons,
                "new_device": new_device,
                "new_location": new_location,
                "country": request.country,
            },
        )

        context = DetectionContext(
            organization_id=self.auth.organization_id,
            event_type=key,
            subject_user_id=request.user_id,
            source_ip=request.source_ip,
            risk_score=assessment.score,
            risk_level=assessment.level,
            new_device=new_device,
            new_location=new_location,
            recent_failed_logins=effective_failed,
            impossible_travel=request.impossible_travel,
            brute_force_threshold=self.settings.SECURITY_BRUTE_FORCE_THRESHOLD,
        )
        proposals = run_detectors(context)
        for proposal in proposals:
            await self.alerts.raise_from_proposal(
                proposal,
                subject_user_id=request.user_id,
                source_ip=request.source_ip,
                risk_score=assessment.score,
            )

        gate = login_gate(
            ip_allowed=request.ip_allowed,
            mfa_satisfied=request.mfa_satisfied,
            assessment=assessment,
        )
        return LoginRiskResult(
            assessment=RiskAssessmentRead(
                score=assessment.score, level=assessment.level, reasons=assessment.reasons
            ),
            gate=GateDecisionRead(
                allow=gate.allow, require_mfa=gate.require_mfa, reason=gate.reason
            ),
            event_id=event.id,
            new_device=new_device,
            alerts_raised=len(proposals),
        )


# =============================================== dashboard


class SecurityDashboardService:
    def __init__(self, session: AsyncSession, auth: AuthorizationContext) -> None:
        self.session = session
        self.auth = auth
        self.events = SecurityEventRepository(session)
        self.alerts = SecurityAlertRepository(session)
        self.devices = TrustedDeviceRepository(session)

    async def dashboard(self, *, now: datetime | None = None) -> SecurityDashboard:
        self.auth.require(MANAGE_PERMISSION)
        moment = now or datetime.now(UTC)
        org = self.auth.organization_id
        since = moment - timedelta(days=7)

        alerts_by_status = await self.alerts.count_by_status(org)
        events_by_severity = await self.events.count_by_severity(org, since=since)
        recent_rows, _ = await self.events.list_for_org(
            org, limit=10, severity="high"
        )
        devices = list(await self.devices.list_for_org(org))
        trusted = sum(1 for d in devices if d.trusted)

        open_alerts = alerts_by_status.get("open", 0)
        posture = "attention" if open_alerts else "healthy"
        return SecurityDashboard(
            open_alerts=open_alerts,
            alerts_by_status=alerts_by_status,
            events_by_severity=events_by_severity,
            recent_high_severity=[_event_read(row) for row in recent_rows],
            trusted_devices=trusted,
            untrusted_devices=len(devices) - trusted,
            posture=posture,
        )


__all__ = [
    "MANAGE_PERMISSION",
    "DeviceIntelligenceService",
    "SecurityAlertService",
    "SecurityDashboardService",
    "SecurityEventService",
    "SecurityOpsService",
]

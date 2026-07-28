"""Canonical audit action names.

Constants rather than free strings so a typo becomes an import error instead of
an entry nobody can find later. Grouped by the question an auditor asks.
"""

from __future__ import annotations

from typing import Final


class AuditAction:
    # --- authentication ---
    LOGIN_SUCCEEDED: Final = "auth.login.succeeded"
    LOGIN_FAILED: Final = "auth.login.failed"
    LOGOUT: Final = "auth.logout"
    PASSWORD_CHANGED: Final = "auth.password.changed"
    TOKEN_REFRESHED: Final = "auth.token.refreshed"
    #: Token theft or a client bug. Alerting hooks onto this.
    TOKEN_REUSE_DETECTED: Final = "auth.token.reuse_detected"
    ACCOUNT_LOCKED: Final = "auth.account.locked"
    #: MFA lifecycle. Enrolment and removal are both security-relevant: the
    #: second is how an attacker with a session would weaken an account they
    #: have already compromised.
    MFA_ENABLED: Final = "auth.mfa.enabled"
    MFA_DISABLED: Final = "auth.mfa.disabled"
    MFA_CHALLENGE_FAILED: Final = "auth.mfa.challenge_failed"
    #: A recovery code was spent. Someone losing their phone looks exactly
    #: like someone else using a stolen code, so this is worth alerting on.
    MFA_RECOVERY_USED: Final = "auth.mfa.recovery_used"

    # --- users ---
    USER_CREATED: Final = "user.created"
    USER_UPDATED: Final = "user.updated"
    USER_DEACTIVATED: Final = "user.deactivated"

    # --- authorization ---
    ROLE_ASSIGNED: Final = "role.assigned"
    ROLE_REVOKED: Final = "role.revoked"
    PERMISSION_DENIED: Final = "authz.denied"

    # --- API keys (Phase 7.1) ---
    #: Minting a machine credential that carries a subset of someone's authority
    #: is privilege-granting, so creation and rotation are both alert-worthy.
    #: Rotation invalidates the previous secret, so it is also how a suspected
    #: leak is remediated — worth being able to find.
    API_KEY_CREATED: Final = "api_key.created"
    API_KEY_ROTATED: Final = "api_key.rotated"
    API_KEY_REVOKED: Final = "api_key.revoked"

    # --- webhooks (Phase 7.3) ---
    #: A webhook endpoint receives a copy of a tenant's events at an external
    #: URL, so creating or re-pointing one is an egress decision worth recording;
    #: rotating its secret is how a suspected leak of the signing key is
    #: remediated.
    WEBHOOK_CREATED: Final = "webhook.created"
    WEBHOOK_UPDATED: Final = "webhook.updated"
    WEBHOOK_SECRET_ROTATED: Final = "webhook.secret_rotated"
    WEBHOOK_DELETED: Final = "webhook.deleted"

    # --- billing (Phase 7.5) ---
    #: Subscribing, changing plan, changing seats, and cancelling all change what
    #: a tenant pays and what it is entitled to, so each is an auditable act.
    #: Invoices are recorded (from the provider or written directly) rather than
    #: "created" by a user — the action name says so.
    SUBSCRIPTION_CREATED: Final = "billing.subscription.created"
    SUBSCRIPTION_UPDATED: Final = "billing.subscription.updated"
    SUBSCRIPTION_CANCELED: Final = "billing.subscription.canceled"
    INVOICE_RECORDED: Final = "billing.invoice.recorded"

    # --- integrations (Phase 7.7) ---
    #: Connecting a tenant's workspace to an external service, and disconnecting
    #: it, both move data across a trust boundary, so each is an auditable act.
    #: A token refresh is recorded because it is the moment a credential is
    #: re-minted; an auto-disable is recorded because it is a security-relevant
    #: state change nobody explicitly asked for.
    INTEGRATION_CONNECTED: Final = "integration.connected"
    INTEGRATION_DISCONNECTED: Final = "integration.disconnected"
    INTEGRATION_UPDATED: Final = "integration.updated"
    INTEGRATION_TOKEN_REFRESHED: Final = "integration.token_refreshed"
    INTEGRATION_DISABLED: Final = "integration.disabled"

    # --- security operations (Phase 8.2) ---
    #: A detection raised or updated an alert; acknowledging and resolving are the
    #: analyst's response, worth recording so the security timeline is complete.
    #: Trusting a device is an authorization-adjacent decision by the account
    #: owner. Alert-raising is high severity — it is the signal an analyst acts on.
    SECURITY_ALERT_RAISED: Final = "security.alert.raised"
    SECURITY_ALERT_ACKNOWLEDGED: Final = "security.alert.acknowledged"
    SECURITY_ALERT_RESOLVED: Final = "security.alert.resolved"
    SECURITY_DEVICE_TRUSTED: Final = "security.device.trusted"
    SECURITY_DEVICE_UNTRUSTED: Final = "security.device.untrusted"

    # --- compliance operations (Phase 8.3) ---
    #: Recording a processing activity or collecting evidence is itself part of
    #: the compliance record a regulator later inspects, so each is auditable.
    #: Running retention execution deletes data on demand — worth recording who
    #: triggered it and when.
    PROCESSING_ACTIVITY_RECORDED: Final = "compliance.processing_activity.recorded"
    PROCESSING_ACTIVITY_UPDATED: Final = "compliance.processing_activity.updated"
    PROCESSING_ACTIVITY_DELETED: Final = "compliance.processing_activity.deleted"
    COMPLIANCE_EVIDENCE_COLLECTED: Final = "compliance.evidence.collected"
    RETENTION_EXECUTED: Final = "compliance.retention.executed"

    # --- trust & risk management (Phase 8.4) ---
    #: A risk-register entry is part of the security record an auditor inspects,
    #: so recording, re-scoring, and closing one are auditable. A certification's
    #: lifecycle is likewise evidence. Publishing the Trust Center exposes the
    #: profile beyond the workspace — an egress decision worth a same-day look, so
    #: it is high severity.
    RISK_RECORDED: Final = "trust.risk.recorded"
    RISK_UPDATED: Final = "trust.risk.updated"
    RISK_DELETED: Final = "trust.risk.deleted"
    CERTIFICATION_RECORDED: Final = "trust.certification.recorded"
    CERTIFICATION_UPDATED: Final = "trust.certification.updated"
    CERTIFICATION_DELETED: Final = "trust.certification.deleted"
    TRUST_PROFILE_UPDATED: Final = "trust.profile.updated"
    TRUST_PROFILE_PUBLISHED: Final = "trust.profile.published"
    QUESTIONNAIRE_ITEM_RECORDED: Final = "trust.questionnaire.recorded"
    QUESTIONNAIRE_ITEM_UPDATED: Final = "trust.questionnaire.updated"
    QUESTIONNAIRE_ITEM_DELETED: Final = "trust.questionnaire.deleted"

    # --- data governance (Phase 8.5) ---
    #: Cataloguing a data asset, and changing its classification, ownership, or
    #: labels, are the acts a data-protection review inspects — each is auditable.
    #: Reclassifying an asset downward, in particular, is a decision worth being
    #: able to find. Quality rules and lineage edges are governance metadata whose
    #: creation and removal are likewise recorded.
    DATA_ASSET_REGISTERED: Final = "governance.asset.registered"
    DATA_ASSET_UPDATED: Final = "governance.asset.updated"
    DATA_ASSET_DELETED: Final = "governance.asset.deleted"
    DATA_OWNER_ASSIGNED: Final = "governance.asset.owner_assigned"
    QUALITY_RULE_RECORDED: Final = "governance.quality_rule.recorded"
    QUALITY_RULE_UPDATED: Final = "governance.quality_rule.updated"
    QUALITY_RULE_DELETED: Final = "governance.quality_rule.deleted"
    LINEAGE_RECORDED: Final = "governance.lineage.recorded"
    LINEAGE_DELETED: Final = "governance.lineage.deleted"

    # --- organization ---
    ORGANIZATION_UPDATED: Final = "organization.updated"
    SETTINGS_CHANGED: Final = "organization.settings.changed"

    # --- enterprise governance (Phase 8.0) ---
    #: Changing the security policy (password rules, MFA enforcement, session
    #: limits, the IP allowlist) alters who can get in and how; revoking every
    #: session and reconfiguring SSO are both account-takeover-adjacent, so each
    #: is alert-worthy. Compliance acts — placing a legal hold, running a GDPR
    #: export or erasure — are recorded because they are exactly the acts a
    #: regulator later asks to see evidence of.
    SECURITY_POLICY_UPDATED: Final = "enterprise.security_policy.updated"
    SESSIONS_REVOKED: Final = "enterprise.sessions.revoked"
    BRANDING_UPDATED: Final = "enterprise.branding.updated"
    SSO_UPDATED: Final = "enterprise.sso.updated"
    FEATURE_FLAG_UPDATED: Final = "enterprise.feature_flag.updated"
    COMPLIANCE_POLICY_UPDATED: Final = "enterprise.compliance_policy.updated"
    LEGAL_HOLD_CHANGED: Final = "enterprise.legal_hold.changed"
    DATA_REQUEST_CREATED: Final = "enterprise.data_request.created"
    DATA_REQUEST_COMPLETED: Final = "enterprise.data_request.completed"
    SCIM_USER_PROVISIONED: Final = "enterprise.scim.user_provisioned"
    SCIM_USER_DEACTIVATED: Final = "enterprise.scim.user_deactivated"

    # --- data (Phase 2 onward) ---
    RECORD_CREATED: Final = "record.created"
    RECORD_UPDATED: Final = "record.updated"
    RECORD_DELETED: Final = "record.deleted"
    RECORD_EXPORTED: Final = "record.exported"
    #: A lead became a client. Distinct from record.updated because it is the
    #: funnel event Phase 4 conversion reporting counts.
    RECORD_CONVERTED: Final = "record.converted"
    #: A deal moved between pipeline stages. Distinct from record.updated for
    #: the same reason: it is the event velocity and cycle-time analytics
    #: count, and burying it in a generic field diff makes it unqueryable.
    RECORD_STAGE_CHANGED: Final = "record.stage_changed"
    #: A task was finished. Distinct from record.updated because "what got done
    #: this week" is a question worth being able to ask directly.
    RECORD_COMPLETED: Final = "record.completed"

    # --- documents (Phase 3) ---
    #: A file's bytes arrived and passed verification. Distinct from
    #: record.created, which fires at registration — the gap between the two is
    #: where an abandoned or rejected upload lives, and it is worth being able
    #: to query for it.
    DOCUMENT_UPLOADED: Final = "document.uploaded"
    #: A download URL was minted. The signed URL is a bearer credential, so the
    #: moment it is issued is the moment access is granted; recording the later
    #: fetch is impossible, because the fetch never reaches this application.
    DOCUMENT_DOWNLOADED: Final = "document.downloaded"
    #: The bytes contradicted the declared type, exceeded the ceiling, or the
    #: client's checksum did not match what was stored.
    DOCUMENT_REJECTED: Final = "document.rejected"
    #: A malware scan flagged the file. It is never served again.
    DOCUMENT_QUARANTINED: Final = "document.quarantined"

    # --- messaging (Phase 3.4) ---
    #: Someone sent a message to a contact from inside the CRM. Recorded at
    #: queue time, not delivery: the intent to contact a customer is the
    #: auditable act, and whether the provider accepted it is a status on the
    #: message itself.
    MESSAGE_SENT: Final = "message.sent"

    # --- automation (Phase 4) ---
    #: Publishing is the moment a workflow starts acting on live customer data,
    #: so it is the auditable act — editing a draft is not.
    WORKFLOW_PUBLISHED: Final = "workflow.published"
    WORKFLOW_ENABLED: Final = "workflow.enabled"
    WORKFLOW_DISABLED: Final = "workflow.disabled"

    # --- AI (Phase 6) ---
    #: A completion was dispatched to a model provider. The auditable act is the
    #: egress — customer data left for a third party — not the answer that came
    #: back, so it is recorded whether or not the call succeeded.
    AI_COMPLETION: Final = "ai.completion"
    #: A tenant's cost ceiling refused a call before dispatch. Worth alerting on:
    #: a workspace that keeps hitting its ceiling is either mis-budgeted or being
    #: driven harder than anyone intended.
    AI_BUDGET_EXCEEDED: Final = "ai.budget_exceeded"


#: Actions that warrant alerting rather than just recording.
HIGH_SEVERITY_ACTIONS: frozenset[str] = frozenset(
    {
        AuditAction.TOKEN_REUSE_DETECTED,
        AuditAction.ROLE_ASSIGNED,
        AuditAction.ROLE_REVOKED,
        AuditAction.ACCOUNT_LOCKED,
        # A machine credential carrying a slice of someone's authority.
        AuditAction.API_KEY_CREATED,
        AuditAction.API_KEY_ROTATED,
        # A webhook endpoint sends a tenant's events to an external URL; a new
        # one, or a re-pointed one, is an egress change worth a same-day look.
        AuditAction.WEBHOOK_CREATED,
        AuditAction.WEBHOOK_SECRET_ROTATED,
        # A cancellation ends a paying relationship — worth a same-day look, in
        # case it was not the customer's intent.
        AuditAction.SUBSCRIPTION_CANCELED,
        AuditAction.MFA_DISABLED,
        AuditAction.MFA_RECOVERY_USED,
        # Connecting or disconnecting an external service moves a tenant's data
        # across a trust boundary — an egress change worth a same-day look.
        AuditAction.INTEGRATION_CONNECTED,
        AuditAction.INTEGRATION_DISCONNECTED,
        # Enterprise controls that change who can get in, or that erase data.
        AuditAction.SECURITY_POLICY_UPDATED,
        AuditAction.SESSIONS_REVOKED,
        AuditAction.SSO_UPDATED,
        AuditAction.LEGAL_HOLD_CHANGED,
        AuditAction.DATA_REQUEST_COMPLETED,
        # A raised security alert is the signal an analyst acts on the same day.
        AuditAction.SECURITY_ALERT_RAISED,
        # Publishing the Trust Center exposes the tenant's security profile
        # beyond the workspace — an egress decision worth a same-day look.
        AuditAction.TRUST_PROFILE_PUBLISHED,
        # A newly live workflow can touch every record in the workspace.
        AuditAction.WORKFLOW_PUBLISHED,
        AuditAction.RECORD_EXPORTED,
        # A quarantine means malware reached the bucket. Somebody should hear
        # about it the same day, not at the next audit review.
        AuditAction.DOCUMENT_QUARANTINED,
        # Repeatedly hitting the AI cost ceiling is either a mis-budget or abuse,
        # and either way is a same-day question rather than a monthly one.
        AuditAction.AI_BUDGET_EXCEEDED,
    }
)

"""Enterprise data governance & advanced privacy (Phase 8.5).

The properties that carry the milestone:

  * **Classification is deterministic** — a set of privacy labels recommends the
    most sensitive level any of them demands, and the PII flag is derived from
    the labels, never set by hand.
  * **The catalog persists per tenant** and the sensitive-data registry is a
    query over it, not a second store.
  * **Quality rules grade a measurement** through the pure framework and the
    asset rolls up to a defensible score.
  * **Lineage is a validated directed graph** — no self-loops, no duplicate
    edges — answerable per asset.
  * **The dashboard reuses the compliance RoPA link and the trust rating** rather
    than restating either, and tenant isolation holds.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.governance.classification import (
    classify,
    contains_personal_data,
    contains_sensitive_data,
    is_sensitive_level,
    max_level,
    privacy_label,
)
from app.governance.quality import (
    QualityResult,
    evaluate_measurement,
    score_quality,
)
from app.models.audit import AuditLog
from app.models.organization import Organization
from app.schemas.compliance_ops import ProcessingActivityCreate
from app.schemas.governance import (
    AssetCreate,
    AssetOwnershipUpdate,
    AssetUpdate,
    LineageEdgeCreate,
    QualityMeasurement,
    QualityRuleCreate,
)
from app.services.compliance_ops import ProcessingActivityService
from app.services.governance import (
    DataCatalogService,
    DataLineageService,
    DataQualityService,
    GovernanceDashboardService,
)
from app.services.rbac import AuthorizationContext
from tests.conftest import make_user

TEST_JWT_SECRET = "test_secret_that_is_at_least_thirty_two_chars"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "JWT_SECRET": SecretStr(TEST_JWT_SECRET),
        "ENVIRONMENT": "test",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _auth(organization: Organization, user_id, manage: bool = True) -> AuthorizationContext:  # type: ignore[no-untyped-def]
    grants = {"settings.manage": Scope.ALL} if manage else {"leads.view": Scope.OWN}
    return AuthorizationContext(
        user_id=user_id,
        organization_id=organization.id,
        role_keys=("admin",),
        grants=grants,
    )


# ----------------------------------------------------- deterministic core


class TestClassification:
    def test_classify_takes_most_sensitive_floor(self) -> None:
        assert classify([]) == "internal"  # unassessed, not public
        assert classify(["public"]) == "public"
        assert classify(["contact"]) == "confidential"
        assert classify(["contact", "financial"]) == "restricted"

    def test_derived_personal_and_sensitive_flags(self) -> None:
        assert contains_personal_data(["pii"]) is True
        assert contains_sensitive_data(["pii"]) is False
        assert contains_sensitive_data(["phi"]) is True
        assert contains_personal_data(["credentials"]) is False

    def test_level_helpers(self) -> None:
        assert max_level("internal", "restricted") == "restricted"
        assert is_sensitive_level("confidential") is True
        assert is_sensitive_level("internal") is False

    def test_registry_rejects_unknown_label(self) -> None:
        assert privacy_label("phi").sensitive is True
        with pytest.raises(KeyError):
            privacy_label("nope")


class TestQuality:
    def test_measurement_bands(self) -> None:
        assert evaluate_measurement(95, 90) == "pass"
        assert evaluate_measurement(88, 90) == "warn"  # within warn margin
        assert evaluate_measurement(80, 90) == "fail"

    def test_score_rollup(self) -> None:
        mandatory_fail = [
            QualityResult("completeness", "c", "pass", True),
            QualityResult("validity", "v", "fail", True),
        ]
        assert score_quality(mandatory_fail).status == "fail"

        advisory_fail = [QualityResult("validity", "v", "fail", False)]
        assert score_quality(advisory_fail).status == "warn"

        unmeasured = [QualityResult("validity", "v", "not_measured", True)]
        score = score_quality(unmeasured)
        assert score.status == "pass" and score.not_measured == 1


# ------------------------------------------------------------ data catalog


class TestCatalog:
    async def test_create_classifies_and_audits(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "cat@vantage.example")
        service = DataCatalogService(db, _auth(organization, user.id))
        created = await service.create(
            user,
            AssetCreate(
                name="Leads table",
                asset_type="table",
                privacy_labels=["pii", "contact"],
            ),
        )
        # No explicit classification -> recommended from the labels.
        assert created.classification == "confidential"
        assert created.recommended_classification == "confidential"
        assert created.contains_pii is True

        audit = (
            await db.execute(
                select(AuditLog).where(AuditLog.action == "governance.asset.registered")
            )
        ).scalars().all()
        assert len(audit) == 1

    async def test_update_recomputes_pii_and_unique_name(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "cat2@vantage.example")
        service = DataCatalogService(db, _auth(organization, user.id))
        asset = await service.create(
            user, AssetCreate(name="Marketing", asset_type="dataset")
        )
        assert asset.contains_pii is False

        updated = await service.update(
            user, asset.id, AssetUpdate(privacy_labels=["financial"])
        )
        assert updated.contains_pii is True
        assert updated.recommended_classification == "restricted"

        # Duplicate name is rejected with a clean error, not an IntegrityError.
        with pytest.raises(AppError):
            await service.create(
                user, AssetCreate(name="Marketing", asset_type="dataset")
            )

    async def test_unknown_label_rejected(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "cat3@vantage.example")
        service = DataCatalogService(db, _auth(organization, user.id))
        with pytest.raises(AppError):
            await service.create(
                user,
                AssetCreate(name="x", asset_type="table", privacy_labels=["bogus"]),
            )

    async def test_sensitive_registry_and_ownership(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "cat4@vantage.example")
        service = DataCatalogService(db, _auth(organization, user.id))
        await service.create(
            user, AssetCreate(name="Public brochure", asset_type="report")
        )
        sensitive = await service.create(
            user,
            AssetCreate(name="Client PII", asset_type="table", privacy_labels=["pii"]),
        )
        registry = await service.sensitive_registry()
        names = {a.name for a in registry}
        assert "Client PII" in names and "Public brochure" not in names

        owner = await make_user(db, organization, "owner@vantage.example")
        assigned = await service.assign_ownership(
            user, sensitive.id, AssetOwnershipUpdate(owner_id=owner.id)
        )
        assert assigned.owner_id == owner.id

    async def test_processing_activity_link(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "link@vantage.example")
        auth = _auth(organization, user.id)
        activity = await ProcessingActivityService(db, auth).create(
            user,
            ProcessingActivityCreate(
                name="Email marketing", purpose="Send", lawful_basis="consent"
            ),
        )
        asset = await DataCatalogService(db, auth).create(
            user,
            AssetCreate(
                name="Newsletter list",
                asset_type="dataset",
                processing_activity_id=activity.id,
            ),
        )
        assert asset.processing_activity_id == activity.id

        # A processing activity from no tenant / a bad id is rejected.
        from uuid import uuid4

        with pytest.raises(AppError):
            await DataCatalogService(db, auth).create(
                user,
                AssetCreate(
                    name="Bad link",
                    asset_type="dataset",
                    processing_activity_id=uuid4(),
                ),
            )

    async def test_requires_manage_and_isolation(
        self,
        db: AsyncSession,
        organization: Organization,
        other_organization: Organization,
    ) -> None:
        user = await make_user(db, organization, "iso@vantage.example")
        no_manage = DataCatalogService(db, _auth(organization, user.id, manage=False))
        with pytest.raises(PermissionDeniedError):
            await no_manage.list_assets()

        created = await DataCatalogService(db, _auth(organization, user.id)).create(
            user, AssetCreate(name="Mine", asset_type="table")
        )
        theirs = await make_user(db, other_organization, "them@meridian.example")
        their_service = DataCatalogService(db, _auth(other_organization, theirs.id))
        with pytest.raises(NotFoundError):
            await their_service.get(created.id)


# ------------------------------------------------------------ quality rules


class TestQualityRules:
    async def test_rule_measurement_and_asset_score(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "q@vantage.example")
        auth = _auth(organization, user.id)
        asset = await DataCatalogService(db, auth).create(
            user, AssetCreate(name="Orders", asset_type="table")
        )
        quality = DataQualityService(db, auth)
        rule = await quality.create_rule(
            user,
            QualityRuleCreate(
                asset_id=asset.id,
                dimension="completeness",
                name="No null emails",
                threshold=90,
                mandatory=True,
            ),
        )
        assert rule.last_status is None  # not measured yet

        measured = await quality.measure(rule.id, QualityMeasurement(value=80))
        assert measured.last_status == "fail" and measured.last_value == 80

        score = await quality.asset_quality(asset.id)
        assert score.score.status == "fail" and score.score.failed == 1

        await quality.measure(rule.id, QualityMeasurement(value=95))
        recovered = await quality.asset_quality(asset.id)
        assert recovered.score.status == "pass"

    async def test_rule_requires_existing_asset(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        from uuid import uuid4

        user = await make_user(db, organization, "q2@vantage.example")
        auth = _auth(organization, user.id)
        with pytest.raises(NotFoundError):
            await DataQualityService(db, auth).create_rule(
                user,
                QualityRuleCreate(
                    asset_id=uuid4(), dimension="validity", name="x", threshold=50
                ),
            )


# ------------------------------------------------------------ lineage


class TestLineage:
    async def test_edges_validation_and_traversal(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "lin@vantage.example")
        auth = _auth(organization, user.id)
        catalog = DataCatalogService(db, auth)
        raw = await catalog.create(user, AssetCreate(name="Raw", asset_type="table"))
        curated = await catalog.create(
            user, AssetCreate(name="Curated", asset_type="table")
        )
        lineage = DataLineageService(db, auth)

        edge = await lineage.add_edge(
            user,
            LineageEdgeCreate(
                upstream_asset_id=raw.id,
                downstream_asset_id=curated.id,
                transformation="dedupe",
            ),
        )
        assert edge.upstream_asset_id == raw.id

        # No self-loops, no duplicates.
        with pytest.raises(AppError):
            await lineage.add_edge(
                user,
                LineageEdgeCreate(upstream_asset_id=raw.id, downstream_asset_id=raw.id),
            )
        with pytest.raises(AppError):
            await lineage.add_edge(
                user,
                LineageEdgeCreate(
                    upstream_asset_id=raw.id, downstream_asset_id=curated.id
                ),
            )

        graph = await lineage.asset_lineage(curated.id)
        assert [n.name for n in graph.upstream] == ["Raw"]
        assert graph.downstream == []

        await lineage.delete_edge(user, edge.id)
        assert await lineage.list_edges() == []


# ------------------------------------------------------------ dashboard


class TestDashboard:
    async def test_dashboard_aggregates_and_reuses_trust(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        user = await make_user(db, organization, "dash@vantage.example")
        auth = _auth(organization, user.id)
        settings = _settings()
        catalog = DataCatalogService(db, auth)

        owner = await make_user(db, organization, "downer@vantage.example")
        pii = await catalog.create(
            user,
            AssetCreate(
                name="Contacts",
                asset_type="table",
                privacy_labels=["pii"],
                owner_id=owner.id,
            ),
        )
        await catalog.create(user, AssetCreate(name="Metrics", asset_type="report"))

        rule = await DataQualityService(db, auth).create_rule(
            user,
            QualityRuleCreate(
                asset_id=pii.id, dimension="validity", name="valid", threshold=90
            ),
        )
        await DataQualityService(db, auth).measure(rule.id, QualityMeasurement(value=99))

        dashboard = await GovernanceDashboardService(db, auth, settings).dashboard()
        assert dashboard.total_assets == 2
        assert dashboard.pii_assets == 1 and dashboard.sensitive_assets == 1
        assert dashboard.owned_assets == 1 and dashboard.ownership_coverage == 0.5
        assert dashboard.quality.status == "pass"
        assert dashboard.trust_rating in ("strong", "moderate", "at_risk")

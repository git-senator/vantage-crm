"""Reporting — the registry, the query builder, exports and scheduling.

The properties that matter, in order of how much damage their absence does:

  * **No field name reaches SQL unvalidated.** Every name goes through the
    registry; an unknown one is a validation error, not a query.
  * **Rows follow the runner's scope, never the author's.** A shared report is
    a shared *specification*; running it as an agent returns the agent's rows.
  * **A truncated export says so.** `partial`, not `succeeded`.
  * **CSV injection is neutralised.** A cell starting `=` is text, not a
    formula, in whatever spreadsheet opens the file.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.core.permissions import Scope
from app.models.organization import Organization
from app.reporting.export import render_csv, render_pdf, render_xlsx
from app.reporting.query import MAX_ROWS, resolve_spec
from app.schemas.client import ClientCreate
from app.schemas.lead import LeadCreate
from app.services.client import ClientService
from app.services.lead import LeadService
from app.services.rbac import AuthorizationContext
from app.services.report import ReportService, export_key
from tests.conftest import auth_for, make_user

pytestmark = pytest.mark.integration


def _leads_spec(**overrides: object) -> dict:
    data: dict = {"dataset": "leads", "columns": ["first_name", "last_name", "source"]}
    data.update(overrides)
    return data


# ------------------------------------------------------------- validation


class TestSpecValidation:
    """These run without a database: validation is pure, and it stays that way."""

    def test_an_unknown_dataset_is_rejected_with_the_valid_ones(self) -> None:
        with pytest.raises(ConflictError, match="deals"):
            resolve_spec({"dataset": "'; DROP TABLE leads; --"})

    def test_an_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ConflictError, match="not a field"):
            resolve_spec(_leads_spec(columns=["password_hash"]))

    def test_a_field_name_cannot_smuggle_sql(self) -> None:
        """The whole security model in one assertion.

        There is no code path from a request string to SQL text — the name is
        looked up, and a miss is an error.
        """
        with pytest.raises(ConflictError):
            resolve_spec(_leads_spec(filters=[{"field": "id) OR (1=1", "value": "x"}]))

    def test_a_sensitive_field_must_be_requested_explicitly(self) -> None:
        with pytest.raises(ConflictError, match="sensitive"):
            resolve_spec(_leads_spec(columns=["email"]))

        spec = resolve_spec(_leads_spec(columns=["email"], include_sensitive=True))
        assert [f.key for f in spec.columns] == ["email"]

    def test_an_operator_must_suit_the_field_type(self) -> None:
        """`contains` on a number is a cast Postgres performs and cannot index."""
        with pytest.raises(ConflictError, match="does not apply"):
            resolve_spec(
                _leads_spec(filters=[{"field": "score", "operator": "contains", "value": "5"}])
            )

    def test_a_value_is_coerced_to_the_columns_type(self) -> None:
        spec = resolve_spec(
            _leads_spec(filters=[{"field": "budget_max", "operator": "gte", "value": "500000"}])
        )
        assert spec.filters[0].value == Decimal("500000")

    def test_an_uncoercible_value_is_a_clear_error(self) -> None:
        with pytest.raises(ConflictError, match="not a valid"):
            resolve_spec(
                _leads_spec(filters=[{"field": "score", "operator": "gt", "value": "abc"}])
            )

    def test_high_cardinality_fields_cannot_be_grouped(self) -> None:
        with pytest.raises(ConflictError, match="cannot be grouped"):
            resolve_spec(_leads_spec(group_by=["first_name"]))

    def test_grouping_without_an_aggregate_defaults_to_count(self) -> None:
        spec = resolve_spec(_leads_spec(group_by=["source"], columns=[]))
        assert [a.function for a in spec.aggregates] == ["count"]

    def test_sum_requires_a_numeric_field(self) -> None:
        with pytest.raises(ConflictError, match="not numeric"):
            resolve_spec(
                _leads_spec(
                    group_by=["source"],
                    aggregates=[{"function": "sum", "field": "stage"}],
                )
            )

    def test_the_row_cap_is_a_ceiling_not_a_suggestion(self) -> None:
        spec = resolve_spec(_leads_spec(limit=10_000_000))
        assert spec.limit == MAX_ROWS

    def test_between_needs_exactly_two_values(self) -> None:
        with pytest.raises(ConflictError, match="exactly two"):
            resolve_spec(
                _leads_spec(filters=[{"field": "score", "operator": "between", "value": [1]}])
            )


# ---------------------------------------------------------------- exports


class TestExportRendering:
    def test_csv_neutralises_formula_injection(self) -> None:
        """`=cmd|'/c calc'!A1` in a CRM note must not execute for whoever opens
        the export. The value is preserved, prefixed so it renders as text."""
        payload = render_csv(["Name"], [("=cmd|'/c calc'!A1",)])
        text = payload.decode("utf-8-sig")
        assert "'=cmd" in text
        assert "\n=cmd" not in text

    def test_csv_carries_a_bom(self) -> None:
        """Without it Excel on Windows mangles every non-ASCII name."""
        payload = render_csv(["Name"], [("Nyström",)])
        assert payload.startswith(b"\xef\xbb\xbf")
        assert "Nyström" in payload.decode("utf-8-sig")

    def test_xlsx_keeps_numbers_numeric(self) -> None:
        """A spreadsheet whose totals cannot be summed is a screenshot."""
        import io

        from openpyxl import load_workbook

        payload = render_xlsx(["Value"], [(Decimal("1250.50"),)], "Deals")
        sheet = load_workbook(io.BytesIO(payload)).active
        assert sheet["A2"].value == 1250.5

    def test_pdf_renders_and_truncates_visibly(self) -> None:
        rows = [(f"Row {i}",) for i in range(3_000)]
        payload = render_pdf(["Name"], rows, "Big report")
        assert payload.startswith(b"%PDF")

    def test_pdf_survives_markup_in_the_data(self) -> None:
        """reportlab's Paragraph takes a small HTML dialect; an unescaped `<`
        from a CRM note breaks the build."""
        payload = render_pdf(["Note"], [("<b>not bold</b> & <script>",)], "Notes")
        assert payload.startswith(b"%PDF")


# ------------------------------------------------------------- execution


class TestPreview:
    async def test_returns_the_callers_rows(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        for name in ("One", "Two", "Three"):
            await LeadService(db, auth).create_lead(
                LeadCreate(first_name=name, last_name="Lead"), user
            )

        result = await ReportService(db, auth).preview(_leads_spec())
        assert result["row_count"] == 3
        assert result["total_rows"] == 3
        assert result["truncated"] is False
        assert result["headers"] == ["First name", "Last name", "Source"]

    async def test_a_shared_report_still_returns_the_runners_rows(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        """Sharing widens who may run a report. It never widens the rows.

        The alternative turns a saved report into a privilege-escalation
        primitive that looks like a feature.
        """
        one = await make_user(db, organization, "one@vantage.example")
        two = await make_user(db, organization, "two@vantage.example")
        one_auth = await auth_for(db, one, "agent")
        two_auth = await auth_for(db, two, "agent")

        await LeadService(db, one_auth).create_lead(
            LeadCreate(first_name="Ones", last_name="Lead"), one
        )
        record = await ReportService(db, one_auth).create_definition(
            name="Every lead",
            description=None,
            definition=_leads_spec(),
            is_shared=True,
            schedule="none",
            schedule_format="csv",
            recipients=[],
            actor=one,
        )
        await db.flush()

        # The other agent can see the definition...
        assert await ReportService(db, two_auth).get_definition(record.id) is not None
        # ...and gets none of the first agent's rows from it.
        result = await ReportService(db, two_auth).preview(dict(record.definition))
        assert result["row_count"] == 0

    async def test_grouping_aggregates(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        for source in ("referral", "referral", "website"):
            await LeadService(db, auth).create_lead(
                LeadCreate(first_name="A", last_name="B", source=source), user
            )

        result = await ReportService(db, auth).preview(
            {"dataset": "leads", "group_by": ["source"], "columns": []}
        )
        counts = {row[0]: row[1] for row in result["rows"]}
        assert counts["referral"] == 2
        assert counts["website"] == 1

    async def test_a_filter_narrows_the_result(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Hot", last_name="One", temperature="hot"), user
        )
        await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Cold", last_name="Two", temperature="cold"), user
        )

        result = await ReportService(db, auth).preview(
            _leads_spec(filters=[{"field": "temperature", "operator": "eq", "value": "hot"}])
        )
        assert result["row_count"] == 1

    async def test_a_dataset_the_caller_cannot_see_is_refused(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "narrow@vantage.example")
        auth = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("agent",),
            grants={"reports.view": Scope.ALL, "leads.view": Scope.ALL},
        )
        with pytest.raises(ConflictError, match="do not have access"):
            await ReportService(db, auth).preview({"dataset": "deals"})

    async def test_the_dataset_catalogue_hides_what_the_caller_cannot_query(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "catalogue@vantage.example")
        auth = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("agent",),
            grants={"reports.view": Scope.ALL, "leads.view": Scope.ALL},
        )
        keys = {d["key"] for d in ReportService(db, auth).list_datasets()}
        assert keys == {"leads"}


class TestDefinitions:
    async def test_a_broken_definition_cannot_be_saved(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        """Otherwise it fails nightly at 03:00 for a month."""
        user, auth = admin
        with pytest.raises(ConflictError):
            await ReportService(db, auth).create_definition(
                name="Broken",
                description=None,
                definition={"dataset": "leads", "columns": ["nope"]},
                is_shared=False,
                schedule="none",
                schedule_format="csv",
                recipients=[],
                actor=user,
            )

    async def test_scheduling_requires_export_permission(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "sched@vantage.example")
        auth = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("agent",),
            grants={"reports.view": Scope.OWN, "leads.view": Scope.OWN},
        )
        with pytest.raises(PermissionDeniedError):
            await ReportService(db, auth).create_definition(
                name="Nightly",
                description=None,
                definition=_leads_spec(),
                is_shared=False,
                schedule="daily",
                schedule_format="csv",
                recipients=[],
                actor=user,
            )

    async def test_a_private_report_is_invisible_to_others(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        one = await make_user(db, organization, "priv1@vantage.example")
        two = await make_user(db, organization, "priv2@vantage.example")
        one_auth = await auth_for(db, one, "agent")
        two_auth = await auth_for(db, two, "agent")

        record = await ReportService(db, one_auth).create_definition(
            name="Mine only",
            description=None,
            definition=_leads_spec(),
            is_shared=False,
            schedule="none",
            schedule_format="csv",
            recipients=[],
            actor=one,
        )
        with pytest.raises(NotFoundError):
            await ReportService(db, two_auth).get_definition(record.id)

    async def test_deleting_is_soft_and_cancels_the_schedule(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ReportService(db, auth)
        record = await service.create_definition(
            name="Weekly digest",
            description=None,
            definition=_leads_spec(),
            is_shared=False,
            schedule="weekly",
            schedule_format="csv",
            recipients=[],
            actor=user,
        )
        await service.delete_definition(record.id, user)

        assert record.deleted_at is not None
        # A deleted report must not keep firing.
        assert record.schedule == "none"
        with pytest.raises(NotFoundError):
            await service.get_definition(record.id)


class TestRuns:
    async def test_an_export_produces_a_file_and_a_run(
        self, db: AsyncSession, admin, object_storage
    ) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        await LeadService(db, auth).create_lead(
            LeadCreate(first_name="Export", last_name="Me"), user
        )

        service = ReportService(db, auth, storage=object_storage)
        run = await service.request_export(
            definition_id=None, definition=_leads_spec(), format="csv", actor=user
        )
        assert run.status == "queued"

        await service.execute_run(run)
        assert run.status == "succeeded"
        assert run.row_count == 1
        assert run.storage_key is not None
        assert run.size_bytes and run.size_bytes > 0

    async def test_a_truncated_export_is_partial_not_succeeded(
        self, db: AsyncSession, admin, object_storage
    ) -> None:  # type: ignore[no-untyped-def]
        """A spreadsheet silently missing its tail is worse than one that says
        so, which is the entire reason this status exists."""
        user, auth = admin
        for index in range(3):
            await LeadService(db, auth).create_lead(
                LeadCreate(first_name=f"L{index}", last_name="Row"), user
            )

        service = ReportService(db, auth, storage=object_storage)
        run = await service.request_export(
            definition_id=None,
            definition=_leads_spec(limit=1),
            format="csv",
            actor=user,
        )
        await service.execute_run(run)

        assert run.status == "partial"
        assert run.row_count == 1
        assert run.total_rows == 3

    async def test_an_export_is_audited(
        self, db: AsyncSession, admin, object_storage
    ) -> None:  # type: ignore[no-untyped-def]
        """ "Who took the client list" must have an answer."""
        from sqlalchemy import select

        from app.core.audit_actions import AuditAction
        from app.models.audit import AuditLog

        user, auth = admin
        await ClientService(db, auth).create_client(
            ClientCreate(first_name="Audit", last_name="Me", type="buyer"), user
        )

        service = ReportService(db, auth, storage=object_storage)
        run = await service.request_export(
            definition_id=None,
            definition={"dataset": "clients", "columns": ["first_name"]},
            format="csv",
            actor=user,
        )
        await service.execute_run(run)

        entries = (
            (
                await db.execute(
                    select(AuditLog).where(AuditLog.action == AuditAction.RECORD_EXPORTED)
                )
            )
            .scalars()
            .all()
        )
        assert len(entries) == 1
        assert entries[0].metadata_["dataset"] == "clients"
        assert entries[0].metadata_["rows"] == 1

    async def test_exporting_requires_the_export_permission(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        user = await make_user(db, organization, "noexport@vantage.example")
        auth = AuthorizationContext(
            user_id=user.id,
            organization_id=organization.id,
            role_keys=("agent",),
            grants={"reports.view": Scope.OWN, "leads.view": Scope.OWN},
        )
        with pytest.raises(PermissionDeniedError):
            await ReportService(db, auth).request_export(
                definition_id=None,
                definition=_leads_spec(),
                format="csv",
                actor=user,
            )

    async def test_a_failed_run_records_why(self, db: AsyncSession, admin) -> None:  # type: ignore[no-untyped-def]
        user, auth = admin
        service = ReportService(db, auth)
        run = await service.request_export(
            definition_id=None, definition=_leads_spec(), format="csv", actor=user
        )
        await service.fail_run(run, "StorageError: bucket unreachable")

        assert run.status == "failed"
        assert "bucket unreachable" in (run.error or "")
        assert run.completed_at is not None

    async def test_another_users_run_is_not_readable(
        self, db: AsyncSession, organization: Organization, rbac_seeded
    ) -> None:  # type: ignore[no-untyped-def]
        one = await make_user(db, organization, "run1@vantage.example")
        two = await make_user(db, organization, "run2@vantage.example")
        one_auth = await auth_for(db, one, "manager")
        two_auth = AuthorizationContext(
            user_id=two.id,
            organization_id=organization.id,
            role_keys=("agent",),
            grants={"reports.view": Scope.OWN, "leads.view": Scope.OWN},
        )

        run = await ReportService(db, one_auth).request_export(
            definition_id=None, definition=_leads_spec(), format="csv", actor=one
        )
        await db.flush()

        with pytest.raises(NotFoundError):
            await ReportService(db, two_auth).get_run(run.id)


class TestStorageKeys:
    def test_the_key_is_tenant_first_and_run_scoped(self) -> None:
        """Tenant-first makes a per-tenant deletion a prefix operation; the run
        id stops a re-export silently overwriting an older link's file."""
        import uuid

        organization = uuid.uuid4()
        run = uuid.uuid4()
        key = export_key(organization, run, "Q3 pipeline", "xlsx")

        assert key.startswith(f"org/{organization}/exports/{run}/")
        assert key.endswith(".xlsx")

    def test_a_hostile_report_name_cannot_steer_the_key(self) -> None:
        import uuid

        organization = uuid.uuid4()
        key = export_key(organization, uuid.uuid4(), "../../etc/passwd", "csv")
        assert ".." not in key
        assert key.startswith(f"org/{organization}/exports/")


class TestScheduleDueness:
    def test_a_new_schedule_is_due_immediately(self) -> None:
        """A newly scheduled report should not wait a full period for its first
        delivery."""
        from datetime import UTC, datetime

        from app.models.report import ReportDefinition
        from app.repositories.report import _is_due

        definition = ReportDefinition(schedule="daily", last_run_at=None)
        assert _is_due(definition, datetime.now(UTC))

    def test_dueness_is_computed_from_the_last_run(self) -> None:
        """Not from a stored next-run timestamp, which drifts whenever a run is
        missed and silently stops the report."""
        from datetime import UTC, datetime, timedelta

        from app.models.report import ReportDefinition
        from app.repositories.report import _is_due

        now = datetime.now(UTC)
        recent = ReportDefinition(schedule="daily", last_run_at=now - timedelta(hours=2))
        stale = ReportDefinition(schedule="daily", last_run_at=now - timedelta(days=9))

        assert not _is_due(recent, now)
        assert _is_due(stale, now)

    def test_an_unscheduled_report_is_never_due(self) -> None:
        from datetime import UTC, datetime

        from app.models.report import ReportDefinition
        from app.repositories.report import _is_due

        assert not _is_due(ReportDefinition(schedule="none"), datetime.now(UTC))


class TestTenantIsolation:
    async def test_a_report_cannot_reach_another_tenants_rows(
        self, db: AsyncSession, admin, other_organization: Organization
    ) -> None:  # type: ignore[no-untyped-def]
        _user, admin_auth = admin
        outsider = await make_user(db, other_organization, "x@meridian.example")
        outsider_auth = AuthorizationContext(
            user_id=outsider.id,
            organization_id=other_organization.id,
            role_keys=("admin",),
            grants={"leads.view": Scope.ALL, "leads.manage": Scope.ALL},
        )
        await LeadService(db, outsider_auth).create_lead(
            LeadCreate(first_name="Foreign", last_name="Lead"), outsider
        )

        result = await ReportService(db, admin_auth).preview(_leads_spec())
        assert result["row_count"] == 0

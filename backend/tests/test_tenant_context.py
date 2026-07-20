"""Guards risk R8 — tenant context must be transaction-scoped.

R8 is rated Critical in docs/ROADMAP.md. `SET LOCAL` is scoped to the enclosing
transaction; a plain `SET` is scoped to the *connection*. Because connections
are pooled and reused across requests, a plain `SET` would leak one tenant's
scope into the next tenant's request — silently, with no error raised anywhere.

This is a static guard on the mechanism. The full cross-tenant integration test
against a live database is a Phase 1 exit criterion.
"""

from __future__ import annotations

import inspect
import re

from app.db import session as session_module


class TestTenantContextIsTransactionScoped:
    def test_set_tenant_context_uses_local_scope(self) -> None:
        """`set_config(..., is_local => true)` is the third argument."""
        source = inspect.getsource(session_module.set_tenant_context)
        set_config_calls = re.findall(r"set_config\([^)]*\)", source)
        assert set_config_calls, "expected set_config to bind the tenant GUC"
        for call in set_config_calls:
            assert "true" in call, (
                f"set_config call is not transaction-local: {call!r}. "
                "The third argument MUST be true, or tenant scope leaks across "
                "pooled connections. See docs/DATABASE.md §2 and risk R8."
            )

    def test_no_connection_scoped_set_statements(self) -> None:
        """A bare `SET app.current_org` anywhere in the module is the bug itself."""
        source = inspect.getsource(session_module)
        bare_set = re.search(r'"\s*SET\s+app\.', source, re.IGNORECASE)
        assert bare_set is None, (
            "Found a connection-scoped SET for a tenant GUC. Use "
            "set_config(..., true) so the value is transaction-local."
        )

    def test_tenant_values_are_bound_not_interpolated(self) -> None:
        """The org id must be a bound parameter, never an f-string."""
        source = inspect.getsource(session_module.set_tenant_context)
        assert ":org" in source, "organization id should be a bound parameter"
        assert "{organization_id}" not in source, (
            "organization id must not be interpolated into SQL text"
        )


class TestGucNaming:
    def test_gucs_are_namespaced(self) -> None:
        """Custom GUCs need a prefix or PostgreSQL rejects them."""
        assert session_module.TENANT_GUC.startswith("app.")
        assert session_module.ACTOR_GUC.startswith("app.")

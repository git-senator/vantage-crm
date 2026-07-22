"""What a running workflow is allowed to do, and what it can see.

**Authorization.** A run executes with a `system_context` — the same machine
context background jobs use — granted exactly the permissions its actions need
and nothing else. That is safe here for a reason specific to this feature:
authoring a workflow requires `automations.manage`, which only owner and admin
hold, and both hold every CRM permission at ALL scope already. So the execution
context is never *wider* than its author's.

The alternative — running as the author, resolved at execution time — was
rejected. It sounds tighter and behaves worse: a workflow silently stops working
when its author changes role or leaves, weeks after anyone connected the two,
and the failure is a customer who never got their follow-up.

The consequence is stated rather than implied: **a workflow can act on records
no individual agent could see.** That is the point of automation, and it is why
authoring is an administrative capability rather than a wider version of editing
your own book.

**Variables.** The run context is a flat namespace the templating reads:
`{{lead.first_name}}`, `{{actor.full_name}}`, `{{step.n3.task_id}}`. It is
bounded — actions contribute small scalars, never whole records — because it is
persisted on every step and an unbounded context is how a long workflow becomes
a multi-megabyte row that is rewritten a dozen times.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.models.user import User
from app.services.rbac import AuthorizationContext
from app.workers.context import system_context

#: Everything an action might need. Granted as a set rather than per-action
#: because the run holds one context for its whole life, and a workflow's steps
#: are known only at execution time.
#:
#: Notably absent: `users.manage`, `roles.manage`, `settings.manage`,
#: `billing.manage`. No action needs them, and a workflow that could grant
#: itself a role is a privilege-escalation primitive with a friendly UI.
AUTOMATION_GRANTS: tuple[str, ...] = (
    "leads.view",
    "leads.manage",
    "leads.assign",
    "contacts.view",
    "contacts.manage",
    "contacts.assign",
    "properties.view",
    "properties.manage",
    "properties.assign",
    "deals.view",
    "deals.manage",
    "tasks.view",
    "tasks.manage",
    "notes.view",
    "notes.manage",
    "activities.view",
    "activities.manage",
    "documents.view",
)

#: A single string variable longer than this is truncated before it reaches the
#: context. Templating renders names and titles, not documents.
MAX_VARIABLE_LENGTH = 1_000


@dataclass(slots=True)
class ExecutionContext:
    """One run's world: who it acts as, what it knows, what it has done."""

    organization_id: UUID
    workflow_id: UUID
    run_id: UUID
    auth: AuthorizationContext
    #: Whose name the writes happen under.
    #:
    #: Services take an acting `User` for the audit trail, and a workflow needs
    #: an answer to "who did this". It is the workflow's **author** — the admin
    #: who published it — which is both true and the most useful thing an audit
    #: reader could be told. A synthetic system user would be less honest and
    #: would need a row in `users` that nobody can log in as.
    acting_user: User
    entity_type: str | None
    entity_id: UUID | None
    #: The trigger snapshot plus whatever steps have produced.
    variables: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def for_run(
        cls,
        *,
        organization_id: UUID,
        workflow_id: UUID,
        run_id: UUID,
        acting_user: User,
        entity_type: str | None,
        entity_id: UUID | None,
        variables: dict[str, Any] | None = None,
    ) -> ExecutionContext:
        return cls(
            organization_id=organization_id,
            workflow_id=workflow_id,
            run_id=run_id,
            auth=system_context(organization_id, *AUTOMATION_GRANTS),
            acting_user=acting_user,
            entity_type=entity_type,
            entity_id=entity_id,
            variables=variables or {},
        )

    # ------------------------------------------------------------ reading

    @property
    def record(self) -> dict[str, Any]:
        """The triggering record's snapshot, as conditions see it."""
        return self.variables.get("record") or {}

    @property
    def previous(self) -> dict[str, Any]:
        """The before-image on an update; empty on a create."""
        return self.variables.get("previous") or {}

    @property
    def actor_id(self) -> str | None:
        """Who caused the trigger. None for machine-initiated changes."""
        actor = self.variables.get("actor") or {}
        value = actor.get("id")
        return str(value) if value else None

    def owner_id(self) -> UUID | None:
        """The triggering record's owner, whatever the entity calls it.

        Leads and clients have `owner_id`, tasks have `assignee_id`, listings
        have `listing_agent_id`. Actions that say "assign to whoever owns this"
        should not have to know which — that knowledge belongs in one place.
        """
        for key in ("owner_id", "assignee_id", "listing_agent_id"):
            value = self.record.get(key)
            if value:
                try:
                    return UUID(str(value))
                except ValueError:  # pragma: no cover - defensive
                    return None
        return None

    # ------------------------------------------------------------ writing

    def remember(self, node_id: str, values: dict[str, Any]) -> None:
        """Record a step's output under `step.<node_id>`.

        Namespaced by node so two `create_task` steps in one workflow do not
        overwrite each other's ids — which is exactly what a flat namespace
        would do, silently, in the workflow most likely to have two of them.
        """
        steps = self.variables.setdefault("step", {})
        steps[node_id] = {
            key: _bounded(value) for key, value in values.items()
        }


def _bounded(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_VARIABLE_LENGTH:
        return value[:MAX_VARIABLE_LENGTH]
    return value

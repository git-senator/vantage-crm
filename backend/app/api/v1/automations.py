"""Automation endpoints.

Everything here is gated on `automations.view` or `automations.manage`, which
only owner and admin hold. That is not a convenience — it is the reason the
execution context can be a broad system context without becoming a
privilege-escalation path. See `app/automation/context.py`.

The registry endpoint is what makes the visual builder possible without a second
copy of the trigger and action catalogues living in the frontend. It serves the
same registries the executor reads, so the palette cannot offer something the
engine cannot run.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.v1.dependencies import (
    Authorization,
    CurrentUser,
    TenantSessionDep,
    require,
    verify_csrf,
)
from app.automation.actions import ACTIONS
from app.automation.conditions import OPERATORS
from app.automation.definition import DefinitionError
from app.automation.registry import Definition
from app.automation.triggers import TRIGGERS
from app.core.exceptions import AppError
from app.models.automation import Workflow, WorkflowRun, WorkflowVersion
from app.schemas.automation import (
    DefinitionSave,
    EnabledUpdate,
    RegistriesRead,
    RegistryEntryRead,
    ValidationResult,
    WorkflowCreate,
    WorkflowDetail,
    WorkflowRead,
    WorkflowRunDetail,
    WorkflowRunRead,
    WorkflowRunStepRead,
    WorkflowUpdate,
    WorkflowVersionRead,
)
from app.services.automation import AutomationService

router = APIRouter()


class InvalidDefinitionError(AppError):
    """A definition that cannot be published.

    422 rather than 400: the request is well-formed JSON that the domain
    refuses, which is exactly the distinction the status codes exist for. The
    `errors` list travels in the problem body so the builder can mark up every
    bad node at once.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    problem_type = "invalid-workflow"
    title = "This workflow cannot be published"


def _to_workflow(workflow: Workflow, trigger_type: str | None = None) -> WorkflowRead:
    return WorkflowRead(
        id=workflow.id,
        name=workflow.name,
        description=workflow.description,
        is_enabled=workflow.is_enabled,
        published_version_id=workflow.published_version_id,
        author=(
            {
                "id": workflow.author.id,
                "full_name": workflow.author.full_name,
                "initials": workflow.author.initials,
                "avatar_hue": workflow.author.avatar_hue,
            }
            if workflow.author
            else None
        ),
        trigger_type=trigger_type,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


def _to_version(version: WorkflowVersion) -> WorkflowVersionRead:
    return WorkflowVersionRead.model_validate(version)


def _to_run(run: WorkflowRun) -> WorkflowRunRead:
    return WorkflowRunRead.model_validate(run)


def _to_entry(definition: Definition) -> RegistryEntryRead:
    return RegistryEntryRead(
        key=definition.key,
        label=definition.label,
        description=definition.description,
        category=definition.category,
        fields=[
            {
                "key": spec.key,
                "label": spec.label,
                "kind": spec.kind,
                "required": spec.required,
                "help_text": spec.help_text,
                "options": [
                    {"value": option.value, "label": option.label}
                    for option in spec.options
                ],
                "default": spec.default,
            }
            for spec in definition.fields
        ],
        entity_type=getattr(definition, "entity_type", None) or None,
        entity_types=list(getattr(definition, "entity_types", ()) or ()),
        external=bool(getattr(definition, "external", False)),
        takes_value=getattr(definition, "takes_value", None),
        value_kind=getattr(definition, "value_kind", None),
    )


@router.get("/registries", response_model=RegistriesRead)
async def get_registries(
    _auth: Annotated[Authorization, Depends(require("automations.view"))],
    _user: CurrentUser,
) -> RegistriesRead:
    """The builder's palette, from the registries the executor reads.

    Served rather than duplicated in the frontend: two hand-maintained copies of
    "what fields does the send-email action take" drift within a week, and the
    symptom is a workflow that validates in the UI and fails at run time.
    """
    return RegistriesRead(
        triggers=[_to_entry(entry) for entry in TRIGGERS.all()],
        actions=[_to_entry(entry) for entry in ACTIONS.all()],
        operators=[_to_entry(entry) for entry in OPERATORS.all()],
    )


@router.get("", response_model=list[WorkflowRead])
async def list_workflows(
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("automations.view"))],
    _user: CurrentUser,
) -> list[WorkflowRead]:
    service = AutomationService(session, auth)
    workflows = await service.list_workflows()

    result: list[WorkflowRead] = []
    for workflow in workflows:
        published = await service.workflows.published_version(
            workflow.id, auth.organization_id
        )
        result.append(_to_workflow(workflow, published.trigger_type if published else None))
    return result


@router.get("/runs", response_model=list[WorkflowRunRead])
async def list_runs(
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("automations.view"))],
    _user: CurrentUser,
    workflow_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[WorkflowRunRead]:
    """The execution log across every workflow, newest first.

    The answer to "why did this client get that email", which is the question
    that decides whether an automation feature is operable.
    """
    runs = await AutomationService(session, auth).list_runs(
        workflow_id=workflow_id, limit=limit
    )
    return [_to_run(run) for run in runs]


@router.get("/runs/{run_id}", response_model=WorkflowRunDetail)
async def get_run(
    run_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("automations.view"))],
    _user: CurrentUser,
) -> WorkflowRunDetail:
    run = await AutomationService(session, auth).get_run(run_id)
    return WorkflowRunDetail(
        run=_to_run(run),
        steps=[WorkflowRunStepRead.model_validate(step) for step in run.steps],
    )


@router.post(
    "/runs/{run_id}/cancel",
    response_model=WorkflowRunRead,
    dependencies=[Depends(verify_csrf)],
)
async def cancel_run(
    run_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("automations.manage"))],
    user: CurrentUser,
) -> WorkflowRunRead:
    """Stop a run parked on a delay.

    Only `waiting` runs can be cancelled: a `running` one is inside a worker,
    and a flag it never checks is not a cancellation.
    """
    return _to_run(await AutomationService(session, auth).cancel_run(run_id, user))


@router.get("/{workflow_id}", response_model=WorkflowDetail)
async def get_workflow(
    workflow_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("automations.view"))],
    _user: CurrentUser,
) -> WorkflowDetail:
    """A workflow with its draft, its live version, and its history."""
    service = AutomationService(session, auth)
    workflow = await service.get_workflow(workflow_id)

    draft = await service.workflows.latest_draft(workflow_id, auth.organization_id)
    published = await service.workflows.published_version(
        workflow_id, auth.organization_id
    )
    versions = await service.workflows.list_versions(workflow_id, auth.organization_id)

    return WorkflowDetail(
        workflow=_to_workflow(
            workflow, published.trigger_type if published else None
        ),
        draft=_to_version(draft) if draft else None,
        published=_to_version(published) if published else None,
        versions=[_to_version(version) for version in versions],
    )


@router.post(
    "",
    response_model=WorkflowDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_csrf)],
)
async def create_workflow(
    payload: WorkflowCreate,
    response: Response,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("automations.manage"))],
    user: CurrentUser,
) -> WorkflowDetail:
    """Create a workflow and its first draft.

    Created disabled. A workflow that starts running the moment it is saved is
    a workflow nobody gets to review first.
    """
    workflow, draft = await AutomationService(session, auth).create_workflow(
        payload, user
    )
    response.headers["Location"] = f"/api/v1/automations/{workflow.id}"
    return WorkflowDetail(
        workflow=_to_workflow(workflow),
        draft=_to_version(draft),
        published=None,
        versions=[_to_version(draft)],
    )


@router.patch(
    "/{workflow_id}",
    response_model=WorkflowRead,
    dependencies=[Depends(verify_csrf)],
)
async def update_workflow(
    workflow_id: UUID,
    payload: WorkflowUpdate,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("automations.manage"))],
    user: CurrentUser,
) -> WorkflowRead:
    """Rename or re-describe. The definition has its own endpoint."""
    workflow = await AutomationService(session, auth).update_workflow(
        workflow_id, payload, user
    )
    return _to_workflow(workflow)


@router.put(
    "/{workflow_id}/definition",
    response_model=WorkflowVersionRead,
    dependencies=[Depends(verify_csrf)],
)
async def save_definition(
    workflow_id: UUID,
    payload: DefinitionSave,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("automations.manage"))],
    user: CurrentUser,
) -> WorkflowVersionRead:
    """Save the builder's current state to the draft.

    **Not validated.** A draft is allowed to be incoherent — refusing every save
    until the graph is complete makes the builder unusable halfway through
    building something. Publishing is where validation refuses.
    """
    draft = await AutomationService(session, auth).save_draft(
        workflow_id, payload.definition, user
    )
    return _to_version(draft)


@router.post("/{workflow_id}/validate", response_model=ValidationResult)
async def validate_definition(
    workflow_id: UUID,
    payload: DefinitionSave,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("automations.view"))],
    _user: CurrentUser,
) -> ValidationResult:
    """Check a definition without saving it.

    Returns every problem rather than the first, so the builder can mark up the
    whole canvas in one pass instead of making the author fix, save, discover,
    repeat.
    """
    errors = AutomationService(session, auth).validate_draft(payload.definition)
    return ValidationResult(valid=not errors, errors=errors)


@router.post(
    "/{workflow_id}/publish",
    response_model=WorkflowVersionRead,
    dependencies=[Depends(verify_csrf)],
)
async def publish_workflow(
    workflow_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("automations.manage"))],
    user: CurrentUser,
) -> WorkflowVersionRead:
    """Validate the draft and make it live.

    The previous published version is archived rather than deleted — a run in
    flight still points at it, and "what was this doing last Tuesday" needs an
    answer.
    """
    try:
        version = await AutomationService(session, auth).publish(workflow_id, user)
    except DefinitionError as exc:
        raise InvalidDefinitionError(
            "This workflow has problems that must be fixed before it can go "
            "live.",
            errors=exc.errors,
        ) from exc
    return _to_version(version)


@router.post(
    "/{workflow_id}/enabled",
    response_model=WorkflowRead,
    dependencies=[Depends(verify_csrf)],
)
async def set_enabled(
    workflow_id: UUID,
    payload: EnabledUpdate,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("automations.manage"))],
    user: CurrentUser,
) -> WorkflowRead:
    """The switch.

    Separate from publishing, so pausing a misbehaving automation does not
    require editing or republishing anything.
    """
    workflow = await AutomationService(session, auth).set_enabled(
        workflow_id, payload.is_enabled, user
    )
    return _to_workflow(workflow)


@router.delete(
    "/{workflow_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_csrf)],
)
async def delete_workflow(
    workflow_id: UUID,
    session: TenantSessionDep,
    auth: Annotated[Authorization, Depends(require("automations.manage"))],
    user: CurrentUser,
) -> None:
    """Soft delete, switching it off on the way out."""
    await AutomationService(session, auth).delete_workflow(workflow_id, user)


__all__ = ["InvalidDefinitionError", "router"]

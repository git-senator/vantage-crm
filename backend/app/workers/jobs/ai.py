"""Background execution support for AI completions.

AI features are slow — a completion is a network round trip to a model that can
take seconds — and many of them are not interactive: nightly lead re-scoring, a
batch of deal-health checks, a summary generated the moment a record changes.
Those belong on the queue, not in a request, so this is the seam the feature
milestones (6.3+) enqueue through.

6.1 ships the seam and its guarantees; it does not ship a feature that uses it.
The job is intentionally generic: it runs one prompt against one entity under a
tenant's own scope, records the result the same way a synchronous call would,
and hands the answer to a callback the enqueuing feature owns. The completion
still goes through `AIService.complete`, so the cost ceiling, the ledger and the
audit trail hold for background work exactly as for a request — a job that
bypassed them would be a hole in the one place the guarantees are supposed to
live.

**No AI-initiated writes (SECURITY.md §5).** This job produces text and records
cost. It does not act on what the model said — a feature that turns a completion
into a draft for human approval does that in its own handler, never here.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.logging import get_logger
from app.services.ai.base import AIError, ChatMessage, CompletionRequest
from app.services.ai.service import AIService
from app.workers.context import system_context, tenant_scope
from app.workers.runner import job

logger = get_logger(__name__)

#: The grant a machine-run completion holds. `ai.use` and nothing wider: a
#: background job reads no more than the interactive path would, and a nightly
#: sweep that quietly ran at ALL scope would be a scope escalation dressed as a
#: cron tick. Feature jobs that need to read an entity add that entity's view
#: grant deliberately.
_AI_GRANTS: tuple[str, ...] = ("ai.use",)


@job(organization_arg=0, max_tries=3)
async def run_completion(
    ctx: dict[str, Any],
    organization_id: str,
    feature: str,
    system: str,
    user_message: str,
    model: str,
    max_tokens: int = 512,
) -> str:
    """Run one completion for a tenant, off the request path. Returns the text.

    Deliberately parameterised rather than reaching for a prompt registry entry:
    the enqueuing feature has already rendered its prompt to a system string and
    a user turn (with the untrusted content fenced and redacted upstream), so
    this job stays a thin, feature-agnostic executor. Retried up to three times
    on a transient model fault — the `@job` runner only re-raises `AIError` when
    it is retryable, so a bad-request or budget refusal dead-letters immediately
    rather than burning the budget.
    """
    organization = UUID(organization_id)

    async with tenant_scope(organization) as session:
        auth = system_context(organization, *_AI_GRANTS)
        service = AIService(session, auth)

        request = CompletionRequest(
            system=system,
            messages=[ChatMessage(role="user", content=user_message)],
            model=model,
            max_tokens=max_tokens,
            metadata={"feature": feature},
        )
        try:
            result = await service.complete(request, feature=feature)
        except AIError:
            # Recorded by the service already (a failed/refused ai_jobs row and
            # an audit entry). Re-raised so the runner decides retry vs.
            # dead-letter from `retryable`.
            raise
        await session.commit()

    logger.info(
        "ai_background_completion",
        extra={"organization_id": organization_id, "feature": feature},
    )
    return result.text

"""AIService — the only AI API business logic should touch.

Every completion in the system goes through `complete()`, and that is where the
AI-layer security controls (SECURITY.md §5) are enforced in one place rather
than trusted to each feature:

  * **Enablement.** The master switch is checked first. The AI layer sends CRM
    data to an external model, so it does nothing at all until a deployment opts
    in — a present API key is not consent.
  * **Permission.** `ai.use` is required, resolved through the same
    `AuthorizationContext` as everything else.
  * **Cost ceiling, before dispatch.** The tenant's month-to-date spend is
    summed and checked *before* the request leaves. A refused call sends
    nothing, spends nothing, and is recorded as `refused` — the ceiling is a
    guarantee, not a warning.
  * **The ledger.** Every attempt writes an `ai_jobs` row and an audit entry.
    The audit records *egress* — customer data left for a third party — which is
    the auditable act whether the model answered or errored.

What this service does **not** do is decide what to say. It executes a
`CompletionRequest` a prompt template built and a context builder filled under
the caller's scope; the safety of *what* is in that request is established
upstream, in `app/ai`, and this service is where *whether and at what cost* it
runs is decided.

The provider is injectable for the same reason storage is on `ReportService`:
tests run the whole path against the deterministic echo provider with no key and
no network.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from time import perf_counter

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_actions import AuditAction
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.ai import AiJob
from app.models.user import User
from app.repositories.ai import AiJobRepository
from app.services.ai.base import (
    AIError,
    BudgetExceededError,
    CompletionProvider,
    CompletionRequest,
    CompletionResult,
)
from app.services.ai.pricing import cost_of, is_known_model
from app.services.audit import AuditService
from app.services.rbac import AuthorizationContext

logger = get_logger(__name__)


def build_provider(settings: Settings) -> CompletionProvider:
    """Resolve the configured adapter.

    Add a provider by writing an adapter and extending this match — no caller
    changes anywhere, exactly as for email and storage.
    """
    match settings.AI_PROVIDER:
        case "anthropic":
            from app.services.ai.anthropic import AnthropicCompletionProvider

            return AnthropicCompletionProvider(settings)
        case "openai_compatible":
            from app.services.ai.openai_compatible import OpenAICompatibleProvider

            return OpenAICompatibleProvider(settings)
        case "echo":
            from app.services.ai.echo import EchoCompletionProvider

            return EchoCompletionProvider()
        case unknown:  # pragma: no cover — Literal makes this unreachable
            raise ValueError(f"Unsupported AI_PROVIDER: {unknown}")


def _month_start(now: datetime) -> datetime:
    """The first instant of the current UTC month — the budget window's start."""
    return now.astimezone(UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


class AIService:
    def __init__(
        self,
        session: AsyncSession,
        auth: AuthorizationContext,
        *,
        provider: CompletionProvider | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.auth = auth
        self.settings = settings or get_settings()
        self.repo = AiJobRepository(session)
        self.audit = AuditService(session)
        self._provider = provider
        #: The ledger row the most recent `complete` produced. Exposed so a
        #: caller can link its own record (a stored assistant turn) to the cost
        #: row without the ledger having to be queried back out — added for the
        #: assistant, harmless to every other caller.
        self.last_job: AiJob | None = None

    @property
    def provider(self) -> CompletionProvider:
        """Resolved on first use, from this service's settings.

        Not the process-wide default: the service is handed its settings, and
        reaching past them to a global would make the provider a second,
        invisible input — the kind that works in the app and fails in a test for
        reasons unrelated to the test.
        """
        if self._provider is None:
            self._provider = build_provider(self.settings)
        return self._provider

    # ------------------------------------------------------------ status

    @property
    def enabled(self) -> bool:
        return self.settings.AI_ENABLED

    def status(self) -> dict[str, object]:
        """What the frontend needs to decide whether to show AI affordances."""
        return {
            "enabled": self.enabled,
            "provider": self.settings.AI_PROVIDER,
            "model": self.settings.AI_MODEL,
            "can_use": self.auth.can("ai.use"),
            "can_configure": self.auth.can("ai.configure"),
        }

    async def budget_status(self, *, now: datetime | None = None) -> dict[str, object]:
        """This tenant's month-to-date spend against its ceiling."""
        moment = now or datetime.now(UTC)
        spent = await self.repo.spend_since(
            self.auth.organization_id, _month_start(moment)
        )
        ceiling = Decimal(str(self.settings.AI_MONTHLY_COST_CEILING_USD))
        remaining = ceiling - spent if ceiling > 0 else None
        return {
            "ceiling_usd": ceiling,
            "spent_usd": spent,
            "remaining_usd": remaining,
            # None when the ceiling is disabled, so the UI shows "unlimited"
            # rather than dividing by zero into a fake percentage.
            "exhausted": bool(ceiling > 0 and spent >= ceiling),
        }

    # ---------------------------------------------------------- dispatch

    async def complete(
        self,
        request: CompletionRequest,
        *,
        feature: str,
        actor: User | None = None,
    ) -> CompletionResult:
        """Run one completion through every guard, and record it.

        Raises `AIError` (or its `BudgetExceededError` subclass) on any refusal
        or failure, so a caller never has to inspect a status field to know
        whether it got a real answer.
        """
        self.auth.require("ai.use")

        if not self.enabled:
            # A configuration state, not a transient one: fail closed and
            # permanently rather than letting a retry loop pretend it might turn
            # on.
            raise AIError("The AI layer is not enabled.", retryable=False)

        # Defence in depth on the per-call token ceiling: even if a prompt asked
        # for more, the global cap wins, so one request cannot become an
        # unbounded generation regardless of how it was built.
        capped = self._cap_output(request)

        await self._enforce_budget(feature, capped, actor)

        started = perf_counter()
        try:
            result = await self.provider.complete(capped)
        except AIError as exc:
            latency_ms = int((perf_counter() - started) * 1000)
            await self._record(
                capped, feature, actor, status="failed", latency_ms=latency_ms, error=str(exc)
            )
            await self._audit_completion(capped, feature, actor, succeeded=False)
            await self.session.flush()
            raise

        latency_ms = int((perf_counter() - started) * 1000)
        await self._record(
            capped,
            feature,
            actor,
            status="succeeded",
            usage_prompt=result.usage.prompt_tokens,
            usage_completion=result.usage.completion_tokens,
            latency_ms=latency_ms,
        )
        await self._audit_completion(capped, feature, actor, succeeded=True)
        await self.session.flush()
        return result

    # --------------------------------------------------------- internals

    def _cap_output(self, request: CompletionRequest) -> CompletionRequest:
        cap = self.settings.AI_MAX_OUTPUT_TOKENS
        if request.max_tokens <= cap:
            return request
        # Frozen dataclass — rebuild with the capped value rather than mutating.
        from dataclasses import replace

        return replace(request, max_tokens=cap)

    async def _enforce_budget(
        self, feature: str, request: CompletionRequest, actor: User | None
    ) -> None:
        ceiling = Decimal(str(self.settings.AI_MONTHLY_COST_CEILING_USD))
        if ceiling <= 0:
            # A disabled ceiling is unbounded spend, which production forbids
            # (assert_production_ready). Where it is deliberately off, there is
            # nothing to enforce.
            return

        spent = await self.repo.spend_since(
            self.auth.organization_id, _month_start(datetime.now(UTC))
        )
        if spent < ceiling:
            return

        # Refused: nothing is dispatched. Recorded so the refusal is visible in
        # the ledger, and audited high-severity so a tenant that keeps hitting
        # the ceiling is a same-day question.
        await self._record(
            request,
            feature,
            actor,
            status="refused",
            error=f"Monthly AI cost ceiling reached: ${spent} of ${ceiling}.",
        )
        await self.audit.record(
            action=AuditAction.AI_BUDGET_EXCEEDED,
            organization_id=self.auth.organization_id,
            actor_id=actor.id if actor is not None else self.auth.user_id,
            actor_email=actor.email if actor is not None else None,
            entity_type="ai",
            metadata={"feature": feature, "spent": str(spent), "ceiling": str(ceiling)},
        )
        await self.session.flush()
        logger.warning(
            "ai_budget_exceeded",
            extra={
                "organization_id": str(self.auth.organization_id),
                "feature": feature,
                "spent": str(spent),
                "ceiling": str(ceiling),
            },
        )
        raise BudgetExceededError(
            "This workspace has reached its monthly AI budget. It resets at the "
            "start of next month, or an administrator can raise the ceiling."
        )

    async def _record(
        self,
        request: CompletionRequest,
        feature: str,
        actor: User | None,
        *,
        status: str,
        usage_prompt: int = 0,
        usage_completion: int = 0,
        latency_ms: int | None = None,
        error: str | None = None,
    ) -> AiJob:
        model = request.model
        if not is_known_model(model):
            # Logged rather than swallowed: the cost is still charged at the
            # conservative unknown-model rate, but somebody should add the real
            # rate so the ceiling is accurate.
            logger.warning("ai_unknown_model_rate", extra={"model": model})

        cost = (
            cost_of(model, usage_prompt, usage_completion)
            if status == "succeeded"
            else Decimal(0)
        )

        job = AiJob(
            organization_id=self.auth.organization_id,
            user_id=actor.id if actor is not None else self.auth.user_id,
            feature=feature[:60],
            prompt_key=request.metadata.get("prompt"),
            prompt_version=_as_int(request.metadata.get("prompt_version")),
            provider=self.settings.AI_PROVIDER,
            model=model[:80],
            status=status,
            prompt_tokens=usage_prompt,
            completion_tokens=usage_completion,
            cost_usd=cost,
            latency_ms=latency_ms,
            error=error[:2000] if error else None,
        )
        self.session.add(job)
        await self.session.flush()

        # Mirror the ledger row into the metrics registry (Phase 7.4). The row
        # remains the durable, summable record of spend; this is the live signal
        # a dashboard scrapes, per tenant and per feature.
        from app.observability import metrics

        metrics.record_ai(
            feature=job.feature,
            provider=job.provider,
            model=job.model,
            status=job.status,
            organization_id=job.organization_id,
            cost_usd=float(cost),
            prompt_tokens=usage_prompt,
            completion_tokens=usage_completion,
        )

        # Exposed so a caller (the assistant) can link its stored turn to this
        # cost row. Flushed above so the id exists to link to.
        self.last_job = job
        return job

    async def _audit_completion(
        self,
        request: CompletionRequest,
        feature: str,
        actor: User | None,
        *,
        succeeded: bool,
    ) -> None:
        # The egress is the auditable act — data left for a third party — so this
        # fires whether or not the answer came back. Carries the feature and the
        # prompt version, never the prompt or its content.
        await self.audit.record(
            action=AuditAction.AI_COMPLETION,
            organization_id=self.auth.organization_id,
            actor_id=actor.id if actor is not None else self.auth.user_id,
            actor_email=actor.email if actor is not None else None,
            entity_type="ai",
            metadata={
                "feature": feature,
                "model": request.model,
                "provider": self.settings.AI_PROVIDER,
                "succeeded": succeeded,
                "prompt": request.metadata.get("prompt", ""),
            },
        )

    # ------------------------------------------------------------- usage

    async def usage_summary(
        self, *, now: datetime | None = None
    ) -> list[dict[str, object]]:
        """Spend per feature this month, for the admin/usage view."""
        self.auth.require("ai.configure")
        moment = now or datetime.now(UTC)
        rows = await self.repo.usage_by_feature(
            self.auth.organization_id, _month_start(moment)
        )
        return [
            {"feature": feature, "calls": calls, "cost_usd": cost}
            for feature, calls, cost in rows
        ]


def _as_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


__all__ = ["AIService", "build_provider"]

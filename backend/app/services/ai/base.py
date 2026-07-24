"""Provider-agnostic completion contract.

Business logic depends on `CompletionProvider` — never on the Anthropic API, an
SDK type, or a model id spelled out at a call site. Swapping provider is a new
adapter plus one environment variable, exactly as it is for email and storage.

The `Protocol` is structural, so an adapter needs no base class and a test can
pass a plain scripted fake without inheriting anything.

**Two message roles, and no `system` role among them.** The system prompt is a
separate field, not a message, because the whole security posture of this layer
(SECURITY.md §5) rests on the model being unable to confuse *instructions* with
*data*. CRM text — a lead's notes, a deal's description — is attacker-influenced
and only ever travels as a `user` message inside delimiters; the instructions
that govern the model live in `system` and nowhere a user can reach. Collapsing
the two into one list is how prompt injection stops being a bug and becomes the
design.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

#: Roles a caller may put in the conversation. Deliberately excludes "system":
#: instructions are a field, not a message a user can imitate.
ChatRole = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: ChatRole
    content: str


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool a model may ask to call, described provider-neutrally.

    Function-calling defined as data, not code, so the *definition* lives here in
    the pure base — provider adapters translate it to their own tool format, and
    the executable behaviour lives in `app/services/ai/tools.py`. Splitting the
    two is what lets a tool be declared to a model without this module importing
    anything that can touch the database.

    `parameters` is a JSON Schema object. Every provider that supports tools
    speaks JSON Schema for arguments, so it is the portable choice.
    """

    name: str
    description: str
    parameters: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A model's request to call a tool. Carried back on the result.

    A request, never an action: the model *asks*, and whether the tool runs — and
    it only ever runs read-only, under the caller's scope — is decided by the
    orchestration layer, never by the model. Model output authorising an action
    is the exact thing SECURITY.md §5 forbids.
    """

    id: str
    name: str
    arguments: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    """One completion to run.

    `system` is the instruction channel and is never derived from CRM data.
    `messages` alternate user/assistant and are where untrusted, delimited
    content lives.
    """

    system: str
    messages: list[ChatMessage]
    model: str
    #: A ceiling, not a target. Capped so a runaway generation cannot turn one
    #: request into an unbounded bill — the cost ceiling is the tenant-level
    #: guard, this is the per-call one.
    max_tokens: int
    #: Low by default at the call sites that matter: a CRM assistant that
    #: invents figures is worse than one that is terse. Each feature may raise
    #: it, but the default leans conservative.
    temperature: float = 0.2
    #: Provider-neutral labels for cost attribution and debugging. Never PII —
    #: these can land in provider-side telemetry.
    metadata: dict[str, str] = field(default_factory=dict)
    #: Tools the model may request. Empty by default, so every existing caller is
    #: unchanged — the field is the seam that lets function-calling be added
    #: without an API change, not a feature that is on yet. A provider that does
    #: not support tools ignores this and answers in text.
    tools: list[ToolSpec] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.system.strip():
            raise ValueError("A completion needs a system prompt.")
        if not self.messages:
            raise ValueError("A completion needs at least one message.")
        if self.messages[0].role != "user":
            # The Messages API requires the first turn to be the user's, and a
            # leading assistant turn is almost always a construction bug — a
            # context builder that appended in the wrong order.
            raise ValueError("The first message must be from the user.")
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be positive.")


@dataclass(frozen=True, slots=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True, slots=True)
class CompletionResult:
    text: str
    model: str
    usage: TokenUsage
    #: Why generation stopped — `end_turn`, `max_tokens`, `stop_sequence`. A
    #: `max_tokens` stop is surfaced rather than hidden: a truncated answer that
    #: looks complete is the same failure class as a truncated report.
    stop_reason: str
    provider: str
    #: Tools the model asked to call, if any. Empty on an ordinary text answer.
    #: Present so the orchestration layer can decide what to do with a request —
    #: the model does not get to act on its own.
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def truncated(self) -> bool:
        return self.stop_reason == "max_tokens"

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@dataclass(frozen=True, slots=True)
class CompletionChunk:
    """One piece of a streamed response.

    The streaming counterpart of `CompletionResult`. A chunk carries a slice of
    text as it is generated; the final chunk sets `done` and carries the usage,
    so a streamed call is accounted for exactly like a whole one — the ledger and
    the cost ceiling must not have a hole that opens the moment streaming is
    switched on.
    """

    text: str
    done: bool = False
    usage: TokenUsage | None = None
    stop_reason: str | None = None


class AIError(Exception):
    """A completion failed.

    `retryable` distinguishes a transient fault (a timeout, a 429, a 5xx) from a
    permanent one (a bad request, an auth failure), so the queue does not burn
    its retry budget on a call that can never succeed — the same contract
    `StorageError` and `NotificationError` use.
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class BudgetExceededError(AIError):
    """The tenant's cost ceiling would be crossed by this call.

    Never retryable: retrying spends money the ceiling exists to stop. Raised
    *before* dispatch, so a refused call costs nothing and records nothing with
    a provider.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


@runtime_checkable
class CompletionProvider(Protocol):
    """Implemented by every model adapter."""

    name: str

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        """Run one completion. Raises `AIError` on failure, never a vendor type."""
        ...

    async def verify_configuration(self) -> bool:
        """Cheap reachability/credential check for the readiness probe."""
        ...


@runtime_checkable
class StreamingProvider(Protocol):
    """A provider that can also stream a response token by token.

    Kept as a **separate** Protocol rather than a method on `CompletionProvider`
    so that streaming is an optional capability, not a requirement every adapter
    must implement to exist. The orchestration layer checks
    `isinstance(provider, StreamingProvider)` and falls back to `complete` when a
    provider cannot stream — which is why the API can be streaming-ready today
    with no adapter actually streaming yet, and gain it later with no contract
    change.
    """

    name: str

    def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        """Yield chunks as they arrive. The final chunk carries usage."""
        ...


__all__ = [
    "AIError",
    "BudgetExceededError",
    "ChatMessage",
    "ChatRole",
    "CompletionChunk",
    "CompletionProvider",
    "CompletionRequest",
    "CompletionResult",
    "StreamingProvider",
    "TokenUsage",
    "ToolCall",
    "ToolSpec",
]

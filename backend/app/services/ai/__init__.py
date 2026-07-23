"""AI provider adapters and the orchestrating service.

Business logic depends on `AIService`, never on a provider adapter or the
Anthropic API. `AIService.complete` is the one guarded path every completion
takes — enablement, permission, the cost ceiling before dispatch, and the ledger
all live there. See docs/AI.md and SECURITY.md §5.
"""

from app.services.ai.base import (
    AIError,
    BudgetExceededError,
    ChatMessage,
    CompletionProvider,
    CompletionRequest,
    CompletionResult,
    TokenUsage,
)
from app.services.ai.service import AIService, build_provider

__all__ = [
    "AIError",
    "AIService",
    "BudgetExceededError",
    "ChatMessage",
    "CompletionProvider",
    "CompletionRequest",
    "CompletionResult",
    "TokenUsage",
    "build_provider",
]

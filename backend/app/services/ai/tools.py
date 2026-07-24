"""Tool (function-calling) execution: the scoped, read-only half.

`ToolSpec` and `ToolCall` in `base.py` are the *data* — what a model is told it
may call, and what it asks for. This module is the *behaviour*: how a requested
call is actually run, and the rules that make running it safe.

Two rules, both from SECURITY.md §5, and both enforced here rather than trusted
to each tool:

  * **A tool is read-only.** The model never authorises a write. A tool answers
    questions about CRM data; it does not create, update, or delete anything. A
    feature that wants the model to *draft* a change produces a draft for human
    approval in its own handler — it does not do it through a tool the model can
    invoke directly.
  * **A tool runs under the caller's scope.** Every tool is handed the same
    `AuthorizationContext` the request runs under and reads through the entity's
    own scoped path, so a tool cannot become a way for the model to reach a
    record the user cannot. This is the RAG-bypasses-RBAC control applied to
    function-calling.

6.1 shipped the provider seam; 6.2 ships this registry so tools can be added
without touching the API or the provider layer. The registry is empty by default
— a tool is a deliberate, reviewed addition, not something that accretes.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai.base import ToolSpec
from app.services.rbac import AuthorizationContext


@runtime_checkable
class Tool(Protocol):
    """A read-only, scoped capability the model may request.

    `spec` is what the model is shown. `run` is what actually happens, under the
    caller's authorization — and it returns a string the model reads as an
    ordinary tool result, never an action it has taken.
    """

    spec: ToolSpec

    async def run(
        self,
        arguments: dict[str, object],
        *,
        session: AsyncSession,
        auth: AuthorizationContext,
    ) -> str: ...


class ToolRegistry:
    """The catalogue of tools the assistant may offer.

    One per name; re-registering is a programming error, the same discipline as
    the prompt and metric registries. Empty until a tool is deliberately added.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        name = tool.spec.name
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered.")
        self._tools[name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def specs(self) -> list[ToolSpec]:
        """The definitions to advertise to a model. Empty when no tool is
        registered, which is the default and keeps `CompletionRequest.tools`
        empty so nothing changes for a provider."""
        return [tool.spec for tool in self._tools.values()]

    def names(self) -> list[str]:
        return sorted(self._tools)


#: The process-wide registry. Empty in 6.2 — the seam exists so a later feature
#: can add, say, a scoped "search my leads" tool with one `register` call and no
#: change to the assistant, the provider layer, or the API contract.
TOOLS = ToolRegistry()


__all__ = ["TOOLS", "Tool", "ToolRegistry"]

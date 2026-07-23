# AI growth engine

Phase 6. The infrastructure the AI features are built on, and the security model
that governs all of them.

- [1. What the AI layer is, and what it is not allowed to be](#1-what-the-ai-layer-is-and-what-it-is-not-allowed-to-be)
- [2. The six controls](#2-the-six-controls)
- [3. The provider abstraction](#3-the-provider-abstraction)
- [4. The prompt framework](#4-the-prompt-framework)
- [5. Context builders and scope](#5-context-builders-and-scope)
- [6. Redaction](#6-redaction)
- [7. Cost accounting and the ledger](#7-cost-accounting-and-the-ledger)
- [8. Background execution](#8-background-execution)
- [9. Configuration](#9-configuration)
- [10. API](#10-api)

---

## 1. What the AI layer is, and what it is not allowed to be

The AI layer reads CRM data, sends some of it to an external model, and turns
the answer into text a person reads. That is the whole shape, and every design
decision falls out of one fact in it: **the model is a third party, and CRM data
is a mix of the customer's confidential records and attacker-influenced text.**

So the layer is built to be distrusted from both directions at once. It does not
trust the data it reads (a lead's notes could be a prompt injection), and it does
not trust the model it talks to (its output is escaped, never executed, and never
authorises an action). The security model was designed in advance in
[SECURITY.md §5](./SECURITY.md), before any of this code existed; this document
is how §5 is implemented.

Phase 6.1 ships the **infrastructure** — the provider abstraction, the prompt
framework, context builders, redaction, cost accounting and background execution.
It ships no feature that uses them. The assistant (6.2), lead and deal
intelligence (6.3–6.4), and the growth engine (6.6) fill the frameworks this
milestone builds.

---

## 2. The six controls

Each row of [SECURITY.md §5](./SECURITY.md) maps to code, and each has a test in
`tests/test_ai.py` that fails loudly if it regresses.

| Risk | Where it is contained |
| --- | --- |
| RAG bypasses RBAC | Context builders read through the caller's scope — §5 |
| Prompt injection via CRM text | Untrusted content is fenced and cannot forge the fence — §4 |
| Autonomous action | No AI-initiated writes; output is text, approval is human — §8 |
| Data egress | Redaction before egress; per-deployment opt-in — §6, §9 |
| Cost exhaustion | Ceiling enforced **before** dispatch; every call on the ledger — §7 |
| Output injection | Model output is escaped on render, never executed — frontend |

The single most important one is the fifth word of the fourth row: **before.**
The cost ceiling is checked before the request leaves, so a refused call sends
nothing and spends nothing. A ceiling checked afterwards is an invoice, not a
guard.

---

## 3. The provider abstraction

`app/services/ai/` — the same shape as email and storage. Business logic depends
on `AIService`; nothing outside the package imports a model API or spells a model
id at a call site.

```
CompletionProvider (Protocol)
  ├─ AnthropicCompletionProvider   the Messages API, over httpx
  └─ EchoCompletionProvider         deterministic, no key, no network — the default
```

The provider talks in `CompletionRequest` → `CompletionResult`. The request has a
`system` field and a list of `user`/`assistant` messages, and **there is no
`system` role among the messages**: instructions are a field, data is a message,
and the two are not interchangeable. That separation is not ergonomics, it is the
anti-injection design (§4).

`echo` is the default provider, deliberately. It never calls a model and cannot
leak a customer's data to a third party during local development or a test run —
the most serious class of AI-integration accident. Production must set a real
provider *and* opt in; `assert_production_ready` refuses `AI_ENABLED` with the
echo provider.

Anthropic is spoken to directly over httpx rather than through the vendor SDK,
for the reason the WhatsApp channel is: this application has one outbound HTTP
client and one set of timeout and error conventions, and a second SDK would bring
its own.

---

## 4. The prompt framework

`app/ai/prompts.py`. A prompt is a **system string** plus a list of **content
blocks**, and the block is where the injection defence lives.

- The system string is authored by us. It is the only place an instruction can
  live, and it never contains CRM data.
- A content block is `trusted` (text we wrote — a task, a question) or untrusted
  (CRM text). Untrusted is the **default**, because forgetting to mark customer
  data as untrusted is the dangerous mistake and the safe default makes it hard.
- Every untrusted block is wrapped in a named delimiter
  (`<untrusted:notes>…</untrusted:notes>`) and its body is escaped so a customer
  who typed a closing delimiter into a notes field cannot forge one and break
  out. A shared preamble, prepended to every system prompt in one place, tells
  the model that anything inside those tags is data it may read but never obey.

Prompts are registered, versioned objects — the same registry discipline as
metrics and report datasets — so a wording change is one edit, and the version
travels onto every job the prompt produces so a shift in output quality is
attributable.

Nothing in the framework calls a model. A template renders to a
`CompletionRequest` the provider executes, so the whole thing is pure and
testable without a key.

---

## 5. Context builders and scope

`app/ai/context.py`. This is where the flagship AI risk — retrieval returning
records the user cannot read — is contained.

The rule from [SECURITY.md §5](./SECURITY.md): retrieval filters by
`organization_id` **and** the caller's scope predicate **before** anything
reaches a model, using **the same scope resolver** the list endpoints use, never
a second one. So a concrete builder (leads in 6.3, deals in 6.4) does not query
freely — it goes through the entity's own service or repository under the
caller's `AuthorizationContext`, exactly as a request handler does. A record the
caller could not open in the UI is one the model never sees.

6.1 ships the safe primitives — `field()` (redacted, fenced), `instruction()`
(trusted, plain) — and the `ContextBuilder` base that holds the caller's scope so
a subclass has no excuse to resolve it any other way. It deliberately fetches
nothing itself: "fetch some context" with no entity and no scope is the shape of
the mistake.

---

## 6. Redaction

`app/ai/redaction.py`. Every string of CRM text passes through here before it
leaves for a model. Email addresses, phone-shaped runs, and 13+‑digit sequences
(plausibly card or account numbers) are masked.

This is **minimisation, not anonymisation**. It reduces what crosses the boundary
to the model — a lead summary reads exactly as well with the contact details
masked — and nothing downstream treats a redacted string as safe to publish.
Over-masking a false positive costs the model a little context; under-masking a
real address sends it to a third party, and the two errors are not symmetric, so
the patterns lean broad.

---

## 7. Cost accounting and the ledger

`ai_jobs` is the ledger [SECURITY.md §5](./SECURITY.md) names. One tenant-scoped,
RLS-FORCEd row per completion — succeeded, failed, or refused — recording the
feature, model, token counts, cost, latency and outcome.

**It stores no prompt and no completion text.** It is a ledger, not a transcript;
storing the rendered prompt would re-introduce the very PII redaction just kept
out of the model, in a second place with a longer life. The row keeps what
accounting and debugging need and nothing that would be a liability in a dump.

Cost comes from `pricing.py`: model → USD per million tokens, in `Decimal`
throughout because the figures sum into a monthly total a ceiling is compared
against, and a float that drifts per call drifts a tenant's budget over a month.
**An unknown model is priced high, not free** — the dangerous direction for a
budget guard is under-counting, so a model id nobody added trips the ceiling
early rather than running unbounded.

The ceiling is enforced in `AIService.complete`, before dispatch:

1. `ai.use` is required.
2. The layer must be enabled.
3. The tenant's month-to-date spend is summed. At or over the ceiling, the call
   is **refused** — a `refused` job is written, `ai.budget_exceeded` is audited
   (high-severity), and nothing is sent. Refused spend does not count toward the
   ceiling, so a wall of refusals cannot lock a tenant out on top of it.
4. The call runs; the result is recorded and the egress audited (`ai.completion`,
   whether or not the model answered — the egress is the auditable act).

---

## 8. Background execution

`app/workers/jobs/ai.py`. AI calls are slow and many are not interactive
(nightly re-scoring, batch summaries), so they belong on the ARQ queue. The job
runs one completion under a tenant's own scope and still goes through
`AIService.complete`, so the ceiling, the ledger and the audit trail hold for
background work exactly as for a request — a job that bypassed them would be a
hole in the one place the guarantees live.

**No AI-initiated writes.** The job produces text and records cost. It does not
act on what the model said. A feature that turns a completion into a *draft* for
human approval does that in its own handler; the model's output never authorises
a write on its own.

---

## 9. Configuration

```
AI_ENABLED=false                 # master switch; off by default
AI_PROVIDER=echo                 # echo | anthropic
AI_API_KEY=                      # required when provider is anthropic
AI_MODEL=claude-sonnet-5         # default; features may pick cheaper per call
AI_MONTHLY_COST_CEILING_USD=50   # per-org, enforced before dispatch; 0 disables
AI_MAX_OUTPUT_TOKENS=1024        # per-call ceiling, clamped even if a prompt asks more
```

Production gates (`assert_production_ready`), all silent-failure modes: the layer
enabled with the echo provider (answers nothing), `anthropic` without a key, and
a disabled cost ceiling (unbounded spend).

---

## 10. API

`/api/v1/ai`. 6.1 exposes only availability and cost — the endpoints that run
completions arrive with the features that need them.

| Method | Path | Permission | Returns |
| --- | --- | --- | --- |
| GET | `/status` | authenticated | enabled? provider, model, this caller's grants, budget |
| GET | `/usage` | `ai.configure` | this month's spend per feature |

`/status` is not gated on `ai.use` on purpose: a caller who cannot use AI still
needs to be told so, so the UI hides the affordance rather than showing a button
that will 403.

Related: [SECURITY.md](./SECURITY.md) §5 for the risk model this implements,
[JOBS.md](./JOBS.md) for the worker, [PERMISSIONS.md](./PERMISSIONS.md) for
`ai.use` / `ai.configure`.

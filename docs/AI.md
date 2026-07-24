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
- [11. The assistant (6.2)](#11-the-assistant-62)
- [12. Lead intelligence (6.3)](#12-lead-intelligence-63)

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

Phase 6.1 shipped the **infrastructure** — the provider abstraction, the prompt
framework, context builders, redaction, cost accounting and background execution
(§3–§10). Phase 6.2 added the **assistant** (§11); Phase 6.3 adds **lead intelligence**
(§12), the first feature to *reason about* a record. Deal and property
intelligence (6.4–6.5) and the growth engine (6.6) fill the same frameworks; none
re-establishes the security model, because it lives in the infrastructure they
all go through.

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
| GET | `/conversations` | `ai.use` | the caller's own conversations |
| POST | `/conversations` | `ai.use` | start one, optionally anchored to a record |
| GET | `/conversations/{id}` | `ai.use` (owner) | a conversation and its turns |
| DELETE | `/conversations/{id}` | `ai.use` (owner) | soft-delete it |
| POST | `/conversations/{id}/messages` | `ai.use` (owner) | send a message, get the reply |

`/status` is not gated on `ai.use` on purpose: a caller who cannot use AI still
needs to be told so, so the UI hides the affordance rather than showing a button
that will 403.

---

## 11. The assistant (6.2)

A CRM assistant — a per-user chat that answers from CRM data, within what the
user can see. It is a *composition* over the 6.1 infrastructure: it never talks
to a model, only to `AIService.complete`, so every guarantee in §2–§7 holds for
it without being re-established.

### Persistence, and what is not persisted

`ai_conversations` and `ai_messages` store the turns — the user's messages and
the assistant's replies, which are the product the user comes back to. They do
**not** store the composed prompt: the system preamble, the fenced CRM context,
the safety rules. That prompt is assembled fresh each turn, under scope, and
discarded. So a lead's notes — even redacted — never sit in the assistant tables;
`ai_jobs` records the cost, these tables record the conversation, and neither
holds the prompt. This is exactly the "audit the response without storing
unnecessary sensitive prompt data" line, made concrete.

### Two isolation boundaries

- **Organization** — RLS `ENABLE` + `FORCE` on both tables, like everything else.
- **User** — every repository query also filters by `user_id`. An assistant
  thread is private to its creator; not even a manager reads it. This is
  *stricter* than the CRM's own scope rules, deliberately, because a chat
  transcript is more revealing than the records it discusses.

### Entity-aware context

A conversation can be anchored to a record ("help me with this lead"). The anchor
is validated **under the user's scope at creation** — you cannot anchor to a
record you cannot see — and the entity's context is **rebuilt under scope at
every turn** through the same `get_*` service a request handler uses
(`context_builders.py`). Access revoked between turns simply drops the context;
the conversation carries on without leaking that the record still exists. Adding
an entity is one registry entry.

### The turn

1. The user's message is persisted and committed *first* — whatever happens to
   the reply, what they said is theirs to keep.
2. The prompt is composed: fenced, redacted entity context + the user's message
   as the latest turn, with the last dozen turns as history (a cap, not the whole
   transcript — an unbounded history is an unbounded bill).
3. It goes through `AIService.complete`: enablement, `ai.use`, the cost ceiling
   before dispatch, the ledger, the egress audit.
4. The reply is persisted, linked to its ledger row. A failure or budget refusal
   still commits the attempt (a `failed`/`refused` `ai_jobs` row), so nothing is
   silently lost.

### Streaming- and tool-ready

Both are built as seams, not yet switched on:

- **Streaming** — `CompletionChunk` and a separate `StreamingProvider` Protocol.
  A provider advertises streaming by satisfying it; the assistant would check
  `isinstance` and fall back to a whole reply otherwise. The `MessageRead` the
  API returns is the same shape a stream would resolve to, so turning streaming
  on later changes the transport, not the contract.
- **Tools** — `ToolSpec`/`ToolCall` (pure, in `base.py`) and `Tool`/`ToolRegistry`
  (executable, scoped, in `tools.py`). `CompletionRequest.tools` defaults empty,
  so nothing changes for a provider until a tool is registered. Every tool is
  **read-only and scoped** by contract — the model asks, the orchestration layer
  decides, under the caller's authorization, and no tool ever writes. The
  registry is empty in 6.2; the Anthropic adapter already maps tools both ways,
  so adding one is a `register` call and no API change.

### Provider-agnostic future

Nothing in the assistant imports a provider. Adding OpenAI, Gemini, Ollama,
OpenRouter or a self-hosted model is one adapter plus one `build_provider` arm
(§3) — the assistant, the API and the conversation model are untouched.

---

## 12. Lead intelligence (6.3)

Scoring, qualification, temperature, prioritisation, risk and missing-info
detection, and a grounded narrative — the first place the AI reasons *about* a
record rather than chatting over it. Its defining decision is the one the brief
asked for: **the scoring is deterministic, and the model only writes the
language.**

### Two layers

**A deterministic rule engine** (`app/ai/lead_scoring.py`, pure) is the whole
scoring surface. A lead's score is the sum of a fixed set of **signals** — each a
pure function of a small `LeadFeatures` set, each returning points *and the
reason it contributed*. From that sum the engine derives temperature,
qualification, priority, buying-intent, risks, missing-info and recommended
actions. There is no model, no randomness, no network: the same lead always
scores the same, and the number is always readable back as its reasons. That is
explainability by construction, not a report bolted on afterward.

**An AI narrative** (through `AIService`, on demand) turns that breakdown into
prose — a summary and phrased next steps. The model is handed the score and its
signals as trusted analysis, and the lead's own fields as fenced, redacted
context, and told to *explain* the number, never to produce one. Numbers are
rules; words are the model.

### Explainability model

Every score ships its full breakdown: `signals` (each with points and a reason),
`risks`, `missing_info`, and `recommendations` (each with the reason it was
suggested). The API returns them not as debug extras but as the score itself —
`score == clamp(Σ signals.points)` — so a client, a test, or an agent can always
answer "why this number". `top_reasons` surfaces the biggest movers, honouring
the product's existing "three signals that drove it" promise.

### Security decisions

- **The AI never writes to the lead.** The score lives in its own `lead_scores`
  table (RLS `FORCE`, one row per lead); the lead's own `score` field is the
  agent's and is never touched. This is SECURITY.md §5's "no autonomous action"
  at the level of one feature.
- **Two permission tiers.** The deterministic intelligence needs only
  `leads.view` — it is computed from CRM data with no egress and works with the
  AI layer switched off. The generative narrative needs `ai.use` and runs
  through the guarded `AIService`, so the cost ceiling, ledger and egress audit
  apply.
- **Scope, by reuse.** Scoring fetches the lead through `LeadService.get_lead`,
  which 404s a lead the caller cannot see; prioritisation ranks stored scores
  joined to leads under the same `leads.view` scope the list uses. A stored
  score is never a way to see a lead the caller could not.

### Scalability

Scores are persisted so prioritisation ranks from an indexed table rather than
recomputing a book of leads on every request. Freshness comes from the queue:
computed on read (deterministic, so a read-write is a safe cache), and refreshed
by a nightly `rescore_leads` sweep — which, because it is pure rules, costs
nothing and cannot fail on a provider outage. This is the "rescore on activity
via queue, not on read" the roadmap called for, generalised.

### Future ML integration

The engine sits behind a `LeadScorer` protocol: `score(features) → LeadScore`. An
ML model is another implementation of the same protocol, producing the same
explainable result, swapped in by replacing one module-level `DEFAULT_SCORER`.
The service, the API, the schemas and the `lead_scores` table are all
model-agnostic — the `scorer` field on every row and response says which engine
ran, so a rules-to-model rollout is legible in the data. Because the protocol
returns an *explanation*, not a bare float, "explainable" survives the switch to
a model that would otherwise be a black box.

### API

| Method | Path | Permission | Returns |
| --- | --- | --- | --- |
| GET | `/ai/leads/prioritized` | `leads.view` | ranked shortlist with top reasons |
| GET | `/ai/leads/{id}/score` | `leads.view` | the full explainable score |
| GET | `/ai/leads/{id}/insights` | `leads.view` + `ai.use` | score + grounded narrative |

Related: [SECURITY.md](./SECURITY.md) §5 for the risk model this implements,
[JOBS.md](./JOBS.md) for the worker, [PERMISSIONS.md](./PERMISSIONS.md) for
`ai.use` / `ai.configure`.

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
- [13. Deal intelligence (6.4)](#13-deal-intelligence-64)
- [14. Property intelligence (6.5)](#14-property-intelligence-65)
- [15. Growth intelligence (6.6)](#15-growth-intelligence-66)

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
(§3–§10). Phase 6.2 added the **assistant** (§11); 6.3 **lead intelligence** (§12), 6.4
**deal intelligence** (§13), 6.5 **property intelligence** (§14) and 6.6 **growth
intelligence** (§15) — the features that reason about a record, and finally about
the whole business. None re-establishes the security model, because it lives in
the infrastructure they all go through.

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

---

## 13. Deal intelligence (6.4)

The same two-layer pattern as lead intelligence, applied to deals — health,
win probability, stalled and risk detection, next best action, forecast
contribution, and a grounded narrative — with one integration the brief named
explicitly: **pipeline statistics come from the Analytics Engine, not a second
copy of the logic.**

### Two explainable numbers

The deterministic engine (`app/ai/deal_scoring.py`, pure) produces both:

- **health** (0-100): the sum of a signal registry — stage progression,
  momentum, time-in-stage, close date, value, priority — each with its reason.
- **win probability** (0-100): the deal's *own stage probability, adjusted* by
  stated ± factors (stalled −15, overdue −15, gone quiet −10, closing soon +5).
  Adjusting a baseline the pipeline already assigned, rather than producing a
  number from nowhere, is what makes the probability defensible: every point of
  difference from the stage default has a cause on the record. `forecast_value`
  is the deal's weighted contribution, `value × win probability`.

### The Analytics Engine reuse

Stalled detection and the time-in-stage signal do not invent a threshold. They
compare a deal's days-in-current-stage against the pipeline's **mean-time-in-
stage from `AnalyticsRepository.stage_velocity`** — the same statistic the
analytics dashboards render. A deal is stalled when it sits past twice its
stage's own norm; "fast stage, quick to stall; slow stage, patient" falls out of
reusing the analytics number rather than hard-coding one. The service reads the
whole per-stage velocity map in one call, so scoring a book of deals reuses one
analytics read, not one per deal.

### Same security posture as 6.3

- **The AI never writes to the deal.** Health lives in `deal_scores` (RLS
  `FORCE`, one row per deal); the deal's own `probability` is the agent's and is
  never overwritten — asserted by a test.
- **Two permission tiers.** Deterministic health and the at-risk ranking need
  only `deals.view`; the narrative needs `ai.use` through the guarded
  `AIService`.
- **Scope by reuse.** Scoring goes through `DealService.get_deal`; the at-risk
  ranking joins stored scores to deals under the same `deals.view` scope.

### Shared explainability

The four explanation primitives (`ScoredSignal`, `RiskFlag`, `MissingField`,
`Recommendation`) were lifted into `app/ai/explain.py` and are shared by lead and
deal scoring — re-exported from `lead_scoring` so 6.3 is untouched. The
explainability *model* is now one shape across features, not re-invented per one.

### Future ML integration

A `DealScorer` protocol, exactly like `LeadScorer`: `score(features) → DealHealth`.
An ML model is one implementation swapped at `DEFAULT_SCORER`, with the API,
schemas and `deal_scores` table untouched and the `scorer` field marking which
engine ran. The protocol returns an explanation, not a bare probability, so
explainability survives the switch.

### API

| Method | Path | Permission | Returns |
| --- | --- | --- | --- |
| GET | `/ai/deals/at-risk` | `deals.view` | lowest-health open deals, worst first |
| GET | `/ai/deals/{id}/health` | `deals.view` | health + win probability, explained |
| GET | `/ai/deals/{id}/insights` | `deals.view` + `ai.use` | health + grounded narrative |

Related: [SECURITY.md](./SECURITY.md) §5 for the risk model this implements,
[ANALYTICS.md](./ANALYTICS.md) for the stage velocity deal intelligence reuses,
[JOBS.md](./JOBS.md) for the worker, [PERMISSIONS.md](./PERMISSIONS.md) for
`ai.use` / `ai.configure`.

## 14. Property intelligence (6.5)

The same two-layer pattern again, applied to listings — a quality score, a
completeness reading, a pricing insight, strengths and weaknesses, missing-info
and marketing recommendations, plus generated listing copy — adapted to the one
thing properties do differently: **listings are shared inventory** (`properties.
view` is org-wide; the scope anchor is the listing agent, not an owner).

### Two deterministic numbers, and a market-relative third

The deterministic engine (`app/ai/property_scoring.py`, pure) produces:

- **quality** (0-100): the clamped sum of a signal registry — description depth,
  features, core specifications, price presence, year built, mappable location,
  MLS syndication-readiness — each with its reason.
- **completeness** (0-100%): a flatter, separate reading, the fraction of a fixed
  listing checklist that is filled. Quality weights; completeness counts. A
  listing can be complete but thin, or rich but missing a fact, and the two
  numbers say different things.
- **pricing insight**: a stance (`above` / `below` / `in_line` / `unknown`)
  against the market's comparable median, with the benchmark and sample named in
  the reason — "priced 18% above the $264/sqft median across 24 comparable
  listings", never a bare assertion.

**Strengths and weaknesses read straight off the signals** — a signal that helped
is a strength, one that hurt or contributed nothing is a weakness, plus the
pricing stance. No second opinion: the same facts as the score, split by sign.

### The Analytics Engine reuse

The pricing insight and the staleness read do not recompute market statistics.
They come from the **Analytics Engine**: `AnalyticsRepository.price_benchmarks`
(median price-per-square-foot per property type, computed in one database pass
with `percentile_cont`) and the existing `average_days_on_market`. Both enter the
engine as *features*, read **once per tenant** — the property analog of the deal
engine's velocity map — so scoring a whole book of listings reuses two analytics
reads, not two per listing. A fair price benchmark is the whole market's, so the
comps are org-wide, which is also exactly the visibility a shared-inventory
listing already has.

### Same security posture as 6.3 / 6.4

- **The AI never writes to the listing.** Quality lives in `property_scores` (RLS
  `FORCE`, one row per property); the listing's own fields are the agent's and are
  never overwritten — asserted by a test.
- **Two permission tiers.** Deterministic quality and the needs-attention ranking
  need only `properties.view`; the generated content needs `ai.use` through the
  guarded `AIService`.
- **Scope by reuse.** Scoring goes through `PropertyService.get_property` (which
  applies the shared-inventory scope); the needs-attention ranking joins stored
  scores to properties under the same `properties.view` scope.
- **Grounded, un-inventable copy.** The three content prompts (summary,
  description, SEO) are handed the listing's fenced, redacted context plus the
  CRM's own strengths, and told first and foremost not to invent facts — a
  fabricated "renovated kitchen" is a misrepresentation and fair-housing risk, not
  a nicety. Each kind is a separate, individually-priced `AIService` call.

### Future ML integration

A `PropertyScorer` protocol, exactly like `LeadScorer` and `DealScorer`:
`score(features) → PropertyQuality`. An ML model — say a learned price or
photo-quality model — is one implementation swapped at `DEFAULT_SCORER`, with the
API, schemas and `property_scores` table untouched and the `scorer` field marking
which engine ran. The market statistics enter as features, so a richer comp model
is a change in the Analytics Engine, not in the public API.

### API

| Method | Path | Permission | Returns |
| --- | --- | --- | --- |
| GET | `/ai/properties/needs-attention` | `properties.view` | lowest-quality active listings, worst first |
| GET | `/ai/properties/{id}/quality` | `properties.view` | quality, completeness, pricing, explained |
| GET | `/ai/properties/{id}/content?kind=` | `properties.view` + `ai.use` | generated summary / description / SEO + quality |

Related: [SECURITY.md](./SECURITY.md) §5 for the risk model this implements,
[ANALYTICS.md](./ANALYTICS.md) for the price benchmarks and days-on-market this
reuses, [JOBS.md](./JOBS.md) for the worker, [PERMISSIONS.md](./PERMISSIONS.md)
for `ai.use` and the shared-inventory `properties.*` scopes.

## 15. Growth intelligence (6.6)

The last member of the family, and the only one that scores the *business*
rather than a record. Where lead, deal and property intelligence each reason
about one row, growth intelligence reasons over the whole funnel at once — a
single business-health score for a workspace, with revenue signals, pipeline
insights, risks and recommendations.

### One explainable score, from aggregates

The deterministic engine (`app/ai/growth_scoring.py`, pure) produces a **growth
score** (0-100) that is the clamped sum of a signal registry spanning lead
conversion, win rate, revenue trend, pipeline coverage, sales cycle, activity,
deal flow and task hygiene — each signal carrying its reason. Alongside it,
**revenue signals** and **pipeline insights** are explainable statements (the
org-level analog of a property's strengths and weaknesses), and the shared
`RiskFlag` / `Recommendation` primitives carry the rest.

### Total Analytics Engine reuse

This is the module where the "do not duplicate analytics calculations" rule is
absolute: the engine computes **no metric of its own**. `GrowthIntelligence
Service` fills the features from `AnalyticsService.kpis` — the same
scope-resolved, previous-period-compared metrics the dashboards render — plus the
pipeline breakdown (`pipeline_by_stage` and `stage_velocity`). Every number is
read; none is recomputed. Because the features come from `AnalyticsService`,
scope is inherited for free: each metric is already resolved under its own entity
grant, so a manager with a team scope is scored on their team and an agent on
their own, exactly as the dashboards behave.

### Security posture

- **The AI never writes CRM data.** The score lives in `growth_scores` (RLS
  `FORCE`), **one canonical org-wide row per tenant**. That row is written only
  for an organization-wide computation — the nightly job, or a caller who holds
  ALL scope on every entity. A scoped, partial view is returned live and never
  overwrites the canonical row.
- **Two permission tiers.** The deterministic read needs `reports.view` (the same
  grant the analytics endpoints use); the briefing needs `ai.use` through the
  guarded `AIService`.
- **No fenced context, because there is none.** A growth briefing works from
  derived aggregates only — never a raw customer record — so the whole prompt is
  trusted, CRM-authored analysis, and nothing crosses the untrusted boundary.

### Future ML integration

A `GrowthScorer` protocol, exactly like the others: `score(features) →
GrowthHealth`. A learned business-health model swaps in at `DEFAULT_SCORER`, with
the API, schemas and `growth_scores` table untouched. The features are analytics
aggregates, so a richer signal set is a change in the Analytics Engine feeding
the same protocol.

### API

| Method | Path | Permission | Returns |
| --- | --- | --- | --- |
| GET | `/ai/growth` | `reports.view` | growth score, revenue signals, pipeline insights, explained |
| GET | `/ai/growth/briefing` | `reports.view` + `ai.use` | growth read + grounded AI briefing |

Both accept the analytics period parameters (`period`, `start`, `end`), so growth
can be read for any window without a second convention.

Related: [ANALYTICS.md](./ANALYTICS.md) for the KPIs and pipeline breakdown this
is built on, [SECURITY.md](./SECURITY.md) §5, [JOBS.md](./JOBS.md) for the
nightly `recompute_growth` worker.

# Vantage CRM — Database Design

PostgreSQL 16+ · extensions: `pgcrypto`, `citext`, `pg_trgm`, `pgvector` (Phase 5)

> **Status.** Phase 1 shipped `organizations`, `users`, `refresh_tokens`,
> `permissions`, `roles`, `role_permissions`, `user_roles`, `teams`,
> `team_members` and `audit_logs`. Phase 2 has since shipped `leads`, `clients`
> and `properties`. The remaining CRM entity tables below (deals, …) are still
> design.
>
> Migrations, in order:
> `a1b2c3d4e5f6` extensions → `abdb194064d6` auth → `c3d5e7f9a1b2` RLS →
> `d7305fe801ac` RBAC → `bea0a00f5c8a` audit → `6816eeddf00a` leads →
> `e4f1a2b3c5d6` clients + conversion → `f7a2b8c1d3e4` `contacts.assign` →
> `b8c3d5e7f2a1` properties → `c9d4e6f8a3b2` `properties.assign`.
>
> **Deviation from the design below.** `clients.first_name` and `last_name` are
> nullable, not NOT NULL: a client may be a company (an LLC, a trust, an
> investment entity) rather than a person. `ck_clients_identity` requires either
> a full person name or a company name, so "has a usable identity" is still
> guaranteed — it is just not guaranteed by nullability.
>
> `clients.source_lead_id` additionally carries a **partial UNIQUE index**
> (`WHERE source_lead_id IS NOT NULL`). That is what makes lead conversion
> one-shot under concurrency; a service-layer check alone is a check-then-act
> race. `leads` gained the matching `converted_client_id` and `converted_at`.
>
> **`properties` deviates in three ways.** The scope anchor is
> `listing_agent_id` as designed below — *not* `owner_id` as on every other
> entity — because on a property "owner" means the party who owns the real
> estate, which is the separate `client_id` (the seller, added in 2.5 and not
> in the original design).
>
> `days_on_market` is **not a column**. It is derived from `listed_at` in the
> model, and freezes once a listing is sold. A stored counter is correct on the
> day it is written and wrong every day after, so it would need a nightly job
> whose only purpose is to fix a number arithmetic already gives for free.
>
> `mls_number` carries a **partial UNIQUE index** per organization
> (`WHERE mls_number IS NOT NULL AND deleted_at IS NULL`). MLS numbers are
> unique within a market, not globally; the `deleted_at` clause means a
> withdrawn listing does not block re-listing the same property.

---

## 1. Conventions

| Rule | Reason |
|---|---|
| **UUIDv7 primary keys**, generated application-side | Time-sortable (index locality, unlike v4) and non-enumerable (unlike serial). Sequential integer IDs leak record counts and invite enumeration. |
| `organization_id UUID NOT NULL` on every business table | Multi-tenancy from day one (D1). MVP has one row in `organizations`. |
| `TIMESTAMPTZ` always, never `TIMESTAMP` | Naive timestamps are a correctness bug waiting for the first DST boundary. |
| `NUMERIC(14,2)` for money + explicit `currency CHAR(3)` | Floats must never touch money. |
| Soft delete via `deleted_at TIMESTAMPTZ` | CRM users expect undo; compliance expects retention. Partial indexes exclude deleted rows. |
| Audit columns: `created_at, updated_at, created_by, updated_by` | |
| **No native `ENUM` types** — `TEXT` + `CHECK`, or a lookup table | Altering a PG enum requires locks and migration gymnastics. |
| `custom_fields JSONB` on core entities | Per-org extensibility without schema churn. GIN-indexed. |

---

## 2. Tenancy and row-level security

This is the mechanism that makes D1 free rather than expensive.

```sql
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE leads FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON leads
  USING      (organization_id = current_setting('app.current_org')::uuid)
  WITH CHECK (organization_id = current_setting('app.current_org')::uuid);
```

Applied to every tenant-scoped table. The API sets the context per transaction:

```sql
SET LOCAL app.current_org = '<uuid>';   -- transaction-scoped, pool-safe
```

**Implemented in migration `c3d5e7f9a1b2`.** The DDL lives in
`backend/app/db/sql_objects.py`, shared by the migration and the test suite —
tests build their schema from ORM metadata, which carries no policies, so
duplicating the SQL would let the two drift and an isolation test would pass
while proving nothing.

**Three non-negotiable constraints:**

1. The application database role **must not** hold `BYPASSRLS`, and must not be the table owner (owners bypass RLS unless `FORCE` is set — hence `FORCE ROW LEVEL SECURITY` above).
2. Migrations run as a **separate privileged role**, never the application role.
3. `SET LOCAL` (not `SET`) — session-scoped context on a pooled connection leaks one tenant's scope into another tenant's request. This is the single most dangerous bug class in this design.

**A fourth, learned the hard way:** `FORCE ROW LEVEL SECURITY` subjects even
the table owner to policies, so a `SECURITY DEFINER` function owned by the
migration role returns zero rows. The login bootstrap functions must be owned
by `vantage_auth` (`NOLOGIN`, `BYPASSRLS`, owner of nothing else) or **every
login fails** while the code looks correct. See
[AUTHENTICATION.md §5](./AUTHENTICATION.md).

RLS is defence in depth, not the only defence: repositories also filter explicitly. Either layer failing alone is not sufficient to leak data.

---

## 3. Identity and access

```
organizations
  id, name, slug, plan, settings JSONB, created_at, updated_at
  -- MVP: exactly one row

users
  id, organization_id, email CITEXT, password_hash, full_name,
  avatar_hue SMALLINT, phone, job_title, status,
  last_login_at, password_changed_at,
  failed_login_count, locked_until,
  mfa_secret, mfa_enabled BOOLEAN,
  created_at, updated_at, deleted_at
  UNIQUE (organization_id, email)   -- email unique per org, not globally

roles
  id, organization_id NULL, key, name, description, is_system BOOLEAN
  -- organization_id NULL = built-in system role, shared by all orgs

permissions
  id, key, resource, action, description
  -- e.g. key='lead:update', resource='lead', action='update'

role_permissions
  role_id, permission_id, scope    -- scope: 'own' | 'team' | 'all'
  PRIMARY KEY (role_id, permission_id)

user_roles
  user_id, role_id, granted_by, granted_at
  PRIMARY KEY (user_id, role_id)

teams
  id, organization_id, name, lead_user_id, created_at

team_members
  team_id, user_id, role_in_team
  PRIMARY KEY (team_id, user_id)
```

`avatar_hue` is retained deliberately — the existing frontend generates avatars from a stored hue rather than serving image assets. Keeping the column means zero component changes.

### Session tables

```
refresh_tokens
  id, user_id, family_id, token_hash,          -- SHA-256, never the raw token
  issued_at, expires_at, used_at, revoked_at,
  replaced_by_id, ip INET, user_agent TEXT
  INDEX (family_id), INDEX (user_id, expires_at)
```

Rotation and reuse detection are described in [SECURITY.md §3](./SECURITY.md).

---

## 4. CRM core

```
leads
  id, organization_id, owner_id → users,
  first_name, last_name, email CITEXT, phone,
  source, stage, temperature, status,
  budget_min NUMERIC(14,2), budget_max NUMERIC(14,2), currency,
  preferred_location, notes TEXT,
  score SMALLINT, score_updated_at,           -- Phase 5 writes here
  converted_client_id → clients NULL,
  converted_at, last_contacted_at,
  custom_fields JSONB, tags TEXT[],
  search_vector tsvector GENERATED ALWAYS AS (...) STORED,
  created_at, updated_at, created_by, updated_by, deleted_at

clients
  id, organization_id, owner_id,
  type, status, first_name, last_name, company_name,
  email, phone, address JSONB,
  lifetime_value NUMERIC(14,2), currency,
  source_lead_id → leads NULL, client_since DATE,
  custom_fields JSONB, tags TEXT[], search_vector,
  <audit columns>

properties
  id, organization_id, listing_agent_id → users,
  mls_number, title, status, property_type,
  address_line1, address_line2, city, state, postal_code, country,
  latitude NUMERIC(9,6), longitude NUMERIC(9,6),
  price NUMERIC(14,2), currency,
  bedrooms SMALLINT, bathrooms NUMERIC(3,1),
  square_feet INT, lot_size_sqft INT, year_built SMALLINT,
  listed_at, days_on_market INT, view_count INT, save_count INT,
  description TEXT, features JSONB, custom_fields JSONB,
  search_vector, <audit columns>

pipelines
  id, organization_id, name, is_default BOOLEAN

pipeline_stages
  id, pipeline_id, key, name, position SMALLINT,
  default_probability SMALLINT, is_won BOOLEAN, is_lost BOOLEAN
  -- A TABLE, not an enum: every brokerage customises its pipeline.

deals
  id, organization_id, pipeline_id, stage_id → pipeline_stages,
  owner_id, client_id → clients, property_id → properties NULL,
  title, value NUMERIC(14,2), currency,
  commission_amount NUMERIC(14,2), commission_rate NUMERIC(5,4),
  probability SMALLINT, priority,
  expected_close_date DATE, actual_close_date DATE,
  lost_reason TEXT, custom_fields JSONB,
  <audit columns>

deal_stage_history
  id, deal_id, from_stage_id, to_stage_id, changed_by, changed_at,
  duration_in_stage INTERVAL
  -- Powers cycle-time analytics and is training data for Phase 5.
```

`pipeline_stages` as a table is a genuine domain requirement, not over-engineering: the prototype hard-codes six stages, but brokerages reconfigure pipelines constantly, and an enum makes that a migration every time.

### Activity, tasks, documents, notifications

```
activities                      -- user-facing business timeline
  id, organization_id, actor_id,
  entity_type TEXT, entity_id UUID,          -- polymorphic
  type,                                       -- call|email|meeting|note|showing|stage_change
  subject, body TEXT, occurred_at,
  metadata JSONB, created_at
  INDEX (organization_id, entity_type, entity_id, occurred_at DESC)

tasks
  id, organization_id, assignee_id, created_by,
  title, description, status, priority, due_at, completed_at,
  entity_type, entity_id,                     -- optional link
  <audit columns>

documents
  id, organization_id, uploaded_by,
  entity_type, entity_id,
  filename, storage_key,                      -- S3 object key, never a URL
  mime_type, size_bytes BIGINT, checksum_sha256,
  category, status,                           -- draft|awaiting_signature|signed|expired
  expires_at, scan_status,                    -- pending|clean|infected
  version INT, replaces_id → documents NULL,
  <audit columns>

notifications
  id, organization_id, user_id, category,
  title, body, entity_type, entity_id,
  read_at, created_at
  INDEX (user_id, read_at) WHERE read_at IS NULL
```

`documents.storage_key` stores the object key only. URLs are minted on demand as short-TTL presigned links — a stored URL is a permanent unauthenticated handle to a private document.

---

## 5. Audit log

**Separate from `activities`, deliberately.** They have different consumers, retention, and access rules, and conflating them is a common and consequential mistake.

| | `activities` | `audit_logs` |
|---|---|---|
| Audience | Agents (timeline UI) | Security / compliance |
| Content | "Logged a call with Harper" | Field-level before/after, IP, user agent |
| Mutable | Yes (edit a note) | **Never** |
| Retention | Business lifetime | Compliance-defined |

```
audit_logs
  id, organization_id, actor_id NULL, actor_email TEXT,   -- denormalised: survives user deletion
  action,                              -- create|update|delete|login|login_failed|export|permission_change
  entity_type, entity_id,
  changes JSONB,                       -- {field: {old, new}}, secrets redacted
  ip INET, user_agent TEXT, request_id UUID,
  created_at
  INDEX (organization_id, created_at DESC)
  INDEX (organization_id, entity_type, entity_id)
```

Enforced append-only at the **grant** level (migration `bea0a00f5c8a`): the
application role holds `INSERT` and `SELECT` on this table, and no `UPDATE` or
`DELETE`. An application bug — or an attacker holding the app's database
credentials — therefore cannot rewrite history.

Verified against the running database: both `UPDATE` and `DELETE` as
`vantage_app` return `permission denied for table audit_logs`.

Partitioned monthly by `created_at` once volume justifies it.

---

## 6. Search

PostgreSQL full-text is sufficient at this scale — no Elasticsearch.

- `search_vector` generated columns on `leads`, `clients`, `properties`, GIN-indexed
- `pg_trgm` GIN indexes for fuzzy name matching and typo tolerance
- Composite indexes on the real access patterns: `(organization_id, owner_id, stage, created_at DESC)`

Revisit only if p95 search latency exceeds budget on production data volume.

---

## 7. AI-readiness (reserved, built in Phase 5)

Designed now so Phase 5 adds tables rather than altering hot ones.

```
entity_embeddings
  id, organization_id, entity_type, entity_id,
  embedding vector(1536), model TEXT, content_hash TEXT,
  created_at
  INDEX USING hnsw (embedding vector_cosine_ops)
  UNIQUE (entity_type, entity_id, model)

lead_scores
  id, organization_id, lead_id, score SMALLINT,
  model_version TEXT,
  features JSONB,        -- inputs, for reproducibility
  explanation JSONB,     -- top contributing signals
  created_at
  -- Append-only: score history is the training signal.

ai_jobs
  id, organization_id, requested_by, job_type, status,
  input JSONB, output JSONB, error TEXT,
  model, prompt_tokens INT, completion_tokens INT, cost_usd NUMERIC(10,6),
  created_at, started_at, completed_at

ai_generated_content
  id, organization_id, entity_type, entity_id,
  content_type,                        -- email|sms|listing_description|summary
  content TEXT, model, prompt_version,
  status,                              -- draft|approved|rejected|sent
  reviewed_by, reviewed_at, created_at
  -- Nothing generated is ever sent without a human transition to 'approved'.
```

`lead_scores.explanation` exists because the current login page already promises *"every score comes with the three signals that drove it."* The schema keeps that product commitment honest rather than retrofitting explainability later.

`ai_jobs` tracks token cost per job from the first day — AI spend is impossible to attribute retroactively.

---

## 8. Migration policy

- Every change ships as an Alembic revision. No manual DDL in any environment.
- Migrations are **forward-only** in production; a mistake is corrected by a new revision.
- Expand → migrate → contract for breaking changes: add nullable column, backfill in a job, switch reads, drop old column in a later release. Never a blocking rewrite on a large table.
- `CREATE INDEX CONCURRENTLY` for indexes on populated tables.
- Every migration is tested against a restored production-shaped dump before release.

# Permission System

Implemented in Phase 1.3. Source of truth: `backend/app/core/permissions.py`.

---

## 1. Two dimensions, evaluated independently

```
CAN this actor perform this action?   →  permission check
WHICH records may they touch?         →  scope  (own | team | all)
```

Collapsing these into one check is the usual RBAC mistake. It forces a role
explosion — `agent_own_leads`, `manager_team_leads`, `admin_all_leads` — where
a 2×N matrix suffices, and every new resource multiplies the role count.

### The clearest example

The **agent** role holds:

```
properties.view    = ALL     ← listings are shared inventory
properties.manage  = OWN     ← only the listing agent edits
```

Same resource, same role, two different reaches. This is impossible to express
if reach is baked into the permission key. Test:
`test_agent_scope_is_asymmetric`.

---

## 2. Permission keys

Format `resource.action`. Enforced by a database CHECK constraint
(`key = resource || '.' || action`), so a malformed key cannot be inserted.

`view` is read; `manage` is create/update/delete. Splitting every verb produces
a matrix nobody can configure in a UI. Genuinely distinct high-risk actions get
their own key: `deals.approve`, `documents.sign`, `reports.export`.

**30 permissions across 14 resources:**

```
leads.view       leads.manage      leads.assign
contacts.view    contacts.manage   contacts.assign
properties.view  properties.manage
deals.view       deals.manage      deals.approve
tasks.view       tasks.manage
activities.view  activities.manage
documents.view   documents.manage  documents.sign
reports.view     reports.export
users.view       users.manage
roles.view       roles.manage
settings.view    settings.manage
audit.view
billing.manage
ai.use           ai.configure        ← declared now; used in Phase 5
```

AI permissions exist already so Phase 5 does not require re-seeding roles.

---

## 3. System roles

| Role | Reach | Notes |
| --- | --- | --- |
| `owner` | everything at ALL | Protected: cannot remove one's own owner role |
| `admin` | everything except `billing.manage` | The single difference from owner |
| `manager` | agent's grants, widened to TEAM | Adds `leads.assign`, `contacts.assign`, `deals.approve`, `documents.sign`, `reports.export` |
| `agent` | own book of business | `properties.view` at ALL — shared inventory |

Seeded idempotently by migration `d7305fe801ac` from the Python registry. Grants
are replaced wholesale on each run, so removing a grant in code actually removes
it in the database.

### Custom roles

`roles.organization_id IS NULL` marks a built-in role shared by every workspace;
a non-null value is a workspace's own role. **Roles are rows, not code** — so
custom roles become a UI feature over `role_permissions`, not a deployment.

---

## 4. Scope resolution

Scope becomes a **SQL predicate**, never a post-fetch filter. Post-filtering
returns wrong pagination totals, leaks the existence of records through counts,
and degrades linearly with table size.

```python
scope = auth.require("leads.view")            # raises 403 if not held
owners = await rbac.owner_ids_for_scope(auth, scope)

# owners is None for ALL → no predicate at all, rather than an
# IN clause listing every user in the organization.
if owners is not None:
    query = query.where(Lead.owner_id.in_(owners))
```

| Scope | Resolves to |
| --- | --- |
| `OWN` | `[user_id]` |
| `TEAM` | every user sharing a team, **always including the user** |
| `ALL` | `None` — no restriction beyond RLS |

TEAM falls back to `[user_id]` when the user is on no team. A manager with no
team must see their own records, not nothing.

TEAM never crosses a tenant: team membership is itself organization-scoped and
covered by RLS. Test: `test_team_scope_does_not_cross_tenants`.

---

## 5. Multiple roles union to the widest scope

A user holding both `agent` (leads.view = OWN) and `manager` (leads.view = TEAM)
gets **TEAM**.

Union, not intersection: adding a role must never *reduce* someone's access,
because nobody expects that and it produces baffling support tickets.

---

## 6. Usage

```python
# Gate an endpoint
@router.get("/leads", dependencies=[Depends(require("leads.view"))])

# Gate and use the scope
async def list_leads(
    auth: Annotated[AuthorizationContext, Depends(require("leads.view"))],
):
    scope = auth.scope_for("leads.view")
```

`require()` **raises** rather than returning a bool. A `if can(...)` whose
result nobody reads is the classic broken-access-control bug; raising makes it
impossible to forget.

Denials are logged with the actor, the permission and their roles — that is the
signal that reveals someone probing.

---

## 7. Four enforcement layers

| Layer | Enforces | If it alone failed |
| --- | --- | --- |
| Next.js middleware | route reachability | cosmetic — API still refuses |
| FastAPI `require()` | permission | blocked at repository / RLS |
| Repository | scope predicate | blocked at RLS |
| PostgreSQL RLS | tenant isolation | **last line — must never be the only one** |

The UI additionally hides navigation the user cannot act on, driven by the
`permissions` array from `/auth/me`. **This is UX, not a control.** Every
action is authorized server-side regardless of what the client renders.

---

## 8. Caching

Resolved permissions are cached in Redis for 300s, keyed by user.

- Invalidated on every role assignment or revocation.
- **Cache failure never denies access** — it falls through to the database.
  An authorization system that fails closed on a cache outage is an outage.
- The TTL bounds a missed invalidation: stale access self-corrects within five
  minutes rather than persisting indefinitely.

---

## 9. Registry validation

`validate_registry()` runs at import. A role granting a permission that does
not exist is a **startup error**, not a silently missing capability — the
latter looks like a bug in the authorization layer and is painful to diagnose.

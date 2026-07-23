# Reporting

Phase 5.3. Custom reports, saved reports, filters and grouping, CSV/XLSX/PDF
export, scheduled delivery and run history.

- [1. The security model](#1-the-security-model)
- [2. The dataset registry](#2-the-dataset-registry)
- [3. Specifications](#3-specifications)
- [4. Preview vs. export](#4-preview-vs-export)
- [5. Export formats](#5-export-formats)
- [6. Saved reports and sharing](#6-saved-reports-and-sharing)
- [7. Scheduling](#7-scheduling)
- [8. Run history and retention](#8-run-history-and-retention)
- [9. API](#9-api)
- [10. Operations](#10-operations)

---

## 1. The security model

**There is no user SQL, anywhere.** A report is a *specification*: a dataset
key, a list of field keys, and a list of `(field, operator, value)` filters.
Every name is resolved against a fixed registry before a query is constructed. A
name that is not in the registry does not become a slow query or an error
message with a table name in it — it is rejected at validation with the list of
names that are valid.

This is the whole design, not a hardening pass on top of it. The alternatives
all converge: a filter that accepts a column name eventually accepts
`id) OR (1=1`, and a report builder that accepts an expression is a SQL console
with a nicer font. RLS would still hold the tenant line, but scope, soft deletes
and column-level exposure would not.

Three properties follow, each with a test that fails loudly if it regresses:

| Property | Where it lives |
| --- | --- |
| No field name reaches SQL unvalidated | `reporting/datasets.py` + `resolve_spec` |
| Rows follow the **runner's** scope, never the author's | `ReportService._owner_ids` |
| Sensitive fields are opt-in per report | `FieldDefinition.sensitive` |

**Scope.** Reporting reuses each dataset's own CRM grant — the same rule
analytics follows ([ANALYTICS.md §2](ANALYTICS.md#2-scope-a-metric-is-readable-exactly-when-its-entity-is)).
A report over deals returns exactly the deals the caller could list at `/deals`.
`reports.view` gates the reporting surface; the dataset's permission gates the
rows. Both are required, and a dataset the caller cannot query is omitted from
the catalogue rather than offered and then refused.

**Sensitive fields.** Email and phone are declared `sensitive` and excluded
unless the specification opts in. A report is a file that leaves the building —
emailed, printed, left in a downloads folder — so a column being visible in the
UI is not on its own an argument for putting it in a spreadsheet.

---

## 2. The dataset registry

Six datasets: leads, clients, properties, deals, tasks, activities. Each
declares its model, the permission that gates it, the column scope applies to,
and its fields.

Two asymmetries are inherited from the CRM rather than reinvented:

- **Tasks anchor on `assignee_id`**, not an owner — work belongs to whoever must
  do it.
- **Properties anchor on `listing_agent_id`** — listings are shared inventory
  that one agent is responsible for.

Each `FieldDefinition` carries its type, whether it is `groupable` (false for
anything high-cardinality enough that grouping produces one group per row),
whether it is `aggregatable` (numeric only), and whether it is `sensitive`.

`validate_registry()` runs at import and refuses a registry that would produce a
broken report — a default sort that is not a field, a non-numeric field marked
aggregatable, a field registered under the wrong key. Cheap here; expensive to
discover when a scheduled report has been emitting an empty column for a month.

---

## 3. Specifications

```jsonc
{
  "dataset": "deals",
  "columns": ["title", "value", "priority"],   // detail mode
  "group_by": ["priority"],                    // grouped mode
  "aggregates": [{"function": "sum", "field": "value"}],
  "filters": [
    {"field": "value", "operator": "gte", "value": "500000"},
    {"field": "actual_close_date", "operator": "between",
     "value": ["2026-01-01", "2026-06-30"]}
  ],
  "sort": "created_at",
  "sort_desc": true,
  "limit": 5000,
  "include_sensitive": false
}
```

Detail and grouped are **different queries**, not one query with a flag. Grouped
returns one row per distinct combination with the requested aggregates; a
grouped spec with no aggregates defaults to `count`, since a grouped report with
nothing to aggregate is a `DISTINCT` dressed up as a report.

**Operators are closed and type-checked.** `OPERATORS_BY_TYPE` decides which
apply to each field type, so `contains` on a numeric column is a validation
error rather than a cast Postgres performs and then cannot index. `contains` and
`starts_with` escape LIKE metacharacters — without it, filtering for a client
called "100%" matches everything, which is a wrong answer that looks like a
working feature.

**Values are coerced to the column's type** once the field is known: a money
filter arriving as `"500000"` becomes a `Decimal`, not a string Postgres casts
with its own rules. Lists (`in`, `between`) coerce element-wise — a list is not
an escape hatch from validation.

**Re-validated on every run, not only on save.** A definition written last month
is a document, and the registry may since have lost a field, so the check that a
report is still valid belongs at the moment it runs.

### Row caps

`MAX_ROWS = 50,000`, `DEFAULT_LIMIT = 1,000`. The cap is not a paging
convenience: a report is materialised into a file in memory before it is written
to storage, so an unbounded report is an unbounded allocation in a worker shared
with every other tenant.

A capped run reports **`partial`**, with `total_rows` alongside `row_count`. A
spreadsheet silently missing its tail is worse than one that says it is
truncated, and that distinction is the reason the status exists.

---

## 4. Preview vs. export

|  | Preview | Export |
| --- | --- | --- |
| Where | In the request | Queued to the worker |
| Limit | 100 rows | up to `MAX_ROWS` |
| Output | JSON rows | a file in object storage |
| Audited | No | Yes (`record.exported`) |
| Permission | `reports.view` | `reports.export` |

The preview is the builder's feedback loop — it fires on every filter change,
and an audit log dominated by previews is one nobody reads. Producing a *file*
is the auditable act, and `record.exported` with the row count is what answers
"who took the client list".

Exports are queued because rendering 50,000 rows into an XLSX takes seconds and
tens of megabytes, and doing that inside a request holds a connection while a
browser waits. The API returns `202` with a `queued` run.

The run row is committed **before** the job is enqueued. Enqueue-then-commit is
how a job arrives for a row that does not exist yet, and it fails intermittently
under exactly the load that makes it hardest to reproduce.

---

## 5. Export formats

**CSV** — UTF-8 with a BOM. Without the BOM, Excel on Windows opens the file as
the system code page and mangles every non-ASCII name in it.

**XLSX** — single sheet, written through openpyxl's `write_only` workbook, since
the normal API builds a full cell object graph. Numbers and dates stay typed: a
spreadsheet whose totals cannot be summed is a screenshot with extra steps.
Decimals become floats here and nowhere else — it is a rendering boundary, and
the exact value is already committed in Postgres.

**PDF** — landscape table, capped at `PDF_MAX_ROWS = 2,000` with a visible note
when truncated. PDF is a presentation format; a 50,000-row PDF is not a document
anybody reads, it is a denial-of-service against reportlab's layout engine. Cell
text is escaped (reportlab's `Paragraph` takes a small HTML dialect) and clipped
(one 4,000-character description makes a cell taller than the page).

### CSV injection

Handled once, in `_cell`. A value beginning `=`, `+`, `-` or `@` is prefixed with
a single quote so every spreadsheet renders it as text. `=cmd|'/c calc'!A1` in a
CRM note is otherwise code execution on the machine of whoever opens the export.
The value is neutralised, not stripped — the reader still sees what was typed.

---

## 6. Saved reports and sharing

A saved report is a **document its author owns**: soft-deleted, edited in place,
versionless. The specification is stored as JSONB because the shape is genuinely
open, and normalising it would produce four tables nobody queries independently.

**Sharing widens who may run a report. It never widens the rows.** An agent
running the sales director's shared "all deals" report gets their own deals. Any
other rule turns a saved report into a privilege-escalation primitive — the kind
that looks like a feature in a demo.

Authors edit their own reports; `reports.export` edits any. Without that split,
sharing a report hands everyone who can see it the ability to rewrite what it
means.

Deleting is soft and **cancels the schedule** — a deleted report must not keep
firing.

---

## 7. Scheduling

`none | daily | weekly | monthly`, deliberately coarse. A report is a digest,
and cron-level expressiveness invites schedules nobody can reason about from a
list view.

Creating a schedule requires `reports.export`, not `reports.view` — see below
for why.

**Dueness is computed from `last_run_at`**, not from a stored next-run
timestamp. A stored one drifts whenever a run is missed, and a report that
silently stops after one outage is worse than one that runs late. A NULL
`last_run_at` is always due, so a newly scheduled report does not wait a full
period for its first delivery.

**A scheduled run has no user behind it.** `requested_by` stays NULL rather than
being attributed to the author — the author did not press anything at 03:00, and
an audit trail that says they did is a lying audit trail. The run executes under
a `system_context` holding every dataset's view grant at ALL scope. That is the
honest statement of what a scheduled workspace report is, and it is exactly why
creating one requires `reports.export`.

Recipients are **user ids, not email addresses**, so an ex-employee's address
cannot keep receiving the company's numbers. Each gets an in-app notification
carrying the **run id, not a signed URL** — links expire in five minutes and a
notification can sit unread for days, so the user mints a fresh link on open.

---

## 8. Run history and retention

`report_runs` is append-only and never edited after it terminates. It exists so
"who exported the client list, when, and how many rows" has an answer — a
question asked after an incident.

`definition_id` is `ON DELETE SET NULL`, not CASCADE: losing the evidence when
someone deletes the report is exactly backwards. The definition snapshot is
copied into the run, so a run that says "1,204 rows" against a since-rewritten
definition is still explainable.

Statuses: `queued → running → succeeded | partial | failed`. A crash mid-render
leaves `running`, which is reapable — better than a row that claims success
because the status was written optimistically up front.

**Retention: 30 days for the file, forever for the row.** `sweep_expired_exports`
deletes the object and NULLs `storage_key`. Deleting the row along with the
object would destroy the evidence to save the storage.

---

## 9. API

All under `/api/v1/reports`.

| Method | Path | Permission | Notes |
| --- | --- | --- | --- |
| GET | `/datasets` | `reports.view` | filtered to what the caller may query |
| POST | `/preview` | `reports.view` | ≤100 rows, inline, unaudited |
| GET | `` | `reports.view` | own + shared |
| POST | `` | `reports.view` (+`export` to schedule) | validated on save |
| GET | `/{id}` | `reports.view` | |
| PATCH | `/{id}` | author or `reports.export` | |
| DELETE | `/{id}` | author or `reports.export` | soft, cancels schedule |
| POST | `/export` | `reports.export` | `202`, returns a queued run |
| GET | `/runs/history` | `reports.view` | own runs unless `reports.export` |
| GET | `/runs/{id}` | `reports.view` | |
| GET | `/runs/{id}/download` | `reports.view` | 5-minute signed URL |

---

## 10. Operations

**`run_report_export(run_id, organization_id)`** — renders one run. Terminal
runs return early, so a duplicate delivery does not re-render and re-charge
storage for a file that already exists. A render failure writes the reason onto
the run and re-raises, so the runner records it and retries — a storage fault is
usually transient.

**`sweep_scheduled_reports`** — hourly at `:12`. Enqueues due reports across
every tenant and re-queues `queued` runs older than five minutes, which is what
makes a failed enqueue *late* rather than lost. Hourly rather than per-minute:
a daily report is due once a day, and 1,440 scans of every tenant's schedule to
find nothing is not a bargain.

**`sweep_expired_exports`** — daily at 03:40, away from the metric snapshot.

Storage keys are tenant-first (`org/<id>/exports/<run_id>/<name>.<ext>`), so a
per-tenant lifecycle rule or deletion is a prefix operation, and run-scoped so a
re-export cannot silently overwrite what an older link points at.

Both tables are tenant-scoped with RLS `ENABLE` + `FORCE`, like every business
table.

Related: [ANALYTICS.md](ANALYTICS.md) for the scope rule reporting reuses,
[JOBS.md](JOBS.md) for worker semantics, [DOCUMENTS.md](DOCUMENTS.md) for the
storage layer exports are written through.

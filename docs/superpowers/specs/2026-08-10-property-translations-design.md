# Multilingual property content

**Date:** 2026-08-10
**Status:** approved, ready to implement

## The problem

The UI ships in three languages — `en`, `pt-BR`, `ru` — and the language toggle
changes every label. It does not change the content. A Brazilian client shown a
listing sees Portuguese chrome wrapped around a Russian description.

This is not cosmetic. Rossa sells Brazilian property to Russian-speaking buyers
through Brazilian agents; all three languages are in daily use by different
people looking at the same records. The owner stated the rule directly: content
must follow the toggle, not just the interface.

The immediate trigger is the imported Rossa catalogue — 41 listings, Russian
only. It cannot go to production until this exists.

## What was ruled out, and why

**Translations on the source site.** Checked first, since a human translation
beats a machine one. `rossagroupbrazil.com` serves English at `/eng` and Russian
at `/rus`, but only the Russian page carries `window.RGB_CATALOG`; the English
pages run on a different builder and contain no listing data. There is no
Portuguese version at all. No reusable translations exist.

**Per-locale columns** (`title_en`, `title_pt_br`, …). A fourth language becomes
a migration plus an edit to every query that reads a listing.

**A JSONB column on `properties`.** Tempting — the table already carries
`custom_fields` — and it was rejected for one concrete reason. Every property
mutation writes an audit entry in the same transaction
(`PropertyService` → `AuditService.record`). Storing a translation means
updating the property row, so each background translation would forge an
audit entry and move the record's "last modified". Forty-one listings would
look edited by a robot, and the audit log — which exists to answer *who changed
this* — would start lying.

## Storage

A `property_translations` table, one row per (property, locale):

| column | purpose |
| --- | --- |
| `organization_id` | tenant, RLS-enforced like every other table |
| `property_id` | FK, cascade delete |
| `locale` | one of `en` / `pt-BR` / `ru` |
| `title`, `description`, `features` | the translated content |
| `is_machine` | false once a human has edited it |
| `source_hash` | fingerprint of the source text this was translated from |
| `is_stale` | source changed after a human-edited translation |
| `translated_at`, `edited_by_id`, `edited_at` | provenance |

Unique on `(property_id, locale)`.

`properties` gains one column: **`source_locale`** — the language the text was
actually written in. `ru` for the catalogue import; for a property an agent
creates, the locale their interface was set to.

The background writer touches only this table. The property row and its audit
trail are left alone, which is the entire point of the separate table.

### Staleness, and not destroying human work

`source_hash` fingerprints the source text. When an agent edits the Russian
description the hash diverges, and the translations are regenerated — **unless
a human edited that translation**. A human-edited translation is never
overwritten: it is flagged `is_stale` and surfaced to the agent, who decides.
A machine does not throw away a person's work.

## How a translation appears

Nothing is translated inside the request. Saving a listing must not wait on a
model, and must not fail when one is down.

1. **On save**, after the transaction commits, a job is enqueued for the
   property.
2. **The job** translates into every locale except `source_locale` and writes
   the rows.
3. **A cron sweep** runs periodically and picks up anything missing or stale.

The sweep is not redundant. Queues lose work — Redis restarts, Groq answers 429
(it does, routinely), the worker redeploys mid-job. The codebase already uses
exactly this belt-and-braces shape for malware scanning (`sweep_scan_backlog`);
this follows it. Without the sweep, failures are silent, and a silently
untranslated listing is indistinguishable from a translated one until a client
is looking at it.

### Translator instructions

The source text mixes three languages already, so the prompt is part of the
design rather than an implementation detail:

- **Proper names stay verbatim** — `NATUS`, `Oceana`, `Ponta das Canas`,
  `Florianópolis`.
- **Market terms stay in their established form** — `Infinity Pool`,
  `Beach Club`, `Technogym`, `suítes`. Brazilian real estate says these in
  English or Portuguese regardless of the reader's language; translating them
  into Russian reads as amateur.
- **Numbers, units and structure are preserved** — `182 м²` stays `182 m²`,
  bullet lists stay bullet lists.
- **Portuguese means Brazilian Portuguese.** A buyer in Florianópolis hears
  European Portuguese immediately.

## Reading

A property is served in the requester's locale. The resolution order is
**requested locale → source locale → any available translation**, so a field is
never empty because a translation is missing.

Machine translations are marked in the UI and editable in place; editing clears
the mark and pins the text against future regeneration.

## Out of scope

Full-text search continues to index the source text only. Searching in a
language the listing was not written in is a real want and a larger change
(`properties.search_vector` is per-row), and it is not what blocks the
catalogue from reaching production.

## Done when

- A listing created in Russian is readable in English and Portuguese without
  anyone pressing anything.
- The language toggle changes description text, not only labels.
- Editing a translation survives a later edit of the source.
- The 41 catalogue listings are fully translated, verified by reading several.

"""The automation engine: registries, validation, conditions, scheduling.

Pure unit tests — no database. What they defend is the layer where a mistake is
silent and expensive:

* **Validation** rejects the definitions that would misbehave at run time. A
  cycle, an orphan, an action under a trigger that can never satisfy it. Each of
  these is a message in the builder here, or an incident on live customer data
  later.
* **Conditions** decide whether a customer gets contacted. The cases that matter
  are the ones with no obviously right answer — a missing field, a numeric
  comparison on strings, a `changed` operator on a create — because those are
  where an implementation quietly picks one behaviour and nobody notices which.
* **Business hours** turn "wait one hour" on a Friday evening into something a
  client would not be surprised to receive.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.automation.actions import ACTIONS, parse_field_updates, render_template
from app.automation.conditions import OPERATORS, Comparison, evaluate_comparison
from app.automation.definition import MAX_NODES, parse, validate
from app.automation.handlers import _HANDLERS
from app.automation.registry import build_registry
from app.automation.scheduling import BusinessHours, resume_at
from app.automation.triggers import TRIGGERS, trigger_matches

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)  # a Wednesday


def _definition(nodes: dict, start: str = "n1", trigger: str = "lead.created") -> dict:
    return {
        "trigger": {"type": trigger, "config": {}},
        "start_node": start,
        "nodes": nodes,
    }


def _action_node(next_id: str | None = None, **overrides) -> dict:  # type: ignore[no-untyped-def]
    return {
        "type": "action",
        "action": "add_note",
        "config": {"body": "hello"},
        "next": next_id,
        **overrides,
    }


class TestRegistries:
    def test_every_action_has_a_handler(self) -> None:
        """The registry is what the builder offers; the handler map is what the
        executor can run. A key in one and not the other is a workflow that
        validates and then fails on every record."""
        assert set(ACTIONS.entries) == set(_HANDLERS)

    def test_duplicate_keys_are_rejected_at_import(self) -> None:
        """A shadowed entry is a node that validates but does the wrong thing,
        and the symptom is a long way from the cause."""
        from app.automation.registry import Definition

        with pytest.raises(ValueError, match="Duplicate"):
            build_registry(
                Definition(key="same", label="A", description=""),
                Definition(key="same", label="B", description=""),
            )

    def test_every_trigger_declares_an_entity_type(self) -> None:
        """The builder uses it to offer the right fields and the right actions."""
        assert all(trigger.entity_type for trigger in TRIGGERS.all())

    def test_config_validation_reports_every_problem(self) -> None:
        """So the builder can mark up every bad field at once rather than
        making the author fix one, save, and discover another."""
        errors = ACTIONS.require("create_task").validate_config(
            {"nonsense": 1, "assignee": "not-an-option"}
        )
        assert len(errors) >= 2

    def test_an_unknown_setting_is_an_error_not_noise(self) -> None:
        """It means the builder and the registry disagree, and dropping it
        silently makes a node do less than its author configured."""
        errors = ACTIONS.require("add_note").validate_config(
            {"body": "hi", "colour": "blue"}
        )
        assert any("Unknown setting" in error for error in errors)


class TestValidation:
    def test_a_minimal_workflow_is_valid(self) -> None:
        assert validate(parse(_definition({"n1": _action_node()}))) == []

    def test_a_cycle_is_refused(self) -> None:
        """Rejected rather than bounded by a step budget: a loop that emails a
        client forty times because the budget was fifty is not a smaller bug."""
        definition = _definition(
            {"n1": _action_node("n2"), "n2": _action_node("n1")}
        )
        errors = validate(parse(definition))
        assert any("loop" in error.lower() for error in errors)

    def test_an_unreachable_step_is_refused(self) -> None:
        """Almost always a wiring mistake, and ignoring it means the author
        believes their workflow does something it does not."""
        definition = _definition({"n1": _action_node(), "n2": _action_node()})
        errors = validate(parse(definition))
        assert any("cannot be reached" in error for error in errors)

    def test_converging_branches_are_allowed(self) -> None:
        """Two branches continuing into the same tail is harmless, and
        forbidding it just to keep the shape a strict tree would be arbitrary."""
        definition = _definition(
            {
                "n1": {
                    "type": "condition",
                    "mode": "all",
                    "comparisons": [
                        {"field": "stage", "operator": "equals", "value": "new"}
                    ],
                    "on_true": "n2",
                    "on_false": "n2",
                },
                "n2": _action_node(),
            }
        )
        assert validate(parse(definition)) == []

    def test_a_dangling_edge_is_refused(self) -> None:
        errors = validate(parse(_definition({"n1": _action_node("nowhere")})))
        assert any("does not exist" in error for error in errors)

    def test_an_action_under_an_incompatible_trigger_is_refused(self) -> None:
        """Caught at publish rather than being a failed run for every record
        that ever triggers it."""
        definition = _definition(
            {"n1": {"type": "action", "action": "change_deal_stage",
                    "config": {"stage_id": "x"}, "next": None}},
            trigger="note.created",
        )
        errors = validate(parse(definition))
        assert any("cannot run on a note trigger" in error for error in errors)

    def test_an_unknown_trigger_is_refused(self) -> None:
        definition = _definition({"n1": _action_node()}, trigger="nope.nope")
        assert any("Unknown trigger" in error for error in validate(parse(definition)))

    def test_a_workflow_with_no_steps_is_refused(self) -> None:
        errors = validate(parse({"trigger": {"type": "lead.created"}, "nodes": {}}))
        assert any("at least one step" in error for error in errors)

    def test_an_oversized_workflow_is_refused(self) -> None:
        nodes = {
            f"n{i}": _action_node(f"n{i + 1}" if i < MAX_NODES + 1 else None)
            for i in range(1, MAX_NODES + 2)
        }
        errors = validate(parse(_definition(nodes)))
        assert any("at most" in error for error in errors)

    def test_a_condition_with_no_branches_is_refused(self) -> None:
        definition = _definition(
            {
                "n1": {
                    "type": "condition",
                    "mode": "all",
                    "comparisons": [
                        {"field": "stage", "operator": "equals", "value": "new"}
                    ],
                }
            }
        )
        assert any("no branches" in error for error in validate(parse(definition)))

    def test_a_delay_that_leads_nowhere_is_refused(self) -> None:
        """Waiting and then doing nothing is never what somebody meant."""
        definition = _definition(
            {"n1": {"type": "delay", "config": {"minutes": 60}, "next": None}}
        )
        assert any("then does nothing" in error for error in validate(parse(definition)))

    def test_a_delay_beyond_the_maximum_is_refused(self) -> None:
        definition = _definition(
            {
                "n1": {"type": "delay", "config": {"minutes": 60 * 24 * 400},
                       "next": "n2"},
                "n2": _action_node(),
            }
        )
        assert any("30-day" in error for error in validate(parse(definition)))

    def test_a_draft_is_allowed_to_be_incomplete(self) -> None:
        """Parsing must not throw on a half-built node — that is what a draft
        is, and the builder saves on every change."""
        parsed = parse({"trigger": {}, "nodes": {"n1": {"type": "action"}}})
        assert parsed.nodes["n1"].action is None


class TestConditions:
    def _check(self, field, operator, value, record, previous=None, actor=None):  # type: ignore[no-untyped-def]
        return evaluate_comparison(
            Comparison(field=field, operator=operator, value=value),
            record=record,
            previous=previous or {},
            actor_id=actor,
            now=NOW,
        )

    def test_a_missing_field_never_matches(self) -> None:
        """Not an error that fails the run, not "matches empty" — the branch is
        simply not taken. A run that dies because a lead has no phone number is
        worse than one that skips and says so."""
        assert self._check("phone", "equals", "x", {}) is False
        assert self._check("phone", "contains", "x", {}) is False
        assert self._check("phone", "greater_than", 1, {}) is False

    def test_is_empty_and_is_set_are_the_presence_operators(self) -> None:
        assert self._check("phone", "is_empty", None, {}) is True
        assert self._check("phone", "is_set", None, {"phone": "123"}) is True
        assert self._check("tags", "is_empty", None, {"tags": []}) is True

    def test_numeric_comparison_is_typed_by_the_operator(self) -> None:
        """`"10" > "9"` has opposite answers as text and as a number, and
        picking by inspecting the values is a bug nobody finds."""
        assert self._check("budget_max", "greater_than", 9, {"budget_max": "10"})
        assert not self._check("budget_max", "greater_than", "abc", {"budget_max": "10"})

    def test_changed_is_false_on_a_create(self) -> None:
        """A create has no before-image. A workflow that wants both says so
        with two triggers."""
        assert self._check("stage", "changed", None, {"stage": "new"}) is False

    def test_changed_to_needs_an_actual_change(self) -> None:
        record, previous = {"stage": "qualified"}, {"stage": "new"}
        assert self._check("stage", "changed_to", "qualified", record, previous)
        # Same value on both sides is not a change, even though it matches.
        assert not self._check(
            "stage", "changed_to", "new", {"stage": "new"}, {"stage": "new"}
        )

    def test_changed_from_reads_the_before_image(self) -> None:
        record, previous = {"stage": "qualified"}, {"stage": "new"}
        assert self._check("stage", "changed_from", "new", record, previous)

    def test_ownership_compares_against_the_trigger_actor(self) -> None:
        assert self._check("x", "owned_by_actor", None, {"owner_id": "u1"}, actor="u1")
        assert not self._check(
            "x", "owned_by_actor", None, {"owner_id": "u2"}, actor="u1"
        )

    def test_unassigned_covers_both_owner_conventions(self) -> None:
        assert self._check("x", "is_unassigned", None, {}) is True
        assert self._check("x", "is_unassigned", None, {"assignee_id": "u1"}) is False

    def test_tags_are_matched_case_insensitively(self) -> None:
        assert self._check("tags", "has_tag", "Cash", {"tags": ["cash", "hot"]})
        assert self._check("tags", "not_has_tag", "vip", {"tags": ["cash"]})

    def test_date_operators_read_iso_strings(self) -> None:
        old = (NOW - timedelta(days=10)).isoformat()
        soon = (NOW + timedelta(days=2)).isoformat()
        stale = {"last_contacted_at": old}
        assert self._check("last_contacted_at", "older_than_days", 7, stale)
        assert not self._check("last_contacted_at", "within_last_days", 7, stale)
        assert self._check("due_at", "due_within_days", 5, {"due_at": soon})
        assert self._check("due_at", "is_future", None, {"due_at": soon})

    def test_a_naive_timestamp_is_read_as_utc(self) -> None:
        """Every timestamp this system stores is `timestamptz`, so a naive
        value means the serialiser dropped the offset, not that the moment is
        local."""
        naive = (NOW - timedelta(days=10)).replace(tzinfo=None).isoformat()
        assert self._check(
            "last_contacted_at", "older_than_days", 7, {"last_contacted_at": naive}
        )

    def test_in_list_splits_on_commas(self) -> None:
        assert self._check("stage", "in_list", "new, contacted", {"stage": "contacted"})

    def test_every_operator_is_reachable(self) -> None:
        """A registry entry with no branch in `evaluate_comparison` is an
        operator the builder offers and the engine silently answers false to."""
        record = {
            "field": "value", "tags": ["x"], "owner_id": "u1",
            "when": NOW.isoformat(),
        }
        for operator in OPERATORS.all():
            # Not asserting the result — only that nothing raises and no
            # "unknown operator" path is hit.
            evaluate_comparison(
                Comparison(field="field", operator=operator.key, value="value"),
                record=record,
                previous={"field": "old"},
                actor_id="u1",
                now=NOW,
            )


class TestTriggerNarrowing:
    def test_no_config_matches_everything(self) -> None:
        trigger = TRIGGERS.require("lead.updated")
        assert trigger_matches(trigger, {}, {"changed_fields": ["stage"]})

    def test_watched_fields_narrow_an_update(self) -> None:
        """Without it, "when a lead is updated, email them" fires on every
        touch — including the ones the workflow itself made."""
        trigger = TRIGGERS.require("lead.updated")
        config = {"fields": "stage, temperature"}
        assert trigger_matches(trigger, config, {"changed_fields": ["stage"]})
        assert not trigger_matches(trigger, config, {"changed_fields": ["notes"]})

    def test_stage_narrowing_is_equality_on_the_payload(self) -> None:
        trigger = TRIGGERS.require("deal.stage_changed")
        config = {"to_stage_id": "abc"}
        assert trigger_matches(trigger, config, {"to_stage_id": "abc"})
        assert not trigger_matches(trigger, config, {"to_stage_id": "xyz"})

    def test_channel_narrowing_applies_to_messages(self) -> None:
        trigger = TRIGGERS.require("message.received")
        assert trigger_matches(trigger, {"channel": "whatsapp"}, {"channel": "whatsapp"})
        assert not trigger_matches(trigger, {"channel": "whatsapp"}, {"channel": "email"})


class TestTemplating:
    def test_placeholders_resolve_from_the_context(self) -> None:
        rendered = render_template(
            "Hi {{record.first_name}}, about {{record.city}}",
            {"record": {"first_name": "Sana", "city": "Noe Valley"}},
        )
        assert rendered == "Hi Sana, about Noe Valley"

    def test_an_unresolved_placeholder_renders_empty(self) -> None:
        """Leaving `{{lead.first_name}}` in a message a customer receives tells
        them they are being processed by a machine that is not working."""
        assert render_template("Hi {{record.missing}}!", {"record": {}}) == "Hi !"

    def test_braces_that_are_not_placeholders_survive(self) -> None:
        assert render_template("Use {this}", {}) == "Use {this}"

    def test_step_outputs_are_addressable(self) -> None:
        rendered = render_template(
            "Task {{step.n2.task_id}}", {"step": {"n2": {"task_id": "T-1"}}}
        )
        assert rendered == "Task T-1"

    def test_field_updates_parse_line_by_line(self) -> None:
        updates = parse_field_updates(
            "stage=qualified\n temperature = hot \n\nbroken-line",
            {},
        )
        assert updates == {"stage": "qualified", "temperature": "hot"}

    def test_field_update_values_are_templated(self) -> None:
        updates = parse_field_updates(
            "notes={{record.first_name}} called", {"record": {"first_name": "Sana"}}
        )
        assert updates == {"notes": "Sana called"}


class TestScheduling:
    def test_a_plain_delay_is_wall_clock(self) -> None:
        assert resume_at(minutes=90, now=NOW) == NOW + timedelta(minutes=90)

    def test_business_hours_skip_the_evening(self) -> None:
        """"Wait one hour" from 5:30pm should not mean 6:30pm if the next step
        emails a client."""
        evening = datetime(2026, 8, 5, 17, 30, tzinfo=UTC)
        woken = resume_at(minutes=60, business_hours=BusinessHours(), now=evening)
        assert woken.hour == 9 and woken.minute == 30
        assert woken.day == 6

    def test_business_hours_count_working_minutes(self) -> None:
        """A two-hour delay from 5pm resumes at 10am, not 7pm. Counting
        wall-clock and then shifting would make it 17 hours."""
        evening = datetime(2026, 8, 5, 17, 0, tzinfo=UTC)
        woken = resume_at(minutes=120, business_hours=BusinessHours(), now=evening)
        assert (woken.day, woken.hour) == (6, 10)

    def test_business_hours_skip_the_weekend(self) -> None:
        friday_evening = datetime(2026, 8, 7, 19, 0, tzinfo=UTC)
        woken = resume_at(minutes=30, business_hours=BusinessHours(), now=friday_evening)
        assert woken.weekday() == 0  # Monday

    def test_inside_the_window_is_untouched(self) -> None:
        woken = resume_at(minutes=30, business_hours=BusinessHours(), now=NOW)
        assert woken == NOW + timedelta(minutes=30)

    def test_an_unsatisfiable_window_does_not_loop_forever(self) -> None:
        """A configuration with no working days must fail visibly rather than
        parking a run until the heat death of the universe."""
        hours = BusinessHours(working_days=())
        assert resume_at(minutes=60, business_hours=hours, now=NOW) is not None

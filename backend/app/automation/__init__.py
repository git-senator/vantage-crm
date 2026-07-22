"""The automation engine.

Layout:

    registry.py     the shape of a registry entry, shared by all three
    triggers.py     what can start a workflow
    conditions.py   the comparison operators and their evaluation
    actions.py      what a workflow does, and the templating
    definition.py   parsing and validating the node graph
    events.py       the transactional outbox CRM services write to
    scheduling.py   delays and business hours
    context.py      what a running workflow is allowed to do
    executor.py     the interpreter

The three registries are the contract between the backend and the visual
builder: the API serves them, so the palette cannot drift from what the
executor can actually run.
"""

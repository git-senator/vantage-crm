"""Outbound webhook infrastructure (Phase 7.3).

Signing, the supported event vocabulary, the payload envelope, and the HTTP
send. The durable model, management service and delivery jobs live in their
usual homes (`app/models`, `app/services`, `app/workers/jobs`); this package
holds the parts that are pure webhook mechanics and want to be imported without
dragging a session in.
"""

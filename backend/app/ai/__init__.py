"""The AI growth engine's pure core: prompts, context builders, redaction.

Split from `app/services/ai` for the reason every pure package in this codebase
is (analytics, reporting, automation): the prompt framework, the context
builders and the redaction rules are importable and testable without a session,
a key, or a network. Keeping them that way is what stops the safety rules — the
system/user separation, redaction-before-egress, scope-before-fetch — from
migrating into request handlers one convenience at a time.

The provider adapters and the orchestrating `AIService` live in
`app/services/ai`, which depends on this package and not the other way round.
"""

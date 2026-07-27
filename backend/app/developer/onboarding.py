"""Developer onboarding: the getting-started sequence and copy-paste quickstarts.

Static content, parameterised by the caller's base URL so a snippet is runnable
as shown rather than a template the reader has to fill in. Kept out of the
service so the copy is reviewable in one place and the service stays about
wiring, not prose.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.developer.sdk import DEFAULT_BASE_URL


@dataclass(frozen=True)
class OnboardingStep:
    title: str
    body: str


def onboarding_steps(base_url: str = DEFAULT_BASE_URL) -> list[OnboardingStep]:
    """The ordered path from zero to a first successful call."""
    return [
        OnboardingStep(
            "Create a sandbox API key",
            "Mint a `sandbox` key from the developer portal (or POST "
            "/api/v1/api-keys with `environment: sandbox`). Sandbox keys are "
            "free — they skip the plan gate and the API-key quota — so you can "
            "build against the API before subscribing.",
        ),
        OnboardingStep(
            "Make your first request",
            f"Authenticate every request with the `X-API-Key` header against "
            f"{base_url}. Start with `GET /leads` to confirm your key works.",
        ),
        OnboardingStep(
            "Install a generated SDK",
            "Download the TypeScript or Python SDK from the portal. Each is "
            "generated from this instance's OpenAPI document, so it always "
            "matches the API you are calling.",
        ),
        OnboardingStep(
            "Make writes idempotent",
            "Send a unique `Idempotency-Key` header on every POST/PATCH. A "
            "retried request with the same key returns the original result "
            "instead of creating a duplicate.",
        ),
        OnboardingStep(
            "Subscribe to webhooks",
            "Register a webhook endpoint to receive events (lead.created, "
            "deal.updated, and more) instead of polling. Verify the "
            "`X-Vantage-Signature` HMAC on every delivery.",
        ),
        OnboardingStep(
            "Go live",
            "Swap your sandbox key for a `live` key (requires a plan with API "
            "access) and point your integration at the same base URL.",
        ),
    ]


def quickstarts(base_url: str = DEFAULT_BASE_URL) -> dict[str, str]:
    """Runnable first-call snippets, one per language, keyed by language."""
    return {
        "curl": (
            f'curl {base_url}/leads \\\n'
            f'  -H "X-API-Key: $VANTAGE_API_KEY" \\\n'
            f'  -H "Accept: application/json"'
        ),
        "typescript": (
            'import { VantageClient } from "@vantage/crm";\n\n'
            "const vantage = new VantageClient({\n"
            "  apiKey: process.env.VANTAGE_API_KEY!,\n"
            f'  baseUrl: "{base_url}",\n'
            "});\n\n"
            "const leads = await vantage.leads.list({ limit: 25 });\n"
            "console.log(leads);"
        ),
        "python": (
            "from vantage_crm import VantageClient\n\n"
            "vantage = VantageClient(\n"
            '    api_key="vk_...",\n'
            f'    base_url="{base_url}",\n'
            ")\n\n"
            'leads = vantage.leads.list(query={"limit": 25})\n'
            "print(leads)"
        ),
    }


__all__ = ["OnboardingStep", "onboarding_steps", "quickstarts"]

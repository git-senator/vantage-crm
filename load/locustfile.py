"""Load and stress profile for the Vantage CRM public API (Phase 8.1).

Run against a staging instance with a sandbox API key — never production, never a
live key:

    VANTAGE_API_KEY=vk_... locust -f load/locustfile.py \
        --host https://staging.crm.example.com --users 200 --spawn-rate 20

The mix is read-heavy, mirroring real usage: listing and cursor-paging dominate,
with a small fraction of idempotent writes. The write path sends an
`Idempotency-Key` so a retried request under load does not create duplicates —
exercising exactly the production safety net.
"""

from __future__ import annotations

import os
import uuid

from locust import HttpUser, between, task

_API_KEY = os.environ.get("VANTAGE_API_KEY", "")
_PREFIX = "/api/public/v1"


class PublicApiUser(HttpUser):
    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        self.client.headers.update(
            {"X-API-Key": _API_KEY, "Accept": "application/json"}
        )
        self._cursor: str | None = None

    @task(6)
    def list_leads(self) -> None:
        params = {"limit": 25}
        if self._cursor:
            params["cursor"] = self._cursor
        with self.client.get(
            f"{_PREFIX}/leads", params=params, name="GET /leads", catch_response=True
        ) as response:
            if response.status_code == 200:
                self._cursor = response.json().get("meta", {}).get("next_cursor")
                response.success()
            else:
                response.failure(f"status {response.status_code}")

    @task(3)
    def list_deals(self) -> None:
        self.client.get(f"{_PREFIX}/deals", params={"limit": 25}, name="GET /deals")

    @task(2)
    def list_properties(self) -> None:
        self.client.get(
            f"{_PREFIX}/properties", params={"limit": 25}, name="GET /properties"
        )

    @task(1)
    def create_lead(self) -> None:
        body = {
            "first_name": "Load",
            "last_name": f"Test-{uuid.uuid4().hex[:8]}",
            "email": f"load-{uuid.uuid4().hex[:12]}@example.com",
        }
        self.client.post(
            f"{_PREFIX}/leads",
            json=body,
            headers={"Idempotency-Key": str(uuid.uuid4())},
            name="POST /leads",
        )

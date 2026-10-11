"""Business metrics are exported and never carry tenant data or amounts."""

from __future__ import annotations

import re
from collections.abc import Callable

from fastapi.testclient import TestClient

from tests.integration.conftest import TenantUsers

ClientFor = Callable[..., TestClient]


def test_metrics_expose_business_signals_without_sensitive_labels(
    client_for: ClientFor, demo_tenant: TenantUsers
) -> None:
    client = client_for()
    client.post(
        "/api/v1/auth/login", json={"email": "nobody@nowhere.test", "password": "wrong-password"}
    )
    body = client.get("/api/v1/metrics").text
    assert re.search(r'nimbrix_auth_events_total\{outcome="failure"\} [1-9]', body)
    assert re.search(
        r'nimbrix_recommendations\{category="idle_nonprod_instance",status="\w+"\} [1-9]', body
    )
    assert 'nimbrix_jobs{kind="import_demo",state="succeeded"}' in body
    for forbidden in (
        "nobody@nowhere.test",
        "wrong-password",
        demo_tenant.slug,
        str(demo_tenant.tenant_id),
        "280.32",
    ):
        assert forbidden not in body

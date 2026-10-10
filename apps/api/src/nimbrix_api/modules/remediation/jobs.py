"""Remediation job: runs the simulation adapter with revalidation and verification."""

from __future__ import annotations

import uuid
from typing import Any

from nimbrix_api.modules.jobs.runner import JobContext, register
from nimbrix_api.modules.remediation import service


@register("remediate")
def remediate(ctx: JobContext) -> dict[str, Any]:
    with ctx.session_factory() as session:
        result = service.run_execution(
            session, ctx.settings, ctx.tenant_id, uuid.UUID(ctx.payload["execution_id"])
        )
        session.commit()
    return result

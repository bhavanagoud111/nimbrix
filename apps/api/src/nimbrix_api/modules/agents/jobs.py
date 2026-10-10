"""Investigation job handler."""

from __future__ import annotations

import uuid
from typing import Any

from nimbrix_api.modules.agents import service
from nimbrix_api.modules.jobs.runner import JobContext, register


@register("investigate")
def investigate(ctx: JobContext) -> dict[str, Any]:
    return service.execute(
        ctx.session_factory, ctx.settings, ctx.tenant_id, uuid.UUID(ctx.payload["investigation_id"])
    )

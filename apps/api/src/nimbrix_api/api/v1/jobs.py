"""Background job status."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from nimbrix_api.api.v1.paths import API_V1_PREFIX
from nimbrix_api.core.deps import AuthContext, DbSession, require
from nimbrix_api.core.permissions import Permission
from nimbrix_api.core.tenancy import get_owned
from nimbrix_api.models import Job
from nimbrix_api.modules.ingestion.schemas import JobOut

router = APIRouter(prefix=API_V1_PREFIX, tags=["jobs"])


def job_out(job: Job) -> JobOut:
    return JobOut.model_validate(job, from_attributes=True)


@router.get("/jobs/{job_id}", response_model=JobOut, summary="Background job status")
def get_job(
    job_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(require(Permission.READ))],
    session: DbSession,
) -> JobOut:
    return job_out(get_owned(session, Job, auth, job_id, label="job"))

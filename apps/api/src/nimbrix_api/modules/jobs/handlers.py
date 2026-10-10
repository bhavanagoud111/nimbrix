"""Imports every module that registers job handlers."""

from nimbrix_api.modules.agents import jobs as agent_jobs
from nimbrix_api.modules.analysis import jobs as analysis_jobs
from nimbrix_api.modules.ingestion import jobs as ingestion_jobs
from nimbrix_api.modules.remediation import jobs as remediation_jobs

__all__ = ["agent_jobs", "analysis_jobs", "ingestion_jobs", "remediation_jobs"]

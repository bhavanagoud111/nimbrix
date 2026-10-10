"""Imports every module that registers job handlers."""

from nimbrix_api.modules.analysis import jobs as analysis_jobs
from nimbrix_api.modules.ingestion import jobs as ingestion_jobs

__all__ = ["analysis_jobs", "ingestion_jobs"]

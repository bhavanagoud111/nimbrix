"""Imports every module that registers job handlers."""

from nimbrix_api.modules.ingestion import jobs as ingestion_jobs

__all__ = ["ingestion_jobs"]

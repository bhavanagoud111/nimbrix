"""Typed agent state, model output contracts, and the final report."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Confidence = Literal["low", "medium", "high", "insufficient_evidence"]


class Evidence(BaseModel):
    """One citable fact. IDs are stable and namespaced (cost:, resource:, anomaly:, ...)."""

    id: str
    kind: str
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(max_length=600)
    evidence_ids: list[str] = Field(min_length=1, max_length=12)
    confidence: Confidence


class ModelAnalysis(BaseModel):
    """What a model (LLM or deterministic) may contribute: words, citations, confidence. No numbers it invented."""

    model_config = ConfigDict(extra="forbid")

    hypotheses: list[Hypothesis] = Field(max_length=5)
    narrative: str = Field(max_length=2000)
    insufficient_evidence: bool
    missing_evidence: list[str] = Field(default_factory=list, max_length=10)


class ProposedAction(BaseModel):
    action_type: str
    resource_id: str
    recommendation_id: str | None
    estimated_monthly_savings: str | None
    policy_allowed: bool
    policy_denial_reasons: list[str]


class InvestigationReport(BaseModel):
    title: str
    mode: str
    model: str | None
    anomaly_id: str | None
    summary: str
    root_cause: Hypothesis | None
    hypotheses: list[Hypothesis]
    insufficient_evidence: bool
    missing_evidence: list[str]
    key_figures: dict[str, str]
    contributing_resources: list[dict[str, Any]]
    forecast_context: dict[str, Any] | None
    related_recommendations: list[dict[str, Any]]
    proposed_actions: list[ProposedAction]
    evidence: list[Evidence]
    limitations: list[str]
    model_output_rejected: list[str] = Field(
        default_factory=list,
        description="Reasons an LLM answer was rejected (deterministic fallback used)",
    )

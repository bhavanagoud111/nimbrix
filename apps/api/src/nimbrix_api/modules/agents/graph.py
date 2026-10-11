"""LangGraph investigation state machine.

triage (Cost Analyst) -> collect_evidence (Infrastructure Investigator) -> forecast_context
(Forecast Analyst) -> hypothesize (Cost Analyst; LLM if configured, bounded retries,
deterministic fallback) -> synthesize_recommendations (Optimization Planner) ->
policy_review (Optimization Planner) -> report (Report Agent).

Explicit transitions only; no free-form agent chatter. Every node runs inside a budget
(max steps, wall-clock deadline, LLM-call cap) and checks for cancellation first. Each
step is persisted as an ``agent_runs`` row.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from nimbrix_api.core.business_metrics import AGENT_CALLS, AGENT_TOKENS
from nimbrix_api.core.config import Settings
from nimbrix_api.core.telemetry import tracer
from nimbrix_api.models import AgentRun, Investigation
from nimbrix_api.modules.agents import deterministic
from nimbrix_api.modules.agents.providers import (
    InvestigationModel,
    MalformedOutputError,
    ModelResult,
    ProviderRefusalError,
    ProviderUnavailableError,
)
from nimbrix_api.modules.agents.schemas import (
    Evidence,
    InvestigationReport,
    ModelAnalysis,
    ProposedAction,
)
from nimbrix_api.modules.agents.tools import EvidenceTools, ToolAccessDeniedError
from nimbrix_api.modules.agents.validation import grounding_errors
from nimbrix_api.modules.remediation.registry import REGISTRY, evaluate_policy

MAX_HYPOTHESIS_ATTEMPTS = 2
NodeResult = tuple[dict[str, Any], str, list[str]]


class InvestigationCancelledError(Exception):
    pass


class BudgetExceededError(Exception):
    pass


class State(TypedDict, total=False):
    question: str
    anomaly_id: str | None
    anomaly_found: bool
    evidence: list[dict[str, Any]]
    contributors: list[dict[str, Any]]
    analysis: dict[str, Any] | None
    analysis_mode: str
    hypothesis_attempts: int
    retry_hypothesis: bool
    rejected: list[str]
    related_recommendations: list[dict[str, Any]]
    proposed_actions: list[dict[str, Any]]
    forecast: dict[str, Any] | None
    report: dict[str, Any] | None


@dataclass
class Runtime:
    investigation_id: uuid.UUID
    tenant_id: uuid.UUID
    settings: Settings
    tools: EvidenceTools
    write_factory: sessionmaker[Session]
    model: InvestigationModel | None
    deadline: float
    steps: int = 0
    llm_calls: int = 0
    usage: dict[str, Any] = field(
        default_factory=lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": None,
            "llm_calls": 0,
        }
    )

    def check(self) -> None:
        with self.write_factory() as session:
            cancelled = session.scalar(
                select(Investigation.cancel_requested).where(
                    Investigation.id == self.investigation_id
                )
            )
        if cancelled:
            raise InvestigationCancelledError("cancelled by user")
        if self.steps >= self.settings.agent_max_steps:
            raise BudgetExceededError(f"step budget exhausted ({self.settings.agent_max_steps})")
        if time.monotonic() > self.deadline:
            raise BudgetExceededError(
                f"time budget exhausted ({self.settings.agent_timeout_seconds:.0f}s)"
            )

    def record(
        self,
        node: str,
        role: str,
        *,
        status: str,
        summary: str,
        output: dict[str, Any],
        evidence_refs: list[str],
        tool_calls: list[dict[str, Any]],
        latency_ms: float,
        mode: str,
        result: ModelResult | None = None,
        error: str | None = None,
    ) -> None:
        with self.write_factory() as session:
            session.add(
                AgentRun(
                    tenant_id=self.tenant_id,
                    investigation_id=self.investigation_id,
                    step_index=self.steps,
                    node=node,
                    agent_role=role,
                    status=status,
                    mode=mode,
                    summary=summary[:2000],
                    output=output,
                    evidence_refs=evidence_refs[:100],
                    tool_calls=tool_calls,
                    latency_ms=round(latency_ms, 2),
                    tokens_in=result.input_tokens if result else None,
                    tokens_out=result.output_tokens if result else None,
                    cost_usd=result.cost_usd(self.settings) if result else None,
                    error=error,
                )
            )
            session.commit()


def _ev(state: State) -> list[Evidence]:
    return [Evidence.model_validate(e) for e in state.get("evidence", [])]


def _add(state: State, items: list[Evidence]) -> list[dict[str, Any]]:
    existing = {e["id"] for e in state.get("evidence", [])}
    return [*state.get("evidence", []), *(i.model_dump() for i in items if i.id not in existing)]


def build_graph(rt: Runtime) -> Any:  # noqa: PLR0915 - one closure per graph node
    def step(name: str, role: str, fn: Callable[[State], NodeResult]) -> Any:
        def run(state: State) -> dict[str, Any]:
            with tracer.start_as_current_span(
                f"agent {name}",
                attributes={
                    "nimbrix.agent.role": role,
                    "nimbrix.investigation.id": str(rt.investigation_id),
                },
            ):
                return run_inner(state)

        def run_inner(state: State) -> dict[str, Any]:
            rt.check()
            started = time.perf_counter()
            calls_before = len(rt.tools.calls)
            update, summary, refs = fn(state)
            result = update.pop("_result", None)
            rt.steps += 1
            rt.record(
                name,
                role,
                status="ok",
                summary=summary,
                output={k: v for k, v in update.items() if k != "evidence"},
                evidence_refs=refs,
                tool_calls=rt.tools.calls[calls_before:],
                latency_ms=(time.perf_counter() - started) * 1000,
                mode=(result.provider if result else update.get("analysis_mode", "deterministic"))
                if name == "hypothesize"
                else "deterministic",
                result=result,
            )
            return update

        return run

    def triage(state: State) -> NodeResult:
        anomaly_id = state.get("anomaly_id")
        if not anomaly_id:
            return {"anomaly_found": False}, "No anomaly referenced; nothing to triage.", []
        try:
            anomaly, items = rt.tools.anomaly_context(uuid.UUID(anomaly_id))
        except ToolAccessDeniedError:
            return (
                {"anomaly_found": False},
                "Anomaly not visible to this tenant (access denied).",
                [],
            )
        contributors = rt.tools.contributors(anomaly)
        items.append(rt.tools.scope_cost_baseline(anomaly))
        items.extend(rt.tools.previous_incidents(anomaly))
        return (
            {"anomaly_found": True, "contributors": contributors, "evidence": _add(state, items)},
            f"Triaged {anomaly.severity} anomaly on {anomaly.scope_type} {anomaly.scope_value}; "
            f"{len(contributors)} candidate contributors.",
            [i.id for i in items],
        )

    def collect_evidence(state: State) -> NodeResult:
        ids = [c["resource_id"] for c in state.get("contributors", [])]
        resources = rt.tools.resource_inventory(ids)
        items = [*resources, rt.tools.pricing_assumptions()]
        return (
            {"evidence": _add(state, items)},
            f"Collected inventory for {len(resources)} of {len(ids)} contributing resources.",
            [i.id for i in items],
        )

    def forecast_context(state: State) -> NodeResult:
        row, _ = rt.tools.anomaly_context(uuid.UUID(str(state["anomaly_id"])))
        item = rt.tools.forecast_context(row)
        if item is None:
            return {"forecast": None}, "No forecast exists for this scope.", []
        return {"forecast": item.data, "evidence": _add(state, [item])}, item.summary, [item.id]

    def hypothesize(state: State) -> NodeResult:
        evidence = _ev(state)
        anomaly = _anomaly(state)
        assert anomaly is not None  # noqa: S101
        resources = {e.id: e for e in evidence if e.kind == "resource"}
        computed = deterministic.attribution(
            {**anomaly.data, "daily": [e.data for e in evidence if e.kind == "daily_cost"]},
            state.get("contributors", []),
            resources,
        )
        evidence.append(computed)
        attempts = state.get("hypothesis_attempts", 0) + 1
        rejected = list(state.get("rejected", []))
        update: dict[str, Any] = {
            "evidence": _add(state, [computed]),
            "hypothesis_attempts": attempts,
        }
        if rt.model is not None and rt.llm_calls < rt.settings.agent_max_llm_calls:
            rt.llm_calls += 1
            try:
                result = rt.model.analyze(state["question"], evidence)
                rt.usage["llm_calls"] += 1
                rt.usage["input_tokens"] += result.input_tokens or 0
                rt.usage["output_tokens"] += result.output_tokens or 0
                cost = result.cost_usd(rt.settings)
                if cost is not None:
                    rt.usage["cost_usd"] = str(Decimal(rt.usage["cost_usd"] or 0) + cost)
                AGENT_TOKENS.labels(provider=result.provider, direction="input").inc(
                    result.input_tokens or 0
                )
                AGENT_TOKENS.labels(provider=result.provider, direction="output").inc(
                    result.output_tokens or 0
                )
                errors = grounding_errors(result.analysis, evidence)
                AGENT_CALLS.labels(
                    provider=result.provider, outcome="rejected" if errors else "accepted"
                ).inc()
                if not errors:
                    update.update(
                        analysis=result.analysis.model_dump(),
                        analysis_mode=result.provider,
                        retry_hypothesis=False,
                        rejected=rejected,
                        _result=result,
                    )
                    return (
                        update,
                        f"{result.provider} analysis accepted ({len(result.analysis.hypotheses)} hypotheses).",
                        [i for h in result.analysis.hypotheses for i in h.evidence_ids],
                    )
                rejected += [f"attempt {attempts}: {e}" for e in errors]
                update["_result"] = result
            except (ProviderUnavailableError, MalformedOutputError, ProviderRefusalError) as exc:
                AGENT_CALLS.labels(provider=rt.model.provider, outcome=type(exc).__name__).inc()
                rejected.append(f"attempt {attempts}: {exc}")
            if (
                attempts < MAX_HYPOTHESIS_ATTEMPTS
                and rt.llm_calls < rt.settings.agent_max_llm_calls
            ):
                update.update(retry_hypothesis=True, rejected=rejected, analysis=None)
                return (
                    update,
                    f"Model output rejected; retrying ({attempts}/{MAX_HYPOTHESIS_ATTEMPTS}).",
                    [],
                )
        analysis = deterministic.analyze(anomaly, computed, list(resources.values()))
        update.update(
            analysis=analysis.model_dump(),
            analysis_mode="deterministic",
            retry_hypothesis=False,
            rejected=rejected,
        )
        return (
            update,
            "Deterministic analysis produced "
            + (
                f"{len(analysis.hypotheses)} hypotheses."
                if analysis.hypotheses
                else "an insufficient-evidence finding."
            ),
            [i for h in analysis.hypotheses for i in h.evidence_ids],
        )

    def synthesize_recommendations(state: State) -> NodeResult:
        anomaly = _anomaly(state)
        assert anomaly is not None  # noqa: S101
        resource_ids = [c["resource_id"] for c in state.get("contributors", [])]
        scope = anomaly.data
        items = rt.tools.recommendations_for(
            resource_ids,
            scope["scope_value"] if scope["scope_type"] == "account" else None,
            scope["scope_value"] if scope["scope_type"] == "team" else None,
        )
        related = [{"evidence_id": i.id, "summary": i.summary, **i.data} for i in items]
        return (
            {"related_recommendations": related, "evidence": _add(state, items)},
            f"Found {len(related)} related rule-based recommendations; no new savings figures are invented.",
            [i.id for i in items],
        )

    def policy_review(state: State) -> NodeResult:
        proposals = []
        for rec in state.get("related_recommendations", []):
            action = rec.get("proposed_action")
            if action not in REGISTRY or not rec.get("resource_id"):
                continue
            payload = {
                "resource_id": rec["resource_id"],
                "account_id": rec.get("account_id"),
                "region": rec.get("region"),
                "reason": f"Proposed by investigation {rt.investigation_id}",
                "recommendation_id": rec["evidence_id"].split(":", 1)[1],
            }
            result = evaluate_policy(
                rt.tools.session,
                rt.tenant_id,
                action,
                payload,
                inventory_max_age_hours=rt.settings.inventory_max_age_hours,
            )
            proposals.append(
                ProposedAction(
                    action_type=action,
                    resource_id=rec["resource_id"],
                    recommendation_id=payload["recommendation_id"],
                    estimated_monthly_savings=rec.get("estimated_monthly_savings"),
                    policy_allowed=result.allowed,
                    policy_denial_reasons=result.denial_reasons,
                ).model_dump()
            )
        return (
            {"proposed_actions": proposals},
            f"Reviewed {len(proposals)} allowlisted actions against policy (read-only; nothing requested or executed).",
            [],
        )

    def report(state: State) -> NodeResult:
        evidence = _ev(state)
        anomaly = _anomaly(state)
        analysis = (
            ModelAnalysis.model_validate(state["analysis"]) if state.get("analysis") else None
        )
        computed = next((e for e in evidence if e.id == "computed:attribution"), None)
        if anomaly is None or analysis is None:
            body = InvestigationReport(
                title="Investigation could not start",
                mode="deterministic",
                model=None,
                anomaly_id=state.get("anomaly_id"),
                summary="The referenced anomaly was not found for this tenant.",
                root_cause=None,
                hypotheses=[],
                insufficient_evidence=True,
                missing_evidence=["an anomaly visible to this tenant"],
                key_figures={},
                contributing_resources=[],
                forecast_context=None,
                related_recommendations=[],
                proposed_actions=[],
                evidence=evidence,
                limitations=[],
                model_output_rejected=state.get("rejected", []),
            )
            return {"report": body.model_dump()}, body.summary, []
        data = anomaly.data
        mode = state.get("analysis_mode", "deterministic")
        body = InvestigationReport(
            title=f"Cost anomaly: {data['scope_type']} {data['scope_value']} ({data['window_start']}..{data['window_end']})",
            mode=mode,
            model=rt.model.model if (rt.model is not None and mode != "deterministic") else None,
            anomaly_id=state.get("anomaly_id"),
            summary=analysis.narrative,
            root_cause=analysis.hypotheses[0] if analysis.hypotheses else None,
            hypotheses=analysis.hypotheses,
            insufficient_evidence=analysis.insufficient_evidence,
            missing_evidence=analysis.missing_evidence,
            key_figures={
                "observed": data["observed"],
                "expected": data["expected"],
                "currency": data["currency"],
                **(
                    {
                        k: str(v)
                        for k, v in computed.data.items()
                        if k.endswith("per_day") or k == "explained_pct"
                    }
                    if computed
                    else {}
                ),
            },
            contributing_resources=[
                {
                    **c,
                    "inventory": next(
                        (e.data for e in evidence if e.id == c.get("evidence_id")), None
                    ),
                }
                for c in state.get("contributors", [])
            ],
            forecast_context=state.get("forecast"),
            related_recommendations=state.get("related_recommendations", []),
            proposed_actions=[
                ProposedAction.model_validate(p) for p in state.get("proposed_actions", [])
            ],
            evidence=evidence,
            limitations=[
                "All dollar figures are computed deterministically from stored billing data; the model only explains them.",
                "Savings are estimates at list prices, never realized savings.",
                "Utilization evidence is CPU-only.",
                "Nothing was changed: investigations are read-only and cannot request or approve actions.",
            ],
            model_output_rejected=state.get("rejected", []),
        )
        return {"report": body.model_dump(mode="json")}, "Report assembled.", []

    graph = StateGraph(State)
    graph.add_node("triage", step("triage", "Cost Analyst Agent", triage))
    graph.add_node(
        "collect_evidence",
        step("collect_evidence", "Infrastructure Investigator", collect_evidence),
    )
    graph.add_node(
        "forecast_context", step("forecast_context", "Forecast Analyst", forecast_context)
    )
    graph.add_node("hypothesize", step("hypothesize", "Cost Analyst Agent", hypothesize))
    graph.add_node(
        "synthesize_recommendations",
        step("synthesize_recommendations", "Optimization Planner", synthesize_recommendations),
    )
    graph.add_node("policy_review", step("policy_review", "Optimization Planner", policy_review))
    graph.add_node("report", step("report", "Report Agent", report))
    graph.add_edge(START, "triage")
    graph.add_conditional_edges(
        "triage",
        lambda s: "collect_evidence" if s.get("anomaly_found") else "report",
        {"collect_evidence": "collect_evidence", "report": "report"},
    )
    graph.add_edge("collect_evidence", "forecast_context")
    graph.add_edge("forecast_context", "hypothesize")
    graph.add_conditional_edges(
        "hypothesize",
        lambda s: "hypothesize" if s.get("retry_hypothesis") else "synthesize_recommendations",
        {"hypothesize": "hypothesize", "synthesize_recommendations": "synthesize_recommendations"},
    )
    graph.add_edge("synthesize_recommendations", "policy_review")
    graph.add_edge("policy_review", "report")
    graph.add_edge("report", END)
    return graph.compile()


def _anomaly(state: State) -> Evidence | None:
    return next(
        (Evidence.model_validate(e) for e in state.get("evidence", []) if e["kind"] == "anomaly"),
        None,
    )


def now_iso() -> str:
    return datetime.now(UTC).isoformat()

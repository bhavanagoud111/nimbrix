"""Deterministic investigator: the default (and fallback) analysis, no LLM involved.

All figures it states are computed here from evidence and recorded as a ``computed:*``
evidence item, so every number in a report traces back to stored data.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from nimbrix_api.modules.agents.schemas import Evidence, Hypothesis, ModelAnalysis

CENTS = Decimal("0.01")


def _d(value: Any) -> Decimal:
    return Decimal(str(value)) if value not in (None, "") else Decimal(0)


def attribution(
    anomaly: dict[str, Any], contributors: list[dict[str, Any]], resources: dict[str, Evidence]
) -> Evidence:
    window_days = len(anomaly.get("daily", [])) or 1
    excess = ((_d(anomaly["observed"]) - _d(anomaly["expected"])) / window_days).quantize(CENTS)
    new = [c for c in contributors if c.get("new_in_window")]
    existing = [c for c in contributors if not c.get("new_in_window")]
    new_delta = sum((_d(c["delta_per_day"]) for c in new), Decimal(0)).quantize(CENTS)
    existing_delta = sum((_d(c["delta_per_day"]) for c in existing), Decimal(0)).quantize(CENTS)
    explained = (
        ((new_delta + existing_delta) / excess * 100).quantize(Decimal("0.1"))
        if excess > 0
        else Decimal(0)
    )

    groups: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)
    for c in new:
        r = resources.get(f"resource:{c['resource_id']}")
        data = r.data if r else {}
        key = (
            str(data.get("instance_type") or data.get("resource_type") or "resource"),
            str(data.get("team") or "untagged"),
            str(data.get("environment") or "untagged"),
            str(data.get("account_id") or "unknown"),
        )
        groups[key].append(c["resource_id"])
    return Evidence(
        id="computed:attribution",
        kind="computed",
        summary=(
            f"Average excess {excess}/day over the anomaly window; new resources add {new_delta}/day, "
            f"existing resources {existing_delta}/day ({explained}% of the excess explained)"
        ),
        data={
            "excess_per_day": str(excess),
            "new_resources_delta_per_day": str(new_delta),
            "existing_resources_delta_per_day": str(existing_delta),
            "explained_pct": str(explained),
            "window_days": window_days,
            "new_resource_groups": [
                {
                    "instance_type": k[0],
                    "team": k[1],
                    "environment": k[2],
                    "account_id": k[3],
                    "resource_ids": ids,
                    "first_seen": min(
                        (c["first_seen"] for c in new if c["resource_id"] in ids), default=None
                    ),
                }
                for k, ids in groups.items()
            ],
            "method": "per-resource average daily cost in the window minus the 28-day pre-window average",
        },
    )


def analyze(
    anomaly_evidence: Evidence, attribution_evidence: Evidence, resource_evidence: list[Evidence]
) -> ModelAnalysis:
    a = attribution_evidence.data
    explained = _d(a["explained_pct"])
    scope = f"{anomaly_evidence.data['scope_type']} {anomaly_evidence.data['scope_value']}"
    hypotheses: list[Hypothesis] = []
    groups = a["new_resource_groups"]
    if groups:
        group = max(groups, key=lambda g: len(g["resource_ids"]))
        cited = [
            anomaly_evidence.id,
            attribution_evidence.id,
            *(f"resource:{rid}" for rid in group["resource_ids"][:8]),
        ]
        confidence = "high" if explained >= 80 else "medium" if explained >= 50 else "low"
        hypotheses.append(
            Hypothesis(
                statement=(
                    f"{len(group['resource_ids'])} new {group['instance_type']} resources (team {group['team']}, "
                    f"environment {group['environment']}, account {group['account_id']}) first appeared on "
                    f"{group['first_seen']} and add {a['new_resources_delta_per_day']}/day, explaining "
                    f"{a['explained_pct']}% of the {a['excess_per_day']}/day excess in {scope}."
                ),
                evidence_ids=[i for i in cited if i],
                confidence=confidence,
            )
        )
    if _d(a["existing_resources_delta_per_day"]) > 0:
        hypotheses.append(
            Hypothesis(
                statement=(
                    f"Existing resources increased by {a['existing_resources_delta_per_day']}/day compared with the "
                    "28 days before the anomaly."
                ),
                evidence_ids=[anomaly_evidence.id, attribution_evidence.id],
                confidence="low",
            )
        )
    missing: list[str] = []
    if not hypotheses:
        missing.append("resource-level billing (resource IDs) for the anomaly scope, e.g. CUR")
    elif explained < 50:
        missing.append("cost drivers without resource IDs (shared or service-level charges)")
    insufficient = not hypotheses
    narrative = (
        hypotheses[0].statement
        if hypotheses
        else f"The increase in {scope} cannot be attributed: the billing data has no resource-level detail."
    )
    if hypotheses and explained < 50:
        narrative += " The identified resources explain less than half of the increase, so other drivers are likely."
    return ModelAnalysis(
        hypotheses=hypotheses,
        narrative=narrative[:2000],
        insufficient_evidence=insufficient,
        missing_evidence=missing,
    )

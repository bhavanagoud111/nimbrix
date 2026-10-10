"""Grounding checks for model output: citations, resource IDs, and money must exist in evidence."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

from nimbrix_api.modules.agents.schemas import Evidence, ModelAnalysis

_MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)|(\d[\d,]*(?:\.\d+)?)\s?(?:USD|usd|dollars)")
_RESOURCE_ID = re.compile(r"\b(?:i|vol|eipalloc|eni|snap|db|sg)-[0-9a-zA-Z]{6,}\b")
_ACCOUNT = re.compile(r"\b\d{12}\b")


def _amounts(text: str) -> list[Decimal]:
    found = []
    for match in _MONEY.finditer(text):
        raw = (match.group(1) or match.group(2) or "").replace(",", "")
        try:
            found.append(Decimal(raw))
        except InvalidOperation:
            continue
    return found


def _numbers_in(blob: str) -> set[Decimal]:
    numbers = set()
    for raw in re.findall(r"-?\d+(?:\.\d+)?", blob):
        try:
            numbers.add(Decimal(raw).quantize(Decimal("0.01")))
        except InvalidOperation:
            continue
    return numbers


def grounding_errors(analysis: ModelAnalysis, evidence: list[Evidence]) -> list[str]:
    """Empty when every citation, resource ID, account, and dollar figure is grounded."""
    errors: list[str] = []
    ids = {e.id for e in evidence}
    blob = json.dumps([e.model_dump() for e in evidence], default=str)
    known_numbers = _numbers_in(blob)
    texts = [analysis.narrative, *(h.statement for h in analysis.hypotheses)]
    for hypothesis in analysis.hypotheses:
        unknown = [i for i in hypothesis.evidence_ids if i not in ids]
        if unknown:
            errors.append(f"cited unknown evidence ids: {', '.join(sorted(set(unknown))[:5])}")
        if hypothesis.confidence == "high" and len(set(hypothesis.evidence_ids)) < 2:
            errors.append("high confidence requires at least two independent evidence items")
    for text in texts:
        for rid in _RESOURCE_ID.findall(text):
            if rid not in blob:
                errors.append(f"mentioned a resource not in evidence: {rid}")
        for account in _ACCOUNT.findall(text):
            if account not in blob:
                errors.append(f"mentioned an account not in evidence: {account}")
        for amount in _amounts(text):
            if amount.quantize(Decimal("0.01")) not in known_numbers:
                errors.append(f"stated a dollar amount not in evidence: {amount}")
    if not analysis.hypotheses and not analysis.insufficient_evidence:
        errors.append("no hypotheses and insufficient_evidence is false")
    return sorted(set(errors))

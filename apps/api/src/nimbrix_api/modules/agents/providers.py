"""Provider-neutral model interface (ADR-0004).

``InvestigationModel.analyze`` takes a question plus evidence and returns a validated
``ModelAnalysis``. Adapters: Anthropic (official SDK), OpenAI and Ollama (HTTP). The
deterministic investigator (``deterministic.py``) needs no provider and is the default.
API keys stay server-side and never enter prompts or logs.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

import anthropic
import httpx2
from pydantic import ValidationError

from nimbrix_api.core.config import LlmProvider, Settings
from nimbrix_api.modules.agents.prompts import SYSTEM_PROMPT, user_prompt
from nimbrix_api.modules.agents.schemas import Evidence, ModelAnalysis

# Hand-written schema: only keywords every structured-output implementation accepts.
# Length and count limits are enforced afterwards by Pydantic validation.
ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["hypotheses", "narrative", "insufficient_evidence", "missing_evidence"],
    "properties": {
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["statement", "evidence_ids", "confidence"],
                "properties": {
                    "statement": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "confidence": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "insufficient_evidence"],
                    },
                },
            },
        },
        "narrative": {"type": "string"},
        "insufficient_evidence": {"type": "boolean"},
        "missing_evidence": {"type": "array", "items": {"type": "string"}},
    },
}


class ProviderUnavailableError(Exception):
    """Network failure, timeout, rate limit, auth error, or 5xx: the provider could not answer."""


class MalformedOutputError(Exception):
    """The provider answered, but not with JSON matching the schema."""


class ProviderRefusalError(Exception):
    """The provider declined to answer."""


@dataclass(frozen=True)
class ModelResult:
    analysis: ModelAnalysis
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: float

    def cost_usd(self, settings: Settings) -> Decimal | None:
        if None in (self.input_tokens, self.output_tokens):
            return None
        in_price, out_price = (
            settings.llm_input_usd_per_million_tokens,
            settings.llm_output_usd_per_million_tokens,
        )
        if in_price is None or out_price is None:
            return None
        assert self.input_tokens is not None  # noqa: S101
        assert self.output_tokens is not None  # noqa: S101
        return (
            Decimal(self.input_tokens) * in_price + Decimal(self.output_tokens) * out_price
        ) / Decimal(1_000_000)


class InvestigationModel(Protocol):
    provider: str
    model: str

    def analyze(self, question: str, evidence: list[Evidence]) -> ModelResult: ...


def parse_analysis(raw: str) -> ModelAnalysis:
    try:
        return ModelAnalysis.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise MalformedOutputError(
            f"model output did not match the schema ({type(exc).__name__})"
        ) from None


class AnthropicModel:
    provider = "anthropic"

    def __init__(self, settings: Settings, client: Any = None) -> None:
        assert settings.llm_model is not None  # noqa: S101 - enforced by settings validation
        self.model = settings.llm_model
        self.settings = settings
        api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
        self.client = client or anthropic.Anthropic(
            api_key=api_key,
            base_url=settings.llm_base_url or None,
            timeout=settings.llm_timeout_seconds,
            max_retries=1,
        )

    def analyze(self, question: str, evidence: list[Evidence]) -> ModelResult:
        started = time.perf_counter()
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.settings.llm_max_output_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt(question, evidence)}],
                output_config={"format": {"type": "json_schema", "schema": ANALYSIS_SCHEMA}},
            )
        except (
            anthropic.APIConnectionError,
            anthropic.RateLimitError,
            anthropic.AuthenticationError,
        ) as exc:
            raise ProviderUnavailableError(f"anthropic: {type(exc).__name__}") from None
        except anthropic.APIStatusError as exc:
            raise ProviderUnavailableError(f"anthropic: HTTP {exc.status_code}") from None
        if response.stop_reason == "refusal":
            raise ProviderRefusalError("anthropic: the model declined to answer")
        if response.stop_reason == "max_tokens":
            raise MalformedOutputError("anthropic: output truncated at max_tokens")
        text = next(
            (b.text for b in response.content if isinstance(b, anthropic.types.TextBlock)), ""
        )
        return ModelResult(
            analysis=parse_analysis(text),
            provider=self.provider,
            model=self.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=(time.perf_counter() - started) * 1000,
        )


class _HttpModel:
    provider = "http"

    def __init__(self, settings: Settings, transport: httpx2.BaseTransport | None = None) -> None:
        assert settings.llm_model is not None  # noqa: S101
        self.model = settings.llm_model
        self.settings = settings
        self.client = httpx2.Client(timeout=settings.llm_timeout_seconds, transport=transport)

    def _post(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        try:
            response = self.client.post(url, json=body, headers=headers)
        except httpx2.HTTPError as exc:
            raise ProviderUnavailableError(f"{self.provider}: {type(exc).__name__}") from None
        if response.status_code >= 400:
            raise ProviderUnavailableError(f"{self.provider}: HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError:
            raise MalformedOutputError(f"{self.provider}: response was not JSON") from None
        if not isinstance(data, dict):
            raise MalformedOutputError(f"{self.provider}: unexpected response shape")
        return data


class OpenAIModel(_HttpModel):
    provider = "openai"

    def analyze(self, question: str, evidence: list[Evidence]) -> ModelResult:
        started = time.perf_counter()
        base = (self.settings.llm_base_url or "https://api.openai.com").rstrip("/")
        key = self.settings.llm_api_key.get_secret_value() if self.settings.llm_api_key else ""
        data = self._post(
            f"{base}/v1/chat/completions",
            {
                "model": self.model,
                "max_completion_tokens": self.settings.llm_max_output_tokens,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt(question, evidence)},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "model_analysis",
                        "strict": True,
                        "schema": ANALYSIS_SCHEMA,
                    },
                },
            },
            {"Authorization": f"Bearer {key}"},
        )
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise MalformedOutputError("openai: response had no message") from None
        if message.get("refusal"):
            raise ProviderRefusalError("openai: the model declined to answer")
        usage = data.get("usage") or {}
        return ModelResult(
            analysis=parse_analysis(message.get("content") or ""),
            provider=self.provider,
            model=self.model,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            latency_ms=(time.perf_counter() - started) * 1000,
        )


class OllamaModel(_HttpModel):
    provider = "ollama"

    def analyze(self, question: str, evidence: list[Evidence]) -> ModelResult:
        started = time.perf_counter()
        base = (self.settings.llm_base_url or "http://localhost:11434").rstrip("/")
        data = self._post(
            f"{base}/api/chat",
            {
                "model": self.model,
                "stream": False,
                "format": ANALYSIS_SCHEMA,
                "options": {"temperature": 0, "num_predict": self.settings.llm_max_output_tokens},
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt(question, evidence)},
                ],
            },
            {},
        )
        content = (data.get("message") or {}).get("content", "")
        return ModelResult(
            analysis=parse_analysis(content),
            provider=self.provider,
            model=self.model,
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
            latency_ms=(time.perf_counter() - started) * 1000,
        )


def build_model(settings: Settings) -> InvestigationModel | None:
    """The configured LLM adapter, or None for deterministic-only mode (the default)."""
    match settings.llm_provider:
        case LlmProvider.ANTHROPIC:
            return AnthropicModel(settings)
        case LlmProvider.OPENAI:
            return OpenAIModel(settings)
        case LlmProvider.OLLAMA:
            return OllamaModel(settings)
        case _:
            return None

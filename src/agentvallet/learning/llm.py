"""Optional LLM providers used for skill synthesis and repair.

AgentVallet works fully without an LLM (``NullProvider``): learning and repair then rely on
recorded code, mined invariants and human corrections. Plugging in a provider lets the engine
propose ``run.py`` implementations and patches, which are *always* re-validated and land as
drafts that a human must approve.
"""

from __future__ import annotations

import re
from typing import Protocol

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"


class LLMError(RuntimeError):
    pass


class LLMProvider(Protocol):
    name: str

    @property
    def available(self) -> bool: ...

    def complete(self, system: str, prompt: str, max_tokens: int = 16000) -> str: ...


class NullProvider:
    name = "none"

    @property
    def available(self) -> bool:
        return False

    def complete(self, system: str, prompt: str, max_tokens: int = 16000) -> str:
        raise LLMError("no LLM provider configured (set AV_LLM_PROVIDER=anthropic)")


class AnthropicProvider:
    """Claude via the official Anthropic SDK (``pip install agentvallet[llm]``)."""

    name = "anthropic"

    def __init__(self, model: str | None = None):
        self.model = model or DEFAULT_ANTHROPIC_MODEL
        try:
            import anthropic

            self._anthropic = anthropic
            self._client = anthropic.Anthropic()
        except Exception as exc:  # ImportError or missing credentials
            self._client = None
            self._error = str(exc)

    @property
    def available(self) -> bool:
        return self._client is not None

    def complete(self, system: str, prompt: str, max_tokens: int = 16000) -> str:
        if self._client is None:
            raise LLMError(f"anthropic provider unavailable: {self._error}")
        a = self._anthropic
        try:
            # Server-side refusal fallback: if the primary model declines, the API re-runs the
            # request on the fallback model within the same call.
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                thinking={"type": "adaptive"},
                betas=["server-side-fallback-2026-06-01"],
                fallbacks=[{"model": "claude-opus-4-8"}],
                messages=[{"role": "user", "content": prompt}],
            )
        except a.RateLimitError as exc:
            raise LLMError(f"rate limited: {exc}") from exc
        except a.APIStatusError as exc:
            raise LLMError(f"API error {exc.status_code}: {exc.message}") from exc
        except a.APIConnectionError as exc:
            raise LLMError(f"network error: {exc}") from exc
        if response.stop_reason == "refusal":
            raise LLMError("model declined the request")
        text = "".join(b.text for b in response.content if b.type == "text")
        if not text:
            raise LLMError("empty response")
        return text


def get_provider(name: str = "none", model: str | None = None) -> LLMProvider:
    name = (name or "none").lower()
    if name in ("anthropic", "claude"):
        return AnthropicProvider(model)
    return NullProvider()


_CODE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)


def extract_code(text: str) -> str:
    """Pull the first fenced Python block out of a model response (or the whole text)."""
    m = _CODE_RE.search(text)
    return (m.group(1) if m else text).strip() + "\n"

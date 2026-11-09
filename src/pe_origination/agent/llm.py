"""Model providers behind one tiny interface: ``complete_json(system, user) -> str``."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol


class LLMClient(Protocol):
    def complete_json(self, system: str, user: str) -> str: ...


class OpenAIClient:
    def __init__(self, model: str, temperature: float | None = None) -> None:
        try:
            from openai import OpenAI
        except ImportError as e:
            raise ImportError("pip install 'pe-origination-agents[openai]'") from e
        self.client = OpenAI()  # reads OPENAI_API_KEY
        self.model = model
        self.temperature = temperature

    def complete_json(self, system: str, user: str) -> str:
        kwargs = {}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        r = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            **kwargs,
        )
        return r.choices[0].message.content or ""


class AnthropicClient:
    def __init__(self, model: str, temperature: float | None = None) -> None:
        try:
            import anthropic
        except ImportError as e:
            raise ImportError("pip install 'pe-origination-agents[anthropic]'") from e
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self.model = model
        self.temperature = temperature

    def complete_json(self, system: str, user: str) -> str:
        kwargs = {}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        r = self.client.messages.create(
            model=self.model,
            max_tokens=3000,
            system=system,
            messages=[{"role": "user", "content": user}],
            **kwargs,
        )
        return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")


class FakeLLM:
    """Deterministic client for tests and dry runs."""

    def __init__(self, responses: Sequence[str] | Callable[[str, str], str]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if callable(self._responses):
            return self._responses(system, user)
        return self._responses[min(len(self.calls), len(self._responses)) - 1]


def make_client(provider: str, model: str, temperature: float | None = None) -> LLMClient:
    if provider == "openai":
        return OpenAIClient(model, temperature)
    if provider == "anthropic":
        return AnthropicClient(model, temperature)
    raise ValueError(f"unknown provider '{provider}'")

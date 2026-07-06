"""Provider-agnostic LLM client ("bring your own LLM").

A single, stable interface (`complete`) used by BOTH the extraction layer
(topic_extractor.py, usefulness_filter.py) and the agent layer (ideation.py,
research.py, polish.py, trail_quality.py), so the whole pipeline runs on ONE
configured LLM provider instead of requiring a Gemini key for topics plus a
separate Anthropic key for trail generation.

Configuration is read from the environment (see .env.example):

    LLM_PROVIDER      "anthropic" (default) | "openai"
    LLM_MODEL         model name for the configured provider
                      (default: "claude-sonnet-5" for anthropic,
                       "gpt-4o-mini" for openai)

    # anthropic path (default — the tool runs on ONE Claude key, no Google)
    ANTHROPIC_API_KEY required when LLM_PROVIDER=anthropic

    # openai path — any OpenAI-compatible endpoint: OpenAI itself, OpenRouter
    # (which can also route to Claude/Gemini/Llama/etc.), or a local server
    # such as Ollama / LM Studio.
    OPENAI_API_KEY    required when LLM_PROVIDER=openai
    OPENAI_BASE_URL   optional override, e.g. https://openrouter.ai/api/v1
                      or http://localhost:11434/v1

Retries transient errors (rate limits, overload, 5xx) with exponential
backoff. This behavior used to live only in topic_extractor.py's Gemini-only
retry loop; centralizing it here means every LLM call in the pipeline
(extraction AND agent) now benefits from it.
"""

import os
import time
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

DEFAULT_PROVIDER = "anthropic"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"

# Substrings (checked against str(exception).lower()) that mark a transient,
# retry-worthy failure: rate limits and overload (the original Gemini-era
# markers) plus generic server/connection hiccups, relevant for
# self-hosted OpenAI-compatible endpoints (Ollama, LM Studio) too.
_TRANSIENT_MARKERS = (
    "503", "unavailable", "overloaded", "429",
    "resource_exhausted", "rate limit", "rate_limit", "deadline",
    "500", "502", "504", "timeout", "connection",
)


class LLMError(RuntimeError):
    """Raised when an LLM call fails after all retry attempts are exhausted."""


class LLMClient:
    """Thin, provider-agnostic wrapper around the Anthropic and OpenAI SDKs.

    Construct with no args to pick up LLM_PROVIDER/LLM_MODEL/*_API_KEY from
    the environment. Pass explicit `model=` (or `provider=`) to override just
    that piece for a single instance (e.g. a cheaper model for one script)
    while still reading the rest of the config (keys, base_url) from env.
    """

    def __init__(self, provider: Optional[str] = None, model: Optional[str] = None,
                 api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.provider = (provider or os.getenv("LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()

        if self.provider == "anthropic":
            import anthropic
            key = api_key or os.getenv("ANTHROPIC_API_KEY")
            if not key:
                raise ValueError(
                    "ANTHROPIC_API_KEY not set (required when LLM_PROVIDER=anthropic, the default)."
                )
            self.model = model or os.getenv("LLM_MODEL") or DEFAULT_ANTHROPIC_MODEL
            self._client = anthropic.Anthropic(api_key=key)

        elif self.provider == "openai":
            import openai
            key = api_key or os.getenv("OPENAI_API_KEY")
            if not key:
                raise ValueError(
                    "OPENAI_API_KEY not set (required when LLM_PROVIDER=openai)."
                )
            resolved_base_url = base_url or os.getenv("OPENAI_BASE_URL") or None
            self.model = model or os.getenv("LLM_MODEL") or DEFAULT_OPENAI_MODEL
            client_kwargs = {"api_key": key}
            if resolved_base_url:
                client_kwargs["base_url"] = resolved_base_url
            self._client = openai.OpenAI(**client_kwargs)

        else:
            raise ValueError(
                f"Unsupported LLM_PROVIDER '{self.provider}' — expected 'anthropic' or 'openai'."
            )

    def complete(self, system: str, prompt: str, *, max_tokens: int,
                 temperature: float = 1.0, max_attempts: int = 5) -> str:
        """Call the configured LLM and return its text response.

        `system` may be an empty string for callers that fold everything into
        the user prompt (all current callers in this codebase do). Retries
        transient errors (rate limits, overload, 5xx) with exponential
        backoff (2s, 4s, 8s, ...) before raising LLMError.
        """
        delay = 2.0
        last_err: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                if self.provider == "anthropic":
                    return self._complete_anthropic(system, prompt, max_tokens, temperature)
                return self._complete_openai(system, prompt, max_tokens, temperature)
            except Exception as e:  # noqa: BLE001 - classified into transient/fatal below
                last_err = e
                msg = str(e).lower()
                transient = any(m in msg for m in _TRANSIENT_MARKERS)
                if transient and attempt < max_attempts:
                    print(f"  LLM transient error (attempt {attempt}/{max_attempts}), "
                          f"retrying in {delay:.0f}s…")
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise LLMError(f"LLM call failed ({self.provider}/{self.model}): {e}") from e
        raise LLMError(f"LLM call failed after {max_attempts} attempts: {last_err}")

    def _complete_anthropic(self, system: str, prompt: str, max_tokens: int,
                             temperature: float) -> str:
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        msg = self._client.messages.create(**kwargs)
        # Some Claude models/configurations may lead with a non-text content
        # block (e.g. extended thinking); take the first TEXT block rather
        # than assuming content[0] is it.
        block = next((b for b in msg.content if getattr(b, "type", "") == "text"), None)
        if block is None:
            raise LLMError("no text block in Anthropic response")
        return block.text

    def _complete_openai(self, system: str, prompt: str, max_tokens: int,
                          temperature: float) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=messages,
        )
        return resp.choices[0].message.content


@lru_cache(maxsize=None)
def get_client(provider: Optional[str] = None, model: Optional[str] = None) -> LLMClient:
    """Shared, cached LLMClient. Safe to call repeatedly (e.g. once per chunk
    during topic extraction over tens of thousands of chunks) without
    re-constructing the underlying SDK client every time.

    Omit both args for the default configured client. Pass an explicit
    `model=` (and/or `provider=`) to get a distinct cached instance — e.g. a
    cheaper model for a specific script — without disturbing the default.
    """
    return LLMClient(provider=provider, model=model)

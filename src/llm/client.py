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

import json
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


class LLMJSONError(LLMError):
    """Raised when the model's structured-JSON output is missing, truncated, or
    unparseable on a single attempt. `complete_json` catches this internally and
    retries (with a larger max_tokens); callers only ever see LLMError after all
    attempts are exhausted."""


def _strip_code_fences(text: str) -> str:
    """Strip a ```json ... ``` (or bare ```) fence from a model response.

    Only used on the OpenAI-compatible fallback path where the response is
    free text; the Anthropic tool-use path never needs this (the tool input
    is structured by construction)."""
    t = text.strip()
    if "```json" in t:
        t = t.split("```json", 1)[1].split("```", 1)[0].strip()
    elif t.startswith("```"):
        t = t.split("```", 1)[1].split("```", 1)[0].strip()
    return t


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
            # Some OpenAI-compatible endpoints (older Ollama/LM Studio builds,
            # certain OpenRouter routes) reject response_format JSON mode. Start
            # optimistic; flip to False permanently on the first rejection so we
            # don't pay a failed request per call.
            self._openai_json_mode = True

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

    # ------------------------------------------------------------------
    # Structured JSON output
    # ------------------------------------------------------------------

    def complete_json(self, system: str, prompt: str, *, schema: Optional[dict] = None,
                      max_tokens: int = 4096, max_attempts: int = 3,
                      tool_name: str = "record_json",
                      tool_description: str = "Record the structured JSON result for this task.") -> dict:
        """Call the configured LLM and return a parsed JSON object — reliable by
        construction, replacing bare `json.loads()` on free-text responses.

        Anthropic path: forced TOOL USE — a single tool whose input_schema is
        `schema`, with tool_choice forcing that tool. The returned tool-call
        `input` is structured JSON by construction (never a truncated string).

        OpenAI-compatible path: JSON mode (`response_format={"type":"json_object"}`)
        when the endpoint supports it, with a permanent per-client fallback to
        plain completion + fence-stripping + json.loads when it doesn't.

        Reliability:
        - Generous default max_tokens (4096) so rich responses can't truncate.
        - Truncation IS detected (Anthropic `stop_reason == "max_tokens"`,
          OpenAI `finish_reason == "length"`) and retried with a doubled
          max_tokens (capped at 16384).
        - Any parse/shape failure retries up to `max_attempts`; transient API
          errors retry with exponential backoff.
        - After exhausting attempts, raises a clear, catchable LLMError — never
          a bare json.JSONDecodeError traceback.

        `schema` is a JSON Schema for the desired object. On the Anthropic path
        it is enforced server-side as the tool's input_schema; on both paths the
        result's required top-level keys (schema["required"]) are validated
        before returning. `schema=None` accepts any JSON object.
        """
        delay = 2.0
        tokens = max_tokens
        last_err: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                if self.provider == "anthropic":
                    data = self._complete_json_anthropic(
                        system, prompt, tokens, schema, tool_name, tool_description)
                else:
                    data = self._complete_json_openai(system, prompt, tokens)
                self._validate_json_result(data, schema)
                return data
            except LLMJSONError as e:
                last_err = e
                if attempt < max_attempts:
                    next_tokens = min(tokens * 2, 16384)
                    print(f"  LLM JSON output invalid (attempt {attempt}/{max_attempts}): {e} — "
                          f"retrying with max_tokens={next_tokens}…")
                    tokens = next_tokens
                    continue
            except Exception as e:  # noqa: BLE001 - classified transient/fatal below
                last_err = e
                msg = str(e).lower()
                if any(m in msg for m in _TRANSIENT_MARKERS) and attempt < max_attempts:
                    print(f"  LLM transient error (attempt {attempt}/{max_attempts}), "
                          f"retrying in {delay:.0f}s…")
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise LLMError(
                    f"LLM structured-JSON call failed ({self.provider}/{self.model}): {e}"
                ) from e
        raise LLMError(
            f"LLM structured-JSON call failed after {max_attempts} attempts "
            f"({self.provider}/{self.model}): {last_err}"
        ) from last_err

    def _complete_json_anthropic(self, system: str, prompt: str, max_tokens: int,
                                 schema: Optional[dict], tool_name: str,
                                 tool_description: str) -> dict:
        """Structured output via forced tool use — valid JSON by construction.

        No `temperature`/`thinking` overrides here: newer Anthropic models
        (Opus 4.7+/Fable 5) reject non-default sampling params outright, and the
        defaults are correct for every model this repo supports.
        """
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [{
                "name": tool_name,
                "description": tool_description,
                "input_schema": schema or {"type": "object"},
            }],
            "tool_choice": {"type": "tool", "name": tool_name},
        }
        if system:
            kwargs["system"] = system
        msg = self._client.messages.create(**kwargs)
        if getattr(msg, "stop_reason", None) == "max_tokens":
            raise LLMJSONError(f"response truncated at max_tokens={max_tokens}")
        # A thinking/text block may precede the tool_use block — take the first
        # tool_use block rather than assuming content[0] (same defensive pattern
        # as _complete_anthropic's text-block handling).
        block = next((b for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
        if block is None:
            raise LLMJSONError("no tool_use block in Anthropic response "
                               "(model did not emit the forced structured output)")
        data = block.input
        if not isinstance(data, dict):
            raise LLMJSONError(f"tool input is {type(data).__name__}, expected a JSON object")
        return data

    def _complete_json_openai(self, system: str, prompt: str, max_tokens: int) -> dict:
        """Structured output via OpenAI JSON mode, with graceful degradation for
        OpenAI-compatible endpoints that don't support response_format."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        base_kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if self._openai_json_mode:
            try:
                resp = self._client.chat.completions.create(
                    response_format={"type": "json_object"}, **base_kwargs)
            except Exception as e:  # endpoint may not support JSON mode
                emsg = str(e).lower()
                if "response_format" in emsg or "json_object" in emsg:
                    self._openai_json_mode = False
                    print("  Note: this OpenAI-compatible endpoint lacks JSON mode "
                          "(response_format) — falling back to plain completion + parse.")
                    resp = self._client.chat.completions.create(**base_kwargs)
                else:
                    raise
        else:
            resp = self._client.chat.completions.create(**base_kwargs)
        choice = resp.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            raise LLMJSONError(f"response truncated at max_tokens={max_tokens}")
        text = choice.message.content or ""
        try:
            data = json.loads(_strip_code_fences(text))
        except json.JSONDecodeError as e:
            raise LLMJSONError(
                f"unparseable JSON from model: {e}; response head: {text[:200]!r}"
            ) from e
        if not isinstance(data, dict):
            raise LLMJSONError(f"parsed JSON is {type(data).__name__}, expected an object")
        return data

    @staticmethod
    def _validate_json_result(data: dict, schema: Optional[dict]) -> None:
        """Light shape check: required top-level keys from the schema must be
        present. (Anthropic enforces the full schema server-side; this guards
        the OpenAI JSON-mode / fallback paths.)"""
        if not schema:
            return
        missing = [k for k in schema.get("required", []) if k not in data]
        if missing:
            raise LLMJSONError(f"JSON object missing required key(s): {', '.join(missing)}")


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

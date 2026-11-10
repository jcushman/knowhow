"""Structured model calls.

Every LLM call in the project goes through `LLM.parse`, which returns a
validated Pydantic object. `make_llm` picks a backend from the model name:
`openrouter:<slug>` goes to OpenRouter, `local:<model>` to any server with an
OpenAI-compatible chat completions API (KNOWHOW_MODEL_BASE_URL), anything
else to the Anthropic API.
`ScriptedLLM` stands in for tests and offline dry runs.
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
from collections import defaultdict
from typing import Callable, Protocol, TypeVar

import anthropic
import httpx
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    pass


# Failures a caller can recover from by trying again later: the model's
# output was unusable, or the provider or network failed.
TRANSIENT_ERRORS = (LLMError, anthropic.APIError, httpx.HTTPError)


class LLM(Protocol):
    def parse(self, *, purpose: str, system: str, user: str, schema: type[T], effort: str) -> T: ...

    def usage_report(self) -> dict[str, dict[str, float]]: ...


OPENROUTER_PREFIX = "openrouter:"
LOCAL_PREFIX = "local:"
BASE_URL_ENV = "KNOWHOW_MODEL_BASE_URL"
LOCAL_KEY_ENV = "KNOWHOW_MODEL_API_KEY"
EXTRA_BODY_ENV = "KNOWHOW_MODEL_EXTRA_BODY"


def make_llm(model: str, fallbacks: bool = True) -> "LLM":
    if model.startswith(LOCAL_PREFIX):
        return OpenRouterLLM(model.removeprefix(LOCAL_PREFIX), local=True)
    if model.startswith(OPENROUTER_PREFIX):
        return OpenRouterLLM(model.removeprefix(OPENROUTER_PREFIX))
    return AnthropicLLM(model, fallbacks=fallbacks)


class AnthropicLLM:
    """Calls one model. Credentials come from the SDK's normal resolution
    (ANTHROPIC_API_KEY, or an `ant auth login` profile)."""

    def __init__(self, model: str, fallbacks: bool = True, max_tokens: int = 16000):
        self.client = anthropic.Anthropic()
        self.model = model
        self.fallbacks = fallbacks
        self.max_tokens = max_tokens
        self._usage: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._lock = threading.Lock()

    def parse(self, *, purpose: str, system: str, user: str, schema: type[T], effort: str) -> T:
        extra: dict = {}
        if self.fallbacks:
            # Refusals on Opus-tier models re-run on a fallback model inside the same call.
            extra = {"extra_headers": {"anthropic-beta": FALLBACK_BETA}, "extra_body": {"fallbacks": "default"}}
        resp = self.client.messages.parse(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            output_config={"effort": effort},
            **extra,
        )
        with self._lock:
            u = self._usage[purpose]
            u["calls"] += 1
            u["input_tokens"] += resp.usage.input_tokens
            u["output_tokens"] += resp.usage.output_tokens
        if resp.stop_reason == "refusal":
            raise LLMError(f"{purpose}: refused ({resp.stop_details})")
        if resp.stop_reason == "max_tokens":
            raise LLMError(f"{purpose}: hit max_tokens={self.max_tokens}")
        if resp.parsed_output is None:
            raise LLMError(f"{purpose}: no parsed output (stop_reason={resp.stop_reason})")
        return resp.parsed_output

    def usage_report(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {k: dict(v) for k, v in self._usage.items()}


def strict_schema(schema: dict) -> dict:
    """Adapt a Pydantic JSON schema to OpenAI-style strict structured output:
    every object closed and every property required (fields with defaults
    included), no `default` keywords."""
    schema = copy.deepcopy(schema)

    def fix(node):
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for v in node.values():
                fix(v)
        elif isinstance(node, list):
            for v in node:
                fix(v)

    fix(schema)
    return schema


OPENROUTER_EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}
RETRY_STATUS = {408, 429, 500, 502, 503, 504}


class OpenRouterLLM:
    """Calls one model through an OpenAI-style chat completions API with a
    strict JSON schema: OpenRouter (key from OPENROUTER_API_KEY), or with
    local=True any compatible server at KNOWHOW_MODEL_BASE_URL (optional key
    from KNOWHOW_MODEL_API_KEY). KNOWHOW_MODEL_EXTRA_BODY holds JSON merged
    into every local request, e.g. '{"chat_template_kwargs": {"enable_thinking": false}}'
    for Qwen models served by vLLM."""

    URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, model: str, max_tokens: int = 16000, attempts: int = 3, local: bool = False):
        self.local = local
        self.extra_body: dict = {}
        if local:
            base = os.environ.get(BASE_URL_ENV)
            if not base:
                raise LLMError(f"{BASE_URL_ENV} is not set (or pass --model-base-url)")
            self.url = base.rstrip("/") + "/chat/completions"
            key = os.environ.get(LOCAL_KEY_ENV, "none")
            if os.environ.get(EXTRA_BODY_ENV):
                self.extra_body = json.loads(os.environ[EXTRA_BODY_ENV])
        else:
            self.url = self.URL
            key = os.environ.get("OPENROUTER_API_KEY")
            if not key:
                raise LLMError("OPENROUTER_API_KEY is not set")
        self.model = model
        self.max_tokens = max_tokens
        self.attempts = attempts
        self.http = httpx.Client(timeout=httpx.Timeout(300.0, connect=20.0), headers={"Authorization": f"Bearer {key}"})
        self._usage: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self._lock = threading.Lock()

    def _post(self, body: dict) -> dict:
        for attempt in range(1, self.attempts + 1):
            try:
                r = self.http.post(self.url, json=body)
            except httpx.TimeoutException:
                if attempt == self.attempts:
                    raise
            else:
                if r.status_code not in RETRY_STATUS or attempt == self.attempts:
                    r.raise_for_status()
                    return r.json()
            time.sleep(2**attempt)
        raise AssertionError("unreachable")

    def parse(self, *, purpose: str, system: str, user: str, schema: type[T], effort: str) -> T:
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "strict": True, "schema": strict_schema(schema.model_json_schema())},
            },
            "reasoning": {"effort": OPENROUTER_EFFORT[effort]},
            "provider": {"require_parameters": True},
        }
        if self.local:
            # OpenRouter's routing and reasoning fields mean nothing to a local server.
            del body["reasoning"], body["provider"]
            body.update(self.extra_body)
        # Providers occasionally return a cut-off JSON body with a normal finish
        # reason; that is retried like a transport error.
        for attempt in range(1, self.attempts + 1):
            started = time.monotonic()
            data = self._post(body)
            elapsed = time.monotonic() - started
            if "error" in data:
                raise LLMError(f"{purpose}: {data['error']}")
            usage = data.get("usage") or {}
            with self._lock:
                u = self._usage[purpose]
                u["calls"] += 1
                u["input_tokens"] += usage.get("prompt_tokens", 0)
                u["output_tokens"] += usage.get("completion_tokens", 0)
                u["cost_usd"] += usage.get("cost", 0.0)
                u["seconds"] += elapsed
                u["max_seconds"] = max(u["max_seconds"], elapsed)
            choice = data["choices"][0]
            msg = choice["message"]
            if msg.get("refusal"):
                raise LLMError(f"{purpose}: refused ({msg['refusal']})")
            if choice.get("finish_reason") == "length":
                raise LLMError(f"{purpose}: hit max_tokens={self.max_tokens}")
            try:
                return schema.model_validate_json(msg.get("content") or "")
            except ValidationError as e:
                with self._lock:
                    self._usage[purpose]["malformed"] += 1
                if attempt == self.attempts:
                    raise LLMError(
                        f"{purpose}: output did not match {schema.__name__} after {attempt} attempts "
                        f"(provider={data.get('provider')}, finish_reason={choice.get('finish_reason')}): {e}"
                    ) from e
        raise AssertionError("unreachable")

    def usage_report(self) -> dict[str, dict[str, float]]:
        with self._lock:
            return {k: {kk: round(vv, 4) for kk, vv in v.items()} for k, v in self._usage.items()}


class ScriptedLLM:
    """Returns whatever `responder(purpose, system, user, schema)` builds.
    Used by tests and `--dry-run` simulations; makes no network calls."""

    def __init__(self, responder: Callable[[str, str, str, type[BaseModel]], BaseModel]):
        self.responder = responder
        self.calls: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def parse(self, *, purpose: str, system: str, user: str, schema: type[T], effort: str) -> T:
        with self._lock:
            self.calls.append((purpose, user))
        out = self.responder(purpose, system, user, schema)
        assert isinstance(out, schema), (purpose, type(out))
        return out

    def usage_report(self) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = defaultdict(lambda: {"calls": 0})
        for purpose, _ in self.calls:
            counts[purpose]["calls"] += 1
        return dict(counts)

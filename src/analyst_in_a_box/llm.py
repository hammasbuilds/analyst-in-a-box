"""Optional language model. Nothing in the app requires one; ``get_client()`` returns None unless
configured through the environment:

    ANALYST_LLM=ollama     ANALYST_LLM_MODEL=qwen2.5-coder:7b   (CPU Ollama, localhost:11434)
    ANALYST_LLM=anthropic  ANTHROPIC_API_KEY=...                ANALYST_LLM_MODEL=claude-haiku-4-5
    ANALYST_LLM=openai     OPENAI_API_KEY=...                   ANALYST_OPENAI_BASE_URL=...

Standard library only. A model's output is untrusted text: generated SQL goes through the same
validator and read-only connection as everything else.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Protocol


class LLMError(RuntimeError):
    pass


class Client(Protocol):
    name: str
    model: str

    def complete(self, prompt: str, system: str = "", max_tokens: int = 600) -> str: ...


def _post(url: str, body: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise LLMError(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise LLMError(f"could not reach the model: {exc}") from exc


class Ollama:
    name = "ollama"

    def __init__(self, model: str, host: str, timeout: float = 240.0):
        self.model, self.host, self.timeout = model, host.rstrip("/"), timeout

    def complete(self, prompt: str, system: str = "", max_tokens: int = 600) -> str:
        body = {
            "model": self.model, "prompt": prompt, "system": system, "stream": False,
            "options": {"temperature": 0.0, "num_predict": max_tokens},
        }
        return _post(self.host + "/api/generate", body, {}, self.timeout).get("response", "")


class Anthropic:
    name = "anthropic"

    def __init__(self, model: str, key: str, timeout: float = 90.0):
        self.model, self.key, self.timeout = model, key, timeout

    def complete(self, prompt: str, system: str = "", max_tokens: int = 600) -> str:
        body = {
            "model": self.model, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": prompt}], "temperature": 0.0,
        }
        h = {"x-api-key": self.key, "anthropic-version": "2023-06-01"}
        data = _post("https://api.anthropic.com/v1/messages", body, h, self.timeout)
        return "".join(b.get("text", "") for b in data.get("content", []))


class OpenAICompatible:
    name = "openai"

    def __init__(self, model: str, key: str, base: str, timeout: float = 90.0):
        self.model, self.key, self.base, self.timeout = model, key, base.rstrip("/"), timeout

    def complete(self, prompt: str, system: str = "", max_tokens: int = 600) -> str:
        msgs = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        body = {"model": self.model, "messages": msgs, "max_tokens": max_tokens, "temperature": 0}
        data = _post(
            self.base + "/chat/completions", body, {"Authorization": f"Bearer {self.key}"},
            self.timeout,
        )
        return data["choices"][0]["message"]["content"]


def get_client() -> Client | None:
    kind = os.environ.get("ANALYST_LLM", "").strip().lower()
    model = os.environ.get("ANALYST_LLM_MODEL", "")
    if kind == "ollama":
        host = os.environ.get("ANALYST_OLLAMA_HOST", "http://localhost:11434")
        return Ollama(model or "qwen2.5-coder:7b", host)
    if kind == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        return Anthropic(model or "claude-haiku-4-5", os.environ["ANTHROPIC_API_KEY"])
    if kind == "openai" and os.environ.get("OPENAI_API_KEY"):
        base = os.environ.get("ANALYST_OPENAI_BASE_URL", "https://api.openai.com/v1")
        return OpenAICompatible(model or "gpt-4o-mini", os.environ["OPENAI_API_KEY"], base)
    return None


def status() -> dict:
    c = get_client()
    kind = os.environ.get("ANALYST_LLM", "").strip().lower()
    return {
        "configured": c is not None,
        "provider": c.name if c else None,
        "model": c.model if c else None,
        "note": None if c or not kind
        else f"ANALYST_LLM={kind} is set but its key is missing; running without a model",
    }

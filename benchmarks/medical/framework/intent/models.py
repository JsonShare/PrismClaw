"""Explicit optional model calls; credentials never enter artifacts or errors."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

PROVIDERS = {
    "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "nebula": ("https://llm.ai-nebula.com/v1", "NEUBULA_API_KEY"),
}


class ModelError(RuntimeError):
    """A model or transport failure, with no provider body or credential text."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ModelError("Authenticated model requests cannot redirect")


def key_for(provider: str, env_file: Path | None) -> str:
    _, name = PROVIDERS[provider]
    value = os.environ.get(name, "")
    if not value and env_file:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            text = line.strip().removeprefix("export ")
            if not text or text.startswith("#") or "=" not in text:
                continue
            key, candidate = text.split("=", 1)
            if key.strip() == name:
                candidate = candidate.strip()
                value = candidate[1:-1] if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in "\"'" else candidate.split(" #", 1)[0].strip()
    if not value:
        raise ModelError(f"Configure {name} through the environment or --env-file")
    return value


def request_body(provider: str, model: str, messages: list[dict], *, json_output: bool,
                 max_output_tokens: int = 2400, reasoning_effort: str | None = None) -> dict:
    budget_key = "max_completion_tokens" if provider == "openai" else "max_tokens"
    body = {"model": model, "messages": messages, budget_key: max_output_tokens}
    if reasoning_effort is None:
        body["temperature"] = 0
    elif provider == "openrouter":
        body["reasoning"] = {"effort": reasoning_effort}
    elif provider in {"openai", "nebula"}:
        body["reasoning_effort"] = reasoning_effort
    else:
        raise ValueError("Reasoning effort requires OpenAI, OpenRouter or Nebula")
    if json_output:
        body["response_format"] = {"type": "json_object"}
    if provider == "deepseek" or (provider == "nebula" and model.startswith("deepseek")):
        body["thinking"] = {"type": "enabled" if reasoning_effort not in {None, "none"} else "disabled"}
    return body


def call(provider: str, model: str, messages: list[dict], env_file: Path | None, *, json_output: bool,
         max_output_tokens: int = 2400, reasoning_effort: str | None = None,
         timeout_seconds: int = 90) -> dict:
    base, _ = PROVIDERS[provider]
    body = request_body(provider, model, messages, json_output=json_output,
                        max_output_tokens=max_output_tokens, reasoning_effort=reasoning_effort)
    request = urllib.request.Request(f"{base}/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {key_for(provider, env_file)}",
                                              "Content-Type": "application/json"})
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            payload = json.load(response)
        choice = payload["choices"][0]
        content = choice["message"]["content"]
        if choice.get("finish_reason") != "stop" or not isinstance(content, str) or not content.strip():
            raise ModelError("Model output is empty, incomplete or not a final answer")
        return {"content": content, "requested_model": model, "response_model": payload.get("model"),
                "response_id": payload.get("id"), "usage": payload.get("usage", {}), "provider": provider}
    except urllib.error.HTTPError as exc:
        raise ModelError(f"Model endpoint returned HTTP {exc.code}; body omitted") from None
    except (urllib.error.URLError, TimeoutError, KeyError, IndexError, TypeError, ValueError):
        raise ModelError("Model transport or response schema failed") from None

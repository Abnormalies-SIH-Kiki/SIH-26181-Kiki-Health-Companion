"""
Fast cloud completion for the `complex_query` action agent.

This is NOT the ordinary speaking path and NOT the slow reasoning router
(`generate_llm_resp`). It exists for one job: drive a multi-turn agent loop
fast enough that a complex spoken request finishes inside ~10 seconds. The
foreground care agent also reuses this lean client; on vision-enabled care
turns, its fresh JPEG and conversation prompt go to Cerebras Gemma in the same
Chat Completions request.

Design notes (all measured on the Pi via scripts/bench_action_agent.py):

* **Non-streaming.** An agent turn must produce a COMPLETE JSON object before
  any tool can run, so streaming buys nothing and only adds parsing surface.
* **Cerebras gets the FULL context, Groq gets a COMPACT one.** Cerebras
  `gemma-4-31b` has a 131K window and prompt caching (`cached_tokens` is
  reported per call), so resending the whole agent conversation is cheap and
  keeps tool-calling accuracy high. Groq is capped at **8000 tokens per minute
  per key** — measured, and a single 4-turn agent query is enough to 429 one
  key on its final turn — so the Groq path trims the conversation first.
* **Key rotation is the Groq recovery, never waiting.** Reuses
  `instant_vision._ordered_keys()` (round-robin + per-key 429 cooldowns +
  dead-key memory). NOTE this is the SAME pool `look_at_scene`/instant vision
  uses, so an agent run and a vision question do compete; that is exactly why
  Cerebras is the default provider.
* **Responses are truncated to the first balanced JSON object.** `gpt-oss-120b`
  reliably keeps going after its first object and invents its own tool results
  (measured). Cutting the response in code makes the loop immune to that
  regardless of how well the prompt is worded.

Measured 4-turn agent loop, Cerebras, warm (2026-07-26):
    gemma-4-31b    3.07s   clean single JSON object per turn
    gpt-oss-120b   2.88s   hallucinates follow-on turns, ~3x output tokens
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional

import requests

from tools_and_config.config_loader import get_full_config

# One keep-alive session, same lean approach as the speaking path.
SESSION = requests.Session()

# Both providers' edges reject the default python-requests/urllib User-Agent
# with a 403 before the request is ever authenticated.
_UA = "KikiFast/1.0"

CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

_DEFAULTS = {
    "provider": "cerebras",
    "fallback_provider": "groq",
    "cerebras": {
        "model": "gemma-4-31b",
        "max_tokens": 800,
        "temperature": 0.3,
        "request_timeout": 25,
        "max_prompt_chars": 0,          # 0 = uncapped (131K ctx + prompt cache)
    },
    "groq": {
        "model": "openai/gpt-oss-120b",
        "max_tokens": 700,
        "temperature": 0.3,
        "request_timeout": 25,
        "reasoning_effort": "low",
        # 8000 TPM per key (measured). ~9000 chars ≈ 2600 tokens leaves room
        # for the completion and the next turn inside one key's minute budget.
        "max_prompt_chars": 9000,
    },
}


def _cfg() -> dict:
    """Merge the `action_agent` config block over the measured defaults."""
    cfg = dict(_DEFAULTS)
    user = get_full_config().get("action_agent", {}) or {}
    for key, value in user.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            merged = dict(cfg[key])
            merged.update(value)
            cfg[key] = merged
        else:
            cfg[key] = value
    return cfg


class FastCloudUnavailable(Exception):
    """Every configured provider failed for this call."""


# --- response cleanup ------------------------------------------------------

def first_json_object(text: str) -> str:
    """Truncate a response to its FIRST balanced top-level JSON object.

    `gpt-oss-120b` does not stop after one object: it emits the tool call, then
    fabricates the tool's result, then the next call, then a `completed` status
    — all in one message. Taking only the first object means the loop acts on
    the real request and discards the fabrication. String-state aware so braces
    inside string literals don't break the depth count.

    Returns the original text unchanged when no balanced object is found (the
    agent loop's own parser then reports a JSON error and asks for a re-emit).
    """
    if not text:
        return text
    # A fenced block is the model's own explicit delimiter — respect it first
    # (gemma-4-31b wraps every reply in ```json ... ```).
    body = text
    if "```" in body:
        parts = body.split("```")
        if len(parts) >= 3:
            candidate = parts[1]
            if candidate.startswith("json"):
                candidate = candidate[4:]
            body = candidate

    start = body.find("{")
    if start == -1:
        return text
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(body)):
        ch = body[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return body[start:i + 1]
    return text


def _trim_prompt(prompt: str, max_chars: int) -> str:
    """Compact an oversized prompt for a TPM-limited provider.

    The HEAD of an agent prompt carries the task, the rules and the tool
    catalog — losing it is fatal — and the TAIL carries the newest tool
    results. So the middle is what gives.
    """
    if not max_chars or len(prompt) <= max_chars:
        return prompt
    head = int(max_chars * 0.55)
    tail = max_chars - head - 80
    return (prompt[:head]
            + f"\n…[{len(prompt) - max_chars} chars of older steps compressed]…\n"
            + prompt[-tail:])


# --- providers -------------------------------------------------------------

# Optional params that some models accept and others reject outright.
_OPTIONAL_PARAMS = ("reasoning_effort",)


def _post(url: str, key: str, body: dict, timeout: float) -> dict:
    """POST a completion, retrying once without optional params on a 400.

    Models disagree about `reasoning_effort`: gpt-oss takes low/medium/high,
    while qwen3.6-27b rejects anything but none/default with a hard 400 (the
    same trap instant_vision._create_stream already works around). Retrying
    without it keeps one config usable across models instead of silently
    turning every call into an "empty response".

    A 400 is the ONLY status worth retrying — 401/429 must propagate so the
    caller rotates keys rather than burning a second call on a dead one.
    """
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": _UA,
    }
    resp = SESSION.post(url, headers=headers, json=body, timeout=timeout)
    if resp.status_code == 400 and any(p in body for p in _OPTIONAL_PARAMS):
        detail = resp.text[:160]
        lean = {k: v for k, v in body.items() if k not in _OPTIONAL_PARAMS}
        print(f"[FastCloud] optional params rejected ({detail}); retrying lean")
        resp = SESSION.post(url, headers=headers, json=lean, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def _extract(data: dict, tag: str) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("no choices in response")
    message = choices[0].get("message") or {}
    content = (message.get("content") or "").strip()
    usage = data.get("usage") or {}
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    image_tokens = usage.get("image_tokens", 0)
    print(f"[FastCloud/{tag}] {usage.get('prompt_tokens', 0)} prompt "
          f"({cached} cached) / {usage.get('completion_tokens', 0)} completion"
          + (f" / {image_tokens} image" if image_tokens else ""))
    if not content:
        # Some models put everything in the separate reasoning channel when the
        # token budget runs out mid-thought. Salvage it rather than returning
        # an empty string, which the agent loop treats as a hard failure.
        content = (message.get("reasoning") or "").strip()
    return content


def _cerebras_user_content(prompt: str, image_b64=None,
                           image_mime: str = "image/jpeg"):
    """OpenAI-compatible user content for Cerebras Chat Completions.

    Gemma 4 image input is accepted only as a base64 data URI in a multimodal
    user message.  Keeping this builder in the lean requests client lets the
    care path use the same fast provider and the same model for vision and
    conversation—there is no preliminary vision-model call.

    Accepts one image or a SEQUENCE of them, in order. A guided exercise sends
    several frames taken across a single hold: one photograph cannot show
    whether a position was actually held for ten seconds, and judging form from
    the frame taken *after* the hold ended is how Kiki ended up praising a
    posture the person had already relaxed out of.
    """
    if not image_b64:
        return prompt
    images = [image_b64] if isinstance(image_b64, str) else list(image_b64)
    images = [img for img in images if img]
    if not images:
        return prompt
    mime = str(image_mime or "image/jpeg").lower()
    if mime not in {"image/jpeg", "image/png"}:
        raise ValueError(f"unsupported Cerebras image MIME type: {mime}")
    content = [{"type": "text", "text": prompt}]
    for img in images:
        content.append({"type": "image_url", "image_url": {
            "url": f"data:{mime};base64,{img}"}})
    return content


def _call_cerebras(prompt: str, cfg: dict,
                    image_b64: Optional[str] = None,
                    image_mime: str = "image/jpeg") -> str:
    key = os.getenv("CEREBRAS_API_KEY")
    if not key:
        raise RuntimeError("CEREBRAS_API_KEY is not set")
    sub = cfg["cerebras"]
    prompt = _trim_prompt(prompt, int(sub.get("max_prompt_chars", 0)))
    body = {
        "model": sub["model"],
        "messages": [{"role": "user", "content": _cerebras_user_content(
            prompt, image_b64=image_b64, image_mime=image_mime)}],
        "temperature": sub.get("temperature", 0.3),
        "max_tokens": sub.get("max_tokens", 800),
    }
    # gpt-oss accepts reasoning_effort; gemma rejects it. Only send when set.
    if sub.get("reasoning_effort"):
        body["reasoning_effort"] = sub["reasoning_effort"]
    data = _post(CEREBRAS_URL, key, body, float(sub.get("request_timeout", 25)))
    return _extract(data, "cerebras")


def _call_groq(prompt: str, cfg: dict,
               image_b64: Optional[str] = None,
               image_mime: str = "image/jpeg") -> str:
    if image_b64:
        raise RuntimeError(
            "the action-agent Groq fallback is text-only; refusing to drop "
            "a care frame or imply that it was inspected")
    from core.vision.instant_vision import _ordered_keys, _DEAD_KEYS, _KEY_COOLDOWN

    sub = cfg["groq"]
    body_base = {
        "model": sub["model"],
        "messages": [{"role": "user", "content": _trim_prompt(
            prompt, int(sub.get("max_prompt_chars", 9000)))}],
        "temperature": sub.get("temperature", 0.3),
        "max_tokens": sub.get("max_tokens", 700),
    }
    if sub.get("reasoning_effort"):
        body_base["reasoning_effort"] = sub["reasoning_effort"]

    keys = _ordered_keys()
    if not keys:
        raise RuntimeError("no Groq keys available")
    last_error = "no Groq key succeeded"
    for key in keys:
        try:
            data = _post(GROQ_URL, key, dict(body_base),
                         float(sub.get("request_timeout", 25)))
            return _extract(data, "groq")
        except RuntimeError as exc:
            message = str(exc)
            last_error = message
            if "HTTP 429" in message:
                # Rotate immediately — never sleep on retry-after. The budget is
                # a per-key token bucket that refills in ~24s; another key is
                # almost always ready right now.
                _KEY_COOLDOWN[key] = time.time() + 20.0
                print("[FastCloud/groq] 429 — rotating key")
                continue
            if "HTTP 401" in message or "HTTP 403" in message:
                _DEAD_KEYS.add(key)
                continue
            raise
    raise RuntimeError(last_error)


_PROVIDERS = {"cerebras": _call_cerebras, "groq": _call_groq}


def complete(prompt: str, provider: Optional[str] = None,
             stop_event: Optional[threading.Event] = None,
             image_b64: Optional[str] = None,
             image_mime: str = "image/jpeg") -> str:
    """Run one agent turn. Returns the model's first JSON object as text.

    Tries the configured provider, then the fallback provider. A supplied image
    is sent directly to Cerebras Gemma 4 through Chat Completions. It is never
    silently discarded for a text-only fallback. Raises
    FastCloudUnavailable only when every provider failed, which the agent loop
    surfaces as a normal failure (Kiki says it could not do it) rather than a
    crash.
    """
    cfg = _cfg()
    primary = (provider or cfg.get("provider") or "cerebras").lower()
    fallback = (cfg.get("fallback_provider") or "").lower()
    order = [p for p in (primary, fallback) if p in _PROVIDERS]
    # De-dupe while preserving order (provider == fallback is a valid config).
    order = list(dict.fromkeys(order))
    if not order:
        raise FastCloudUnavailable(f"unknown provider {primary!r}")

    errors = []
    for name in order:
        if stop_event is not None and stop_event.is_set():
            raise FastCloudUnavailable("stopped before request")
        started = time.perf_counter()
        try:
            text = _PROVIDERS[name](
                prompt, cfg, image_b64=image_b64, image_mime=image_mime)
            print(f"[FastCloud/{name}] turn in {time.perf_counter() - started:.2f}s")
            return first_json_object(text)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            print(f"[FastCloud] {name} failed ({exc})")
    raise FastCloudUnavailable("; ".join(errors))


def active_model() -> str:
    """Short 'provider/model' label for logs and the LCD."""
    cfg = _cfg()
    provider = (cfg.get("provider") or "cerebras").lower()
    return f"{provider}/{cfg.get(provider, {}).get('model', '?')}"

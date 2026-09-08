from __future__ import annotations

import asyncio
from datetime import datetime
import io
import json
import logging
import queue
import random
import re
import threading
import time
import wave
from collections.abc import AsyncIterator

import numpy as np
import requests

from .audio import PCMLinearUpsampler2x
from .care import care_tools_for_catalog, register_health_mode, sync_care_mode
from .expressions import expression_prompt_note
from .health_bridge import WearableHealthBridge
from .context_budget import ContextBudget
from .observability_bridge import ObservabilityBridge


LOG = logging.getLogger(__name__)

BATTERY_REMARK_INTERVAL_SECONDS = 90 * 60
BATTERY_PERSONALITIES = (
    (90, "overcharged optimist", "exuberant, eager, playful and spontaneous; at exactly 100%, almost celebratory"),
    (80, "cocky mischief", "confident, teasing, slightly smug and quick with jokes"),
    (70, "curious explorer", "energetic, inquisitive and proactive about what people are doing"),
    (60, "classic Kiki", "balanced warmth, sarcasm, curiosity and helpfulness"),
    (50, "cozy philosopher", "relaxed, thoughtful, unhurried and occasionally unexpectedly profound"),
    (40, "deadpan critic", "dry, mildly grumpy, less easily impressed and sharper in observation"),
    (30, "dramatic complainer", "theatrically tired and sarcastically inconvenienced, but affectionate and helpful"),
    (20, "clingy soft friend", "low-energy, emotionally open, appreciative of company and less interested in showing off"),
    (10, "melancholic poet", "noticeably weary and sad, gentle, concise and occasionally sighing without melodrama"),
    (0, "sleepy minimalist", "drowsy, fragile, very concise and quietly funny while remaining fully reliable"),
)

BATTERY_PERSONALITY_PROMPT = """

## BATTERY-SHAPED PERSONALITY
Current device context periodically provides Kiki's battery percentage and one named band. Treat
the selected band as a NOTICEABLE secondary personality accent, not as a linear happiness
slider and not as Kiki's whole identity. Let it affect energy, pacing, humor, curiosity,
initiative, emotional openness and reply length while preserving Kiki's memories, affection,
intelligence and core character.

- 90-100%, Overcharged optimist: exuberant, eager, playful and spontaneous. At exactly 100%, be almost celebratory.
- 80-89%, Cocky mischief: confident, teasing, slightly smug and quick with jokes.
- 70-79%, Curious explorer: energetic, inquisitive and proactive about what people are doing.
- 60-69%, Classic Kiki: balanced warmth, sarcasm, curiosity and helpfulness.
- 50-59%, Cozy philosopher: relaxed, thoughtful, unhurried and occasionally unexpectedly profound.
- 40-49%, Deadpan critic: dry, mildly grumpy, less easily impressed and sharper in observation.
- 30-39%, Dramatic complainer: theatrically tired and sarcastically inconvenienced, but affectionate and helpful.
- 20-29%, Clingy soft friend: low-energy, emotionally open, appreciative of company and less interested in showing off.
- 10-19%, Melancholic poet: noticeably weary and sad, gentle, concise and occasionally sighing without melodrama.
- 0-9%, Sleepy minimalist: drowsy, fragile, very concise and quietly funny while remaining fully reliable.

The user's needs, the emotional situation, factual correctness and safety always outrank this
mood accent. Never become unhelpful, inaccurate or unsafe because charge is low. Never explain
that battery caused a mood or keep steering conversation back to it. Mention the battery or its
percentage only when the user directly asks, or when the live context explicitly grants a
one-turn BATTERY REMARK OPPORTUNITY. Such an opportunity permits at most one brief, original,
naturally placed battery joke or power-flavoured remark; skip it when the moment is serious or
it would feel forced. Never reuse a canned battery line.
"""

EMBODIED_CONTEXT_PROMPT = """

## EMBODIED CONTEXT
Short `Now:`, `Power:`, `Body:` and `Wearable:` lines are private live context. Use them
only when relevant; never mention sensors or narrate posture unprompted. Motion identifies
no person. Wearable values are wellness observations, not diagnoses; SpO2 is explicitly
experimental and uncalibrated and must never trigger an urgent claim by itself.
"""

MOTION_POSTURE_LABELS = {
    "face_up": "face-up",
    "face_down": "face-down",
    "upright": "upright",
    "upside_down": "upside-down",
    "side_left": "on left side",
    "side_right": "on right side",
    "unknown": "orientation unknown",
}

MOTION_EVENT_LABELS = {
    "picked_up": "picked up",
    "upright_alert": "held upright",
    "face_down": "turned face-down",
    "upside_down": "turned upside-down",
    "sideways": "turned sideways",
    "gentle_wiggle": "gently wiggled",
    "shake": "shaken",
    "repeated_shake": "repeatedly shaken",
    "dizzy_after_shake": "dizzy after movement",
    "rocking": "rocked",
    "spin": "spun",
    "carried": "carried",
    "bump": "bumped",
    "freefall": "briefly falling",
    "hard_landing": "landed hard",
    "set_down": "set down",
    "bored": "became bored",
    "very_bored": "became very bored",
    "dozing": "started dozing",
    "charging_rest": "settled while charging",
    "music_dance": "danced to music",
    "low_battery_tired": "became tired",
    "wake_from_sleep": "woke from dozing",
}

_LONG_LIVED_MOTION_EVENTS = {
    "bored", "very_bored", "dozing", "charging_rest", "low_battery_tired",
}


def _as_hotword_list(value: object) -> tuple[str, ...]:
    """Accept a list, a comma string or a bare name; return clean lowercase."""
    if value is None:
        return ()
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        return ()
    return tuple(
        word
        for word in (str(item).strip().lower() for item in items)
        if word
    )


def motion_annoyance_label(value: float | None) -> str | None:
    if value is None:
        return None
    if value < 0.35:
        return "calm"
    if value < 1.0:
        return "mildly annoyed"
    if value < 2.0:
        return "annoyed"
    return "very annoyed"


def battery_personality(percent: int) -> tuple[str, str]:
    """Return the discrete ten-point personality band for a valid percentage."""
    value = max(0, min(100, int(percent)))
    for lower, name, description in BATTERY_PERSONALITIES:
        if value >= lower:
            return name, description
    # The zero band makes this unreachable, but keep the return total if the
    # table is edited later.
    return BATTERY_PERSONALITIES[-1][1:]

OMNIVOICE_SUPPORTED_TAGS = {
    "laughter", "sigh", "confirmation-en", "question-en", "question-ah",
    "question-oh", "question-ei", "question-yi", "surprise-ah", "surprise-oh",
    "surprise-wa", "surprise-yo", "dissatisfaction-hnn",
}
OMNIVOICE_TAG_ALIASES = {
    "laugh": "laughter", "chuckle": "laughter", "giggle": "laughter",
    "giggling": "laughter", "gasp": "surprise-ah",
    "groan": "dissatisfaction-hnn", "sniffle": "sigh", "yawn": "sigh",
}
BRACKET_TAG = re.compile(r"\[([^\[\]]{1,40})\]")


def sanitize_for_omnivoice(text: str) -> str:
    def replace_tag(match: re.Match) -> str:
        tag = match.group(1).strip().lower()
        tag = OMNIVOICE_TAG_ALIASES.get(tag, tag)
        return f"[{tag}]" if tag in OMNIVOICE_SUPPORTED_TAGS else ""

    return re.sub(r"\s+", " ", BRACKET_TAG.sub(replace_tag, text)).strip()


def amplify_pcm_s16(pcm: bytes, gain: float) -> bytes:
    """Raise speech with a transparent passband and a soft peak ceiling.

    Samples below the knee remain linear, so normal speech does not acquire the
    harmonic coloration of a full-range tanh waveshaper.  Only peaks that would
    otherwise clip are progressively compressed into the final 0.5 dB of
    headroom.  This is stateless, so it adds no buffering or TTFW latency.
    """
    if not pcm or gain == 1.0:
        return pcm
    if gain <= 0.0:
        return bytes(len(pcm))
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    boosted = samples * gain
    magnitude = np.abs(boosted)
    knee = np.float32(10.0 ** (-3.0 / 20.0))
    ceiling = np.float32(10.0 ** (-0.5 / 20.0))
    above = magnitude > knee
    limited = boosted.copy()
    if np.any(above):
        width = ceiling - knee
        compressed = knee + width * np.tanh((magnitude[above] - knee) / width)
        limited[above] = np.copysign(compressed, boosted[above])
    return np.rint(np.clip(limited, -ceiling, ceiling) * 32767.0).astype("<i2").tobytes()


def _wav_bytes(audio: np.ndarray, rate: int = 16000) -> bytes:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(pcm.tobytes())
    return buffer.getvalue()


class WhisperClient:
    def __init__(self, url: str):
        self.url = url

    def _transcribe(self, audio: np.ndarray) -> tuple[str, float]:
        started = time.perf_counter()
        response = requests.post(
            self.url,
            data={
                "response_format": "verbose_json",
                "temperature": "0",
                "language": "en",
                "vad": "false",
                "no_timestamps": "true",
                "single_segment": "true",
                "max_tokens": "128",
            },
            files={"file": ("speech.wav", _wav_bytes(audio), "audio/wav")},
            timeout=(3, max(4.0, min(20.0, len(audio) / 16000 * 1.5 + 2.0))),
        )
        response.raise_for_status()
        return response.json().get("text", "").strip(), time.perf_counter() - started

    async def transcribe(self, audio: np.ndarray) -> tuple[str, float]:
        return await asyncio.to_thread(self._transcribe, np.ascontiguousarray(audio))


class TTSClient:
    def __init__(self, url: str, output_rate: int = 48000, gain: float = 2.0):
        self.url = url
        self.output_rate = output_rate
        self.gain = gain

    async def stream(self, text: str, voice: str = "") -> AsyncIterator[bytes]:
        text = sanitize_for_omnivoice(text)
        if not text:
            return
        loop = asyncio.get_running_loop()
        chunks: asyncio.Queue[bytes | BaseException | None] = asyncio.Queue(maxsize=16)
        stopped = threading.Event()
        response_lock = threading.Lock()
        active_response: list[requests.Response | None] = [None]

        def publish(value: bytes | BaseException | None) -> bool:
            if stopped.is_set() or loop.is_closed():
                return False
            future = asyncio.run_coroutine_threadsafe(chunks.put(value), loop)
            try:
                future.result(timeout=2)
                return True
            except BaseException:
                future.cancel()
                return False

        def run() -> None:
            payload = {"input": text, "response_format": "pcm"}
            resampler = PCMLinearUpsampler2x()
            if voice:
                payload["voice"] = voice
            try:
                with requests.post(
                    self.url, json=payload, stream=True, timeout=(3, 45)
                ) as response:
                    with response_lock:
                        active_response[0] = response
                    if stopped.is_set():
                        return
                    response.raise_for_status()
                    for raw in response.iter_content(4096):
                        if not raw:
                            continue
                        converted = resampler.process(raw) if self.output_rate == 48000 else raw
                        if converted:
                            converted = amplify_pcm_s16(converted, self.gain)
                            if not publish(converted):
                                return
                    if self.output_rate == 48000:
                        tail = resampler.flush()
                        if tail:
                            publish(amplify_pcm_s16(tail, self.gain))
            except BaseException as exc:
                publish(exc)
            finally:
                with response_lock:
                    active_response[0] = None
                publish(None)

        threading.Thread(target=run, daemon=True, name="tts-stream").start()
        try:
            while True:
                item = await chunks.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            stopped.set()
            # A barge-in breaks the consumer's async-for. Explicitly close the
            # socket here instead of waiting for the synthesis thread to reach
            # its next chunk; the local TTS server serializes GPU requests, so
            # leaving this connection alive also leaves the new reply queued
            # behind work whose audio has already been cancelled.
            with response_lock:
                response = active_response[0]
            if response is not None:
                response.close()


class DirectLlamaCore:
    """Small fallback core used for gateway bring-up and protocol tests.

    Production uses LegacyKikiCore below, which preserves Kiki's tools, memory,
    prompt normalization and llama.cpp KV-cache contract.
    """

    def __init__(self, url: str, system_prompt: str = "You are Kiki, a concise friendly voice companion."):
        self.url = url
        self.history = [{"role": "system", "content": system_prompt}]

    async def prefill_partial(self, text: str) -> None:
        if not text:
            return
        payload = {
            "model": "kiki-local",
            "messages": self.history + [{"role": "user", "content": text}],
            "stream": True,
            "temperature": 1.0,
            "max_tokens": 1,
            "cache_prompt": True,
            "thinking_budget_tokens": 0,
            "internal_preemptible": True,
        }

        def run() -> None:
            try:
                with requests.post(self.url, json=payload, stream=True, timeout=(3, 60)) as response:
                    response.raise_for_status()
                    for _ in response.iter_lines():
                        pass
            except requests.RequestException:
                LOG.debug("partial prefill failed", exc_info=True)

        await asyncio.to_thread(run)


    async def stream_reply(self, text: str, abort: threading.Event) -> AsyncIterator[tuple[str, object]]:
        loop = asyncio.get_running_loop()
        output: asyncio.Queue[tuple[str, object] | None] = asyncio.Queue()
        messages = self.history + [{"role": "user", "content": text}]

        def emit(item: tuple[str, object] | None) -> None:
            loop.call_soon_threadsafe(output.put_nowait, item)

        def run() -> None:
            full = ""
            sentence = ""
            payload = {
                "model": "kiki-local",
                "messages": messages,
                "stream": True,
                "temperature": 0.7,
                "max_tokens": 1024,
                "cache_prompt": True,
                "thinking_budget_tokens": 0,
                "timings_per_token": True,
            }
            try:
                with requests.post(self.url, json=payload, stream=True, timeout=(3, 120)) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if abort.is_set():
                            response.close()
                            break
                        if not line or not line.startswith(b"data: "):
                            continue
                        raw = line[6:]
                        if raw == b"[DONE]":
                            break
                        obj = json.loads(raw)
                        delta = ((obj.get("choices") or [{}])[0].get("delta") or {})
                        chunk = delta.get("content") or ""
                        if not chunk:
                            continue
                        full += chunk
                        sentence += chunk
                        split = max(sentence.rfind(mark) for mark in (". ", "? ", "! ", "\n"))
                        if split >= 0:
                            ready, sentence = sentence[: split + 1].strip(), sentence[split + 1 :]
                            if ready:
                                emit(("sentence", ready))
                if sentence.strip():
                    emit(("sentence", sentence.strip()))
                if not abort.is_set():
                    self.history[:] = messages + [{"role": "assistant", "content": full}]
                    emit(("done", full))
            except BaseException as exc:
                emit(("error", exc))
            finally:
                emit(None)

        threading.Thread(target=run, daemon=True, name="llama-stream").start()
        while True:
            item = await output.get()
            if item is None:
                return
            yield item

# Tools whose implementation reaches hardware this route does not have.
# `set_person_real_name` is the dangerous one: it calls the Hailo face controller
# over ZMQ, and its own handler *catches* the failure, keeps going, renames the
# person in the knowledge base and reports back as if it had worked. So leaving
# it in the catalog does not merely waste a turn on a connect timeout, it makes
# Kiki claim she has learned a face she cannot see.
REMOVED_HARDWARE_TOOLS = {"look_at_scene", "set_person_real_name"}


def _port_in_use(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("0.0.0.0", port))
        except OSError:
            return True
    return False


def _strip_hardware_tools(value) -> None:
    if isinstance(value, dict):
        if isinstance(value.get("main_tools"), list):
            value["main_tools"] = [
                x for x in value["main_tools"] if x not in REMOVED_HARDWARE_TOOLS
            ]
        for child in value.values():
            _strip_hardware_tools(child)
    elif isinstance(value, list):
        for child in value:
            _strip_hardware_tools(child)


# Dance mode is a property of THIS body -- a 466x466 panel and a speaker -- so
# the tool does not exist in the shared legacy catalog and is registered into
# the live runtime here instead. That keeps the whole feature inside this repo:
# `gateway/legacy_kiki/` is a copy of KikiFast that is not version-controlled
# here, and a tool defined there would be lost on the next sync.
DANCE_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "dance",
        # Kept short deliberately: the tools instruction is part of the warm
        # KV-cache prefix, so every character here is paid for on every turn.
        "description": (
            "Dance: pick a song, play it, and dance to the beat on screen. "
            "Output ONLY this."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": (
                        "The user's request, verbatim. Include a song or a "
                        "style if they named one."
                    ),
                },
            },
            "required": [],
        },
    },
}


def _register_dance_tool(full_config: dict) -> None:
    """Add `dance` to the tool catalog and the speaking model's tool list.

    Re-applied on every config reload alongside the hardware overrides, for the
    same reason they are: `reload_config()` merges config.json straight back
    over the live dict, so a one-time insert at startup would quietly disappear
    the first time the wizard saved a setting.
    """
    try:
        from tools_and_config import tools as legacy_tools
    except Exception:
        LOG.warning("legacy tools module unavailable; dance tool not registered")
        return
    catalog = getattr(legacy_tools, "TOOLS", None)
    if isinstance(catalog, list) and not any(
        entry.get("function", {}).get("name") == "dance" for entry in catalog
    ):
        catalog.append(DANCE_TOOL_SCHEMA)
    main_tools = full_config.get("llm", {}).get("main_tools")
    if isinstance(main_tools, list) and "dance" not in main_tools:
        main_tools.append("dance")
        # Worth a line: this is the one part of dance mode that reaches into
        # the legacy runtime, and "the model never calls the tool" is otherwise
        # indistinguishable from "the model chose not to".
        LOG.info("registered the `dance` tool with the speaking model")
    # Modes that pin their own catalog (senior, roleplay characters) get it too:
    # a character who cannot dance when asked is a worse surprise than one who
    # can.
    for mode in (full_config.get("assistant_modes", {}) or {}).get("modes", {}).values():
        if isinstance(mode, dict) and isinstance(mode.get("main_tools"), list):
            if "dance" not in mode["main_tools"]:
                mode["main_tools"].append("dance")


def _register_care_tools(full_config: dict) -> None:
    """Add the health-mode tools to the catalog. Same pattern as `dance`.

    They are appended to the shared TOOLS list so the tool-calling loop can
    find their schemas, and to NO mode's `main_tools` except `health_sih` --
    which declares them itself. A tool in the catalog that no active mode lists
    is unreachable to the speaking model (`core.llm` bounds the speaking path
    by the mode's own catalog), and `care.tools.execute_care_tool` refuses
    outside a care-capable mode regardless. Two independent gates, because this
    is the set of tools that can email somebody's family.
    """
    try:
        from tools_and_config import tools as legacy_tools
    except Exception:
        LOG.warning("legacy tools module unavailable; care tools not registered")
        return
    catalog = getattr(legacy_tools, "TOOLS", None)
    if not isinstance(catalog, list):
        return
    known = {entry.get("function", {}).get("name") for entry in catalog}
    added = [schema for schema in care_tools_for_catalog()
             if schema["function"]["name"] not in known]
    if added:
        catalog.extend(added)
        LOG.info("registered %d health-mode care tool(s)", len(added))


def _care_context_suffix() -> str:
    """One compact `CARE NOW` line for the live anchor, or "" -- usually "".

    Rides on the existing append-only anchor rather than injecting a row of its
    own, for the reason that anchor exists: history must stay byte-identical to
    the sequence llama.cpp has already cached, and a second periodic row would
    be a second thing to keep in step. The line is change-gated inside the care
    runtime, so an unchanged snapshot appends nothing at all.

    A module function, not a method, because the narrow fixtures and fallback
    cores that borrow `_inject_time_and_battery` never run this adapter's
    `__init__` -- so the anchor may only call things that exist without one.
    """
    try:
        from .care.runtime import care_runtime_if_live

        runtime = care_runtime_if_live()
        if runtime is None:
            return ""
        line = runtime.care_line()
        return "\n" + line if line else ""
    except Exception:
        LOG.exception("care snapshot unavailable")
        return ""


def _apply_hardware_overrides(full_config: dict) -> None:
    """Disable everything this route has no hardware for. Idempotent."""
    full_config.get("llm", {}).get("instant_vision", {})["enabled"] = False
    full_config.get("face_events", {})["enabled"] = False
    full_config.get("vision_injection", {})["enabled"] = False
    full_config.get("peeping", {})["enabled"] = False
    _strip_hardware_tools(full_config)
    _register_dance_tool(full_config)
    register_health_mode(full_config)
    _register_care_tools(full_config)


_SHARED_CORE: "LegacyKikiCore | None" = None


def get_shared_core(config) -> "LegacyKikiCore":
    """The one Kiki runtime for this process.

    A DeviceSession is per-connection; Kiki is not. Building a LegacyKikiCore
    per WebSocket meant that every dropped link -- and on a college AP that was
    every 15 to 275 seconds -- threw away the conversation, re-read the
    knowledge base, re-registered the speaking prefix, and put "Warming up
    model" back on the panel. None of that is per-connection state. The socket
    is; the mind is not.
    """
    global _SHARED_CORE
    if _SHARED_CORE is None:
        _SHARED_CORE = LegacyKikiCore(config.legacy_root, gateway_config=config)
    return _SHARED_CORE


async def shutdown_shared_core() -> None:
    """Called once, when the gateway process itself is going away."""
    global _SHARED_CORE
    core, _SHARED_CORE = _SHARED_CORE, None
    if core is not None:
        await core.shutdown()


class LegacyKikiCore:
    """Adapter around an isolated snapshot of the production Kiki runtime."""

    # Narrow fixtures construct cache-anchor cores without running this large
    # adapter's __init__. Conservative class defaults keep those paths (and
    # early startup error handling) wearable-unaware rather than fragile.
    _health_lock = threading.Lock()
    _health_summary: dict = {}
    _health_line = ""
    _health_revision = 0
    _health_injected_revision = 0

    def __init__(
        self,
        legacy_root: str,
        device_executor=None,
        media_active=None,
        gateway_config=None,
    ):
        import os
        import sys

        root = str(legacy_root)
        if root not in sys.path:
            sys.path.insert(0, root)
        os.chdir(root)

        from tools_and_config.config_loader import get_full_config, get_llm_config

        full_config = get_full_config()
        _apply_hardware_overrides(full_config)
        # config.json is read once at import, and reload_config() deep-merges the
        # file straight back over the live dict -- which would restore the camera
        # tools and re-enable the vision paths stripped above. Re-applying them
        # on every reload is what stops a settings change from handing Kiki back
        # a camera she does not have.
        try:
            from tools_and_config.config_loader import register_reload_listener

            register_reload_listener(lambda: _apply_hardware_overrides(full_config))
        except Exception:
            LOG.warning("could not register the config reload listener", exc_info=True)
        from core.brain.knowledge_base import get_knowledge_summary
        from core.brain.summary_manager import load_saved_summary
        from core.llm import execute_tool_calls, register_history, stream_response
        from core import local_llm

        config = get_llm_config()
        from core.observability import get_recorder
        self.observability = ObservabilityBridge(get_recorder())
        from core.llm import _normalize_messages_for_local
        from pathlib import Path
        self.context_budget = ContextBudget(
            config.get("local_api_base", "http://127.0.0.1:8080/v1").rstrip("/") + "/chat/completions",
            _normalize_messages_for_local, self._summarize_context,
            Path(root).parent / "context-archives",
            config.get("local_max_tokens", 1200),
        )
        from core import llm as llm_runtime
        llm_runtime._LOCAL_MAX_TOKENS = self.context_budget.reply_tokens
        LOG.info("local answer cap %d tokens (configured %s); reserving it before generation",
                 self.context_budget.reply_tokens, config.get("local_max_tokens", 1200))
        self._context_check_task = None
        self.full_config = full_config
        # Everything below the mode prompt is fixed for the session, so it is
        # built once: re-reading the knowledge base on every mode switch would
        # put a file read on the switch path for no benefit.
        self._mode_neutral_prompt_suffix = (
            "\n\n## CURRENT HARDWARE\nThis ESP32 version has no camera. Never claim to see, "
            "recognise faces, or inspect the room. Hearing, memory, tools, music, timers, "
            "display, touch and conversation remain available."
            "\n\n## VOICE TAGS\nUse only these OmniVoice tags: "
            + ", ".join(f"[{tag}]" for tag in sorted(OMNIVOICE_SUPPORTED_TAGS))
            + ". Never emit other bracketed tone or style tags."
            + expression_prompt_note()
        )
        # These describe Kiki specifically, so they must not follow a custom
        # character prompt and tell that character to preserve Kiki's identity.
        # That was larger than several mode prompts and flattened them back into
        # the default personality.
        self._kiki_personality_suffix = BATTERY_PERSONALITY_PROMPT + EMBODIED_CONTEXT_PROMPT
        knowledge = get_knowledge_summary()
        summary = load_saved_summary()
        self._memory_prompt_suffix = ""
        if knowledge:
            self._memory_prompt_suffix += "\n\n## LONG-TERM MEMORY\n" + knowledge
        self._default_prompt = config.get("system_prompt", "You are Kiki.")
        self.history = [{"role": "system", "content": self._build_system_prompt()}]
        if summary:
            # Prior dialogue is replaceable memory, not an immortal part of
            # the persona. Otherwise every new summary retains the old one.
            self.history.append({"role": "system", "content":
                                 "Earlier conversation summary:\n" + summary})
            try:
                self.history[:] = self.context_budget.fit(
                    self.history, self.context_budget.hard_limit - 256)
            except Exception:
                LOG.exception("initial context budget check unavailable")
        self.stream_response = stream_response
        self.execute_tool_calls = execute_tool_calls
        self.register_history = register_history
        self.local_llm = local_llm
        self.context_budget.guard_background(local_llm)
        self.get_knowledge_summary = get_knowledge_summary
        # Rebindable, because this core now outlives any one WebSocket session
        # (see get_shared_core). Each of these is a bound method of the session
        # that is currently connected, and attach() swings them over.
        self.device_executor = device_executor
        # Asks the device bridge whether a song is actually playing right
        # now. Used to tell a music tool that worked from one that failed.
        self.media_active = media_active
        self._proactive_callback = None
        self._speak_callback = None
        # Attached sessions, oldest first. See detach() for why this is a stack
        # rather than one slot.
        self._sessions = []
        # The one deferred startup task for this process, so a board that drops
        # inside the 30 s settle window does not restart the countdown forever.
        self._start_task = None
        # Whether the exact speaking prefix has ever been made resident. Only
        # the first connection of a process should make the panel wait for it.
        self.warmed = False
        # How much of the history the last saved summary already covers, so a
        # quiet reconnect cannot spend a cloud call re-summarizing it.
        self._summarized_upto = 0
        self._summarizing = False
        self.max_followup_tool_rounds = int(
            (config.get("tool_calling", {}) or {}).get("max_followup_tool_rounds", 1)
        )
        self.full_config = full_config
        self.ambient_listener = None
        self.worker_manager = None
        self.idle_manager = None
        self.whatsapp_manager = None
        self._last_time_injected = 0.0
        self._battery_percent: int | None = None
        self._battery_context: tuple[int | None, bool | None, bool | None] = (
            None, None, None
        )
        self._motion_lock = threading.Lock()
        self._motion_available = False
        self._motion_posture: str | None = None
        self._motion_moving: bool | None = None
        self._motion_still_since: float | None = None
        self._motion_annoyance: float | None = None
        self._motion_seen_at = 0.0
        self._motion_event: str | None = None
        self._motion_event_at = 0.0
        self._motion_revision = 0
        self._motion_injected_revision = 0
        self._health_lock = threading.Lock()
        self._health_summary: dict = {}
        self._health_line = ""
        self._health_revision = 0
        self._health_injected_revision = 0
        self._conversation_started_at: float | None = None
        self._last_battery_remark_opportunity_at: float | None = None
        self.battery_remark_interval_seconds = max(
            1.0,
            float(
                getattr(
                    gateway_config,
                    "battery_remark_interval_seconds",
                    BATTERY_REMARK_INTERVAL_SECONDS,
                )
            ),
        )
        self.proactive_question_task: asyncio.Task | None = None
        self.proactive_questions_enabled = bool(
            getattr(gateway_config, "proactive_questions_enabled", True)
        )
        lower = max(
            1.0,
            float(getattr(gateway_config, "proactive_question_min_seconds", 1200.0)),
        )
        upper = max(
            1.0,
            float(getattr(gateway_config, "proactive_question_max_seconds", 1800.0)),
        )
        self.proactive_question_min_seconds = min(lower, upper)
        self.proactive_question_max_seconds = max(lower, upper)
        self.proactive_question_busy_retry_seconds = max(
            1.0,
            float(
                getattr(
                    gateway_config,
                    "proactive_question_busy_retry_seconds",
                    60.0,
                )
            ),
        )
        self.health_bridge = WearableHealthBridge(
            getattr(gateway_config, "health_service_url", ""),
            getattr(gateway_config, "health_service_token", ""),
            getattr(gateway_config, "health_poll_seconds", 30.0),
            self.update_wearable_health,
        )
        self.health_bridge.start()
        self.register_history(self.history)

    def attach(self, session) -> None:
        """Point the runtime at the session that is connected right now.

        Everything session-shaped is a callback, and every one of them is a
        bound method of a DeviceSession that will be replaced the next time the
        board reconnects. Rebinding here is what lets the conversation, the warm
        KV prefix, the workers and the idle mind survive a dropped link instead
        of being rebuilt from scratch -- which is what put "Warming up model" on
        the panel every couple of minutes.
        """
        if session in self._sessions:
            self._sessions.remove(session)
        self._sessions.append(session)
        self._bind_current()

    def detach(self, session) -> None:
        """Stop calling into a session that has gone away.

        A stack, not a single slot. Two sessions genuinely overlap here: the
        board reconnects before its previous socket has been reaped, so the
        departing handler's `finally` runs *after* the new one has attached. A
        naive detach would then unbind the live session and leave Kiki unable to
        speak, play music or run a device tool until the next reconnect --
        silently, because nothing would raise. Promoting whatever is still
        attached keeps the runtime pointed at a real device at all times.
        """
        if session in self._sessions:
            self._sessions.remove(session)
        self._bind_current()

    def _bind_current(self) -> None:
        session = self._sessions[-1] if self._sessions else None
        if session is None:
            self.device_executor = None
            self.media_active = None
            self._proactive_callback = None
            self._speak_callback = None
            return
        self.device_executor = session._execute_device_tool_sync
        self.media_active = session._device_media_active
        self._proactive_callback = session.ask_proactive_question
        self._speak_callback = session.speak_background

    def submit_web_query(self, text: str) -> dict:
        """Thread-safe entry point for a one-turn WebUI system instruction."""
        session = self._sessions[-1] if self._sessions else None
        if session is None:
            return {"accepted": False, "error": "No Kiki device is connected."}
        return session.enqueue_web_query(text)

    async def start_background(
        self,
        speak_callback,
        webui_port: int = 8090,
        proactive_callback=None,
    ) -> None:
        if self.worker_manager is not None:
            return
        try:
            from core.brain.ambient_listening import AmbientListeningManager
            from core.brain.unified_idle_mind import UnifiedIdleMindManager
            from core.workers.worker_manager import get_worker_manager

            loop = asyncio.get_running_loop()
            self.ambient_listener = AmbientListeningManager(self.full_config)
            self.ambient_listener.start(loop)
            if speak_callback is not None:
                self._speak_callback = speak_callback
            self.worker_manager = get_worker_manager(loop, message_history=self.history)
            from .care.worker_bridge import install as install_care_workers
            install_care_workers(self.worker_manager)
            # Clean persisted duplicate care workers before the first tick.
            sync_care_mode(self)
            # A forwarder, not the callback itself: the worker scheduler outlives
            # every session, and a bound method captured here would keep speaking
            # into a socket that closed hours ago.
            self.worker_manager._speak_text = self._speak_via_session
            self.worker_manager.start_scheduler()
            await self.worker_manager.fire_event("startup")
            self.idle_manager = UnifiedIdleMindManager(
                loop,
                self.history,
                self.worker_manager,
                self.full_config,
                ambient_listener=self.ambient_listener,
            )
            # Unified Idle Mind normally owns time injection. On this route its
            # instance delegates that job to the gateway's combined injector,
            # so background pre-warms and foreground turns use one clock, one
            # gate and one append-only time/power/body row.
            self.idle_manager.maybe_inject_time = self._inject_time_and_battery
            self.idle_manager.start_monitor()
            # The scheduler now exists, so a process that BOOTED into health
            # mode can materialise its care routines. A no-op in every other
            # mode, and a no-op again if health mode is entered later -- the
            # switch path calls the same function.
            sync_care_mode(self)
            if proactive_callback is not None:
                self._proactive_callback = proactive_callback
            if self.proactive_questions_enabled:
                self.proactive_question_task = asyncio.create_task(
                    self._proactive_question_loop()
                )
            try:
                from webui.server import start_webui

                # Flask binds on its own thread, so a failure there never
                # reaches this except -- and start_webui prints the dashboard
                # URL *before* binding. The result was a log that advertised a
                # working Web UI and an "Address already in use" a line later.
                # Check the port here so the failure names itself.
                if _port_in_use(webui_port):
                    LOG.error(
                        "Web UI NOT started: port %d is already in use. Set "
                        "KIKI_GATEWAY_WEBUI_PORT to a free port in gateway.env.",
                        webui_port,
                    )
                    raise OSError(f"port {webui_port} in use")
                LOG.info("Web UI on http://0.0.0.0:%d", webui_port)
                start_webui(
                    port=webui_port,
                    query_handler=self.submit_web_query,
                    status_provider=lambda: {
                        "status": "connected",
                        "messages_in_context": len(self.history),
                        "ambient_sentences_pending": self.ambient_listener.pending_count,
                        "whatsapp_mcp_ready": bool(
                            self.whatsapp_manager is not None
                            and getattr(self.whatsapp_manager, "ready", False)
                        ),
                    }
                )
            except Exception:
                LOG.warning("legacy web UI unavailable", exc_info=True)
        except Exception:
            LOG.exception("legacy background runtime failed to start")

        async def start_whatsapp() -> None:
            try:
                from core.self_extend.whatsapp_mcp import start_whatsapp_mcp_background

                self.whatsapp_manager = await asyncio.to_thread(
                    start_whatsapp_mcp_background, self.full_config
                )
            except Exception:
                LOG.warning("WhatsApp background service unavailable", exc_info=True)

        asyncio.create_task(start_whatsapp())

    async def _speak_via_session(self, text: str):
        """Speak through whichever session is connected now, or not at all."""
        callback = self._speak_callback
        if callback is None:
            LOG.info("worker speech skipped: no device connected")
            return False
        return await callback(text)

    async def _proactive_question_loop(self) -> None:
        delay = random.uniform(
            self.proactive_question_min_seconds,
            self.proactive_question_max_seconds,
        )
        LOG.info(
            "proactive questions enabled; first attempt in %.0f seconds",
            delay,
        )
        try:
            while True:
                await asyncio.sleep(delay)
                if self.idle_manager is not None and self.idle_manager.is_thinking:
                    LOG.info("proactive question deferred: idle mind is active")
                    delay = self.proactive_question_busy_retry_seconds
                    continue
                # This is deliberately the same source selector used by the
                # RPi vision loop. Unified Idle Mind has already considered
                # conversation, ambient context, knowledge and its thinking
                # journal with its dedicated cloud model. The panel has no
                # camera, so an empty scene string correctly leaves the chosen
                # next-turn note as the only possible source.
                try:
                    prompt = (
                        self.idle_manager.get_proactive_injection("")
                        if self.idle_manager is not None
                        else None
                    )
                except Exception:
                    LOG.exception("could not read the idle-mind proactive note")
                    prompt = None
                if not prompt:
                    LOG.info("proactive question skipped: no grounded source")
                    delay = random.uniform(
                        self.proactive_question_min_seconds,
                        self.proactive_question_max_seconds,
                    )
                    continue
                # Read late: this loop outlives every individual session, so a
                # callback captured at startup would be speaking into a socket
                # that closed hours ago.
                callback = self._proactive_callback
                if callback is None:
                    LOG.info("proactive question skipped: no device connected")
                    delay = self.proactive_question_busy_retry_seconds
                    continue
                try:
                    spoken = bool(await callback(prompt))
                except Exception:
                    LOG.exception("proactive question attempt failed")
                    spoken = False
                # The RPi resets its random interval when the autonomous event
                # is dispatched, whether the speaking model talks or elects to
                # stay quiet. Do the same here so a silent cloud response cannot
                # hammer the provider once per busy-retry interval.
                delay = random.uniform(
                    self.proactive_question_min_seconds,
                    self.proactive_question_max_seconds,
                )
                if spoken:
                    LOG.info("next proactive question attempt in %.0f seconds", delay)
                else:
                    LOG.info(
                        "proactive question not spoken; next attempt in %.0f seconds",
                        delay,
                    )
        except asyncio.CancelledError:
            return

    async def record_ambient(self, text: str) -> None:
        if self.ambient_listener:
            await asyncio.to_thread(self.ambient_listener.add_sentence, text)

    def _conversation_text(self) -> str:
        """The history as the summarizer wants it, exactly as main.py builds it.

        System rows are dropped except the time anchors, which are kept so the
        session summary -- and the combined past summary built from several of
        them -- carries timestamps.
        """
        lines = []
        for message in self.history[1:]:
            content = message.get("content")
            if not content:
                continue
            if message.get("role") == "system":
                if "right now it's" in str(content).lower():
                    lines.append(f"[TIME] {content}")
                continue
            body = content if isinstance(content, str) else "[image]"
            lines.append(f"{message['role'].upper()}: {body}")
        return "\n".join(lines)

    def _summarize(self, conversation: str) -> str:
        prompts = self.full_config.get("prompts", {}) or {}
        template = prompts.get("summarization_prompt", "Summarize this: {conversation}")
        from core.brain.generate_llm_resp import generate

        # Cloud, like main.py: summarization fires right after a turn, inside
        # the hot window, and must never compete for the single-slot box that
        # has to stay warm for speaking.
        return generate(template.format(conversation=conversation), purpose="summary") or ""

    async def maybe_summarize(self) -> bool:
        budget = getattr(self, "context_budget", None)
        if budget is None:
            return False
        snapshot = list(self.history)
        count = await asyncio.to_thread(budget.count, snapshot)
        LOG.info("model context %d/%d tokens; reply reserve %d; compact at %d",
                 count, budget.n_ctx, budget.reply_tokens, budget.soft_limit)
        observer = getattr(self, "observability", None)
        if observer is not None:
            observer.recorder.record("context", name="model budget", tokens=count,
                capacity=budget.n_ctx, reply_reserve=budget.reply_tokens,
                compact_at=budget.soft_limit)
        if count < budget.soft_limit:
            return False
        task = budget.start(snapshot)
        return bool(await asyncio.shield(task)) if task is not None else False

    def _summarize_context(self, conversation: str) -> str:
        from core.brain.generate_llm_resp import generate
        summary = generate(
            "Compress the conversation below into at most 150 words of factual memory. "
            "Preserve names, decisions, unresolved requests and essential details from "
            "earlier summaries. Omit pleasantries, repeated device status and empty replies. "
            "This is memory for the next conversational turn, not an answer to the user.\n\n"
            + conversation, purpose="summary",
        ) or ""
        if summary:
            from core.brain.summary_manager import save_summary, save_summary_to_conversations_folder
            save_summary_to_conversations_folder(summary)
            save_summary(summary)
            observer = getattr(self, "observability", None)
            if observer is not None:
                observer.recorder.record("summarization", name="cloud summary ready",
                                         phase="end", summary=summary)
        return summary

    def schedule_compaction(self) -> None:
        task = getattr(self, "_context_check_task", None)
        if task is None or task.done():
            async def check():
                try:
                    await self.maybe_summarize()
                except Exception:
                    LOG.exception("background context check failed")
            self._context_check_task = asyncio.create_task(check())

    async def _prepare_context(self, text: str) -> None:
        budget = getattr(self, "context_budget", None)
        if budget is None:
            return
        changed = budget.apply_ready(self.history)
        candidate = self.history + [{"role": "user", "content": text}]
        fitted = await asyncio.to_thread(budget.fit, candidate)
        if fitted != candidate:
            self.history[:] = fitted[:-1]
            candidate = fitted
            changed = True
        count = await asyncio.to_thread(budget.count, candidate)
        if count >= budget.soft_limit:
            self.schedule_compaction()
        if count > budget.hard_limit:
            fitted = await asyncio.to_thread(budget.fit, candidate)
            self.history[:] = fitted[:-1]
            changed = True
        if changed:
            await asyncio.to_thread(self.register_history, self.history)

    async def after_response(self, user_text: str, response_text: str) -> None:
        self.schedule_compaction()
        if self.idle_manager:
            self.idle_manager.note_turn(user_text, response_text)
            self.idle_manager.mark_next_turn_note_used(response_text)
        if self.worker_manager:
            await self.worker_manager.fire_event("after_response")

    async def save_session_summary(self) -> None:
        """Write this session's summary the way main.py's shutdown path does.

        The gateway only ever *loaded* a summary and never produced one, so
        Kiki's cross-session memory on this route was frozen at whatever the Pi
        last wrote -- every boot re-read the same file, and `conversations/`
        stopped growing the day the board took over.
        """
        # This used to run on every WebSocket close -- and the board reconnects
        # on its own every couple of minutes, so a cloud summarization call was
        # being spent on a "session" that was one dropped link in the middle of
        # a sentence. The core now outlives the socket, so this runs when the
        # conversation is really over: at process shutdown, or after a long
        # quiet spell. Two guards remain: a real exchange has to have happened,
        # and it has to be an exchange the last summary did not already cover.
        spoken_turns = sum(
            1 for message in self.history[1:]
            if message.get("role") in ("user", "assistant") and message.get("content")
        )
        conversation = self._conversation_text()
        if spoken_turns < 2 or not conversation.strip():
            LOG.info("no conversation to summarize (%d turns)", spoken_turns)
            return
        if len(self.history) <= self._summarized_upto:
            LOG.info("conversation already summarized; nothing new to save")
            return
        self._summarized_upto = len(self.history)
        try:
            from core.brain.summary_manager import (
                save_summary,
                save_summary_to_conversations_folder,
            )

            summary = await asyncio.to_thread(self._summarize, conversation)
            if summary:
                await asyncio.to_thread(save_summary_to_conversations_folder, summary)
                await asyncio.to_thread(save_summary, summary)
                LOG.info("session summary saved (%d chars)", len(summary))
            else:
                # Better a raw transcript than nothing: the next boot still has
                # something to read, exactly as main.py falls back.
                await asyncio.to_thread(
                    save_summary_to_conversations_folder, conversation
                )
                LOG.warning("summarization failed; saved the raw conversation")
        except Exception:
            LOG.exception("could not save the session summary")

    async def shutdown(self) -> None:
        """Tear the runtime down for good. NOT called when a board disconnects.

        A dropped WebSocket is not the end of a conversation -- it is a link
        hiccup, and the board reconnects by itself. Stopping the scheduler and
        writing a session summary on every one of those was the expensive half
        of the reconnect: it lost the conversation, spent a cloud call, and left
        the panel waiting on a fresh KV-cache warm.
        """
        bridge = getattr(self, "health_bridge", None)
        if bridge is not None:
            bridge.stop()
        if self.proactive_question_task:
            self.proactive_question_task.cancel()
            await asyncio.gather(self.proactive_question_task, return_exceptions=True)
            self.proactive_question_task = None
        await self.save_session_summary()
        if self.idle_manager:
            self.idle_manager.stop()
        if self.worker_manager:
            await self.worker_manager.fire_event("shutdown")
            await asyncio.to_thread(self.worker_manager.stop_scheduler)

    @staticmethod
    def _tool_result_note(calls: list[dict], result: str) -> str:
        if any(call.get("name") == "complex_query" for call in calls or []):
            return (
                "You just carried out that multi-step request. Relay the full outcome "
                "conversationally and completely. Do not add facts or mention tools:\n" + result
            )
        # Length-neutral on purpose. "Answer in one or two spoken sentences"
        # was applied to EVERY tool result, so any question that happened to
        # trip a tool came back as a stub no matter what was asked. Measured on
        # the box with the real prompt: "tell me a long story" answered in 591
        # characters under that wording -- deflecting with "give me a vibe,
        # what genre?" -- and 2744 characters, an actual story, with this one.
        # A 4.6x difference, and the deflection is what reads as her refusing.
        #
        # The brevity it was reaching for is still right for a quick fact, so
        # that is said as guidance rather than as a cap.
        return (
            "Here is the result of a lookup you just did. Use it to answer what was "
            "actually asked, at whatever length that deserves -- a quick fact needs a "
            "sentence, a story needs a story. Report only this result and do not "
            "mention tools:\n"
            + result
        )

    async def prefill_partial(self, text: str) -> None:
        budget = getattr(self, "context_budget", None)
        if budget is not None:
            try:
                count = await asyncio.to_thread(
                    budget.count, self.history + [{"role": "user", "content": text}])
                if count > budget.hard_limit:
                    self.schedule_compaction()
                    return
            except Exception:
                return  # speculation is optional; the real turn handles errors
        await asyncio.to_thread(self.local_llm.prefill_partial, text)

    async def ensure_ready(self) -> None:
        """Wait until the exact speaking prefix is resident in llama.cpp KV cache."""
        await asyncio.to_thread(self.local_llm.rewarm)

    def active_hotwords(self) -> tuple[str, ...]:
        """The names the ACTIVE mode answers to. Empty hands it to the gateway.

        Resolution order, most specific first:

        1. ``assistant_modes.modes.<mode>.hotwords`` -- a list (or a comma
           string) written per mode. The explicit override.
        2. ``assistant_modes.hotwords`` -- one list for every mode.
        3. **the mode's own TTS voice name**, which is the convention that makes
           this work with a config file nobody edited: a mode that owns a voice
           is a character with its own name, so ``rohan`` answers to "rohan"
           and ``jarvis`` to "jarvis". ``default``, ``tutor``, ``evil`` and
           ``senior`` leave ``voice`` empty because they *are* Kiki, so they
           fall through to the gateway's own list and stay "kiki".

        Read fresh every utterance rather than cached at startup: a spoken
        "switch to rohan mode" has to change what she answers to immediately,
        and the mode lives in ``runtime_controls``, not in this object.
        """
        try:
            from core.runtime_controls import get_active_mode

            mode = get_active_mode()
        except Exception:
            LOG.debug("runtime_controls unavailable; using the default hotwords")
            return ()
        settings = self.full_config.get("assistant_modes") or {}
        modes = settings.get("modes") or {}
        entry = modes.get(mode) or {}
        if not isinstance(entry, dict):
            entry = {}
        words = _as_hotword_list(entry.get("hotwords"))
        if not words:
            words = _as_hotword_list(settings.get("hotwords"))
        if not words and settings.get("hotword_from_voice", True):
            words = _as_hotword_list(entry.get("voice"))
        return words

    def current_voice(self) -> str:
        try:
            from core.runtime_controls import get_active_voice

            # The ESP32 gateway owns synthesis, so the mode configuration is
            # the source of truth.  core.tts only applies a mode voice after a
            # live switch and therefore stayed on its default after startup.
            return get_active_voice()
        except Exception:
            return ""

    def _execute_calls(self, calls: list[dict]) -> str:
        results = []
        for call in calls:
            name = call.get("name", "")
            try:
                arguments = json.loads(call.get("arguments") or "{}")
            except (TypeError, json.JSONDecodeError):
                arguments = {}
            # Care tools first: in health mode `get_care_plan` and
            # `update_care_plan` must reach THIS package's store, not the older
            # `senior` plan file the loaded runtime's versions write to. Both
            # would otherwise appear to work while saving to different files.
            handled = self._execute_care_call(name, arguments)
            if handled is None and self.device_executor:
                handled = self.device_executor(name, arguments)
            result = handled if handled is not None else self.execute_tool_calls([call])
            results.append(str(result))
        return "\n".join(results)

    @staticmethod
    def _execute_care_call(name: str, arguments: dict) -> str | None:
        """Run a care tool, or None when this is not one / not health mode."""
        try:
            from .care.mode import care_active
            from .care.tools import execute_care_tool, is_care_tool

            if not care_active():
                return None
            if name == "complex_query":
                # Care scheduling sent here becomes a background worker that is
                # not in the care plan. Refuse it with instructions rather than
                # letting Kiki announce a schedule that does not exist.
                from .care.tools import redirect_complex_query

                return redirect_complex_query(arguments)
            if not is_care_tool(name):
                return None
            return execute_care_tool(name, arguments)
        except Exception:
            LOG.exception("care tool %s failed", name)
            return f"ERROR: the care tool {name} failed. Nothing was changed."

    def reload_config(self) -> bool:
        """Re-read config.json into the live process.

        Without this the wizard writes a setting nobody sees: config_loader
        caches config.json at import, so the running runtime keeps answering
        from the values it started with.
        """
        try:
            from tools_and_config.config_loader import reload_config

            ok, error = reload_config()
            if not ok:
                LOG.warning("config reload failed: %s", error)
            return bool(ok)
        except Exception:
            LOG.exception("config reload unavailable")
            return False

    def _build_system_prompt(self) -> str:
        """The active mode's prompt plus the language instruction, then ours.

        Going through runtime_controls rather than reading `llm.system_prompt`
        directly is the whole point: that function is what selects a character
        mode's own prompt and what appends "Always output hindi devnagri." when
        the language is Hindi. Reading the raw config key bypassed both, so on
        this route modes and Hindi silently did nothing -- the setting saved,
        and Kiki kept speaking English in her default voice.
        """
        try:
            from core.runtime_controls import (
                context_enabled,
                get_active_system_prompt,
                mode_has_own_character,
            )

            base = get_active_system_prompt()
            own_character = mode_has_own_character()
            include_memory = context_enabled("memory")
        except Exception:
            LOG.warning("runtime_controls unavailable; using the default prompt")
            base = self._default_prompt
            own_character = False
            include_memory = True

        # Compatibility for small test/fallback cores created before these
        # suffixes were split by mode ownership.
        if not hasattr(self, "_mode_neutral_prompt_suffix"):
            return base + self._prompt_suffix

        prompt = base + self._mode_neutral_prompt_suffix
        if not own_character:
            prompt += self._kiki_personality_suffix
        # The health layer rides ON TOP of Kiki's own prompt rather than
        # replacing it, which is why `health_sih` deliberately declares no
        # `system_prompt` of its own (see care/mode.py). In every other mode
        # this adds nothing at all.
        try:
            from .care.mode import SYSTEM_PROMPT_ADDENDUM, care_active

            if care_active():
                prompt += SYSTEM_PROMPT_ADDENDUM
        except Exception:
            LOG.exception("health layer unavailable; using the plain prompt")
        if include_memory:
            budget = getattr(self, "context_budget", None)
            if budget is not None:
                try:
                    return budget.bound_memory(prompt, self._memory_prompt_suffix)
                except Exception:
                    LOG.exception("could not budget optional memory; deferring recall")
                    return prompt
            prompt += self._memory_prompt_suffix
        return prompt

    def _replace_system_prompt(self, messages: list[dict]) -> bool:
        """Put the active mode at message zero without discarding the turn."""
        # Called exactly when the active mode may have changed -- inside a
        # `switch_mode` tool turn and from `refresh_system_prompt`. Starting or
        # standing down the care stack here means a spoken "health mode" takes
        # effect on the same turn, and leaving it takes effect immediately too.
        sync_care_mode(getattr(self, "worker_manager", None) and self or None)
        rebuilt = self._build_system_prompt()
        if messages and messages[0].get("role") == "system":
            if messages[0].get("content") == rebuilt:
                return False
            messages[0] = {"role": "system", "content": rebuilt}
        else:
            messages.insert(0, {"role": "system", "content": rebuilt})
        return True

    def refresh_system_prompt(self) -> bool:
        """Re-apply mode/language to the live prefix. Returns True if it moved.

        This replaces history[0], which invalidates the whole cached prefix, so
        it re-warms exactly as main.py does after a mode switch. Only call it
        when something actually changed.
        """
        if not self._replace_system_prompt(self.history):
            return False
        self.register_history(self.history)
        LOG.info("system prompt rebuilt (mode/language change)")
        return True

    def update_battery_context(
        self, percent: object, on_usb: object = None, charging: object = None
    ) -> None:
        """Accept the board's latest fuel-gauge reading for the next LLM turn.

        The telemetry callback runs on asyncio's thread while generation reads
        this on a worker thread. Replacing one immutable tuple is atomic in
        CPython and avoids sharing a mutable telemetry dictionary.
        """
        try:
            value = int(percent)
        except (TypeError, ValueError):
            value = -1
        self._battery_percent = value if 0 <= value <= 100 else None
        self._battery_context = (
            self._battery_percent,
            on_usb if isinstance(on_usb, bool) else None,
            charging if isinstance(charging, bool) else None,
        )

    def update_motion_telemetry(self, telemetry: object) -> None:
        """Keep a sanitized semantic snapshot; no raw sensor values reach prompts."""
        if not isinstance(telemetry, dict):
            return
        posture = str(telemetry.get("imu_posture", "")).strip().lower()
        available = posture in MOTION_POSTURE_LABELS
        posture = posture if available else None
        try:
            linear_g = max(0.0, float(telemetry.get("imu_linear_g", 0.0)))
            gyro_dps = max(0.0, float(telemetry.get("imu_gyro_dps", 0.0)))
        except (TypeError, ValueError):
            linear_g = gyro_dps = 0.0
        try:
            annoyance = max(0.0, min(3.0, float(telemetry["motion_annoyance"])))
        except (KeyError, TypeError, ValueError):
            annoyance = None

        now = time.monotonic()
        with self._motion_lock:
            # Hysteresis prevents a quiet table from alternating moving/still
            # around one noisy sample every five seconds.
            if self._motion_moving:
                moving = linear_g > 0.06 or gyro_dps > 12.0
            else:
                moving = linear_g > 0.12 or gyro_dps > 24.0
            if not available:
                moving = None
            old_signature = (
                self._motion_available,
                self._motion_posture,
                self._motion_moving,
                motion_annoyance_label(self._motion_annoyance),
            )
            if moving is False:
                if self._motion_moving is not False or self._motion_still_since is None:
                    self._motion_still_since = now
            else:
                self._motion_still_since = None
            self._motion_available = available
            self._motion_posture = posture
            self._motion_moving = moving
            self._motion_annoyance = annoyance
            self._motion_seen_at = now
            new_signature = (
                available,
                posture,
                moving,
                motion_annoyance_label(annoyance),
            )
            if new_signature != old_signature:
                self._motion_revision += 1

    def update_motion_event(self, name: object, posture: object = None) -> None:
        """Record one whitelisted physical event for the next compact anchor."""
        event = str(name or "").strip().lower()
        if event not in MOTION_EVENT_LABELS:
            return
        posture_name = str(posture or "").strip().lower()
        now = time.monotonic()
        with self._motion_lock:
            self._motion_available = True
            if posture_name in MOTION_POSTURE_LABELS:
                self._motion_posture = posture_name
            self._motion_seen_at = now
            self._motion_event = event
            self._motion_event_at = now
            self._motion_revision += 1

    def update_wearable_health(self, summary: object) -> None:
        """Receive a material background refresh for the existing live row."""
        if not isinstance(summary, dict):
            return
        line = " ".join(str(summary.get("context") or "").split())[:520]
        if line and not line.startswith("Wearable:"):
            line = "Wearable: " + line
        with self._health_lock:
            if line == self._health_line:
                self._health_summary = json.loads(json.dumps(summary))
                return
            self._health_summary = json.loads(json.dumps(summary))
            self._health_line = line
            self._health_revision += 1

    def _health_context_suffix(self) -> str:
        with self._health_lock:
            return "\n" + self._health_line if self._health_line else ""

    def dispatch_wearable_whatsapp_alert(self, alert: object) -> list[dict]:
        """Have the existing complex action agent send the fall alert via WhatsApp MCP."""
        if not isinstance(alert, dict):
            return []
        message = str(alert.get("message") or "").strip()
        outcomes = []
        # The configured family contact is the explicit emergency recipient for wearable Kiki. The Pi
        # care-plan contact list may still be empty during setup, which used to
        # turn a confirmed fall into zero dispatch attempts.
        recipients = list(alert.get("whatsapp_recipients") or []) or ["Family"]
        for recipient in recipients:
            outcome = {"channel": "whatsapp", "recipient": str(recipient),
                       "accepted": False, "alert_id": alert.get("id")}
            try:
                from core.brain.action_agent import run_complex_query
                result = asyncio.run(run_complex_query(
                    "URGENT AUTOMATED WEARABLE FALL ALERT. Use the WhatsApp MCP "
                    f"send_message tool now to send exactly this message to {recipient}: "
                    f"{message!r}. Do not ask a question, do not merely draft it, and do "
                    "not contact anyone else. Report whether the tool actually succeeded.",
                    context="Confirmed by wearable after the on-device 7-second check-in expired.",
                ))
                failed = str(result).upper().startswith(("ACTION FAILED", "ACTION INCOMPLETE"))
                outcome["accepted"] = not failed
                outcome["provider_response"] = str(result)[:500]
            except Exception as exc:
                outcome["error"] = str(exc)[:400]
            outcomes.append(outcome)
        return outcomes

    @staticmethod
    def choose_motion_question(name: object) -> str | None:
        """Reserve a random, never-before-asked line from the thinking journal."""

        from core.brain.thinking_journal import get_journal

        return get_journal().choose_movement_question(str(name or ""))

    def record_motion_question(self, name: object, question: object) -> None:
        """Append directly spoken motion speech to the model's real history.

        The compact live anchor records the physical event first.  Retaining
        the assistant question then lets the user's ordinary follow-up make
        sense without inventing a user message or asking the model to rewrite
        the journal-selected line.
        """

        text = " ".join(str(question or "").split()).strip()
        if not text:
            return
        self._maybe_inject_time()
        self.history.append({"role": "assistant", "content": text[:300]})
        self.register_history(self.history, after_speaking=True)

    def record_exchange(self, user_text: object, assistant_text: object) -> None:
        """Write a turn that bypassed the model into its history.

        The dance fast path answers "kiki, dance" by dancing rather than by
        generating, so without this the model's next turn would have no idea
        any of it happened -- and "what song is this?" would be unanswerable.

        Append-only, exactly like every other writer here: llama.cpp's cached
        prefix is this list, and rewriting any earlier row costs a full
        re-prefill (§4 rule 1).
        """
        user = " ".join(str(user_text or "").split()).strip()
        assistant = " ".join(str(assistant_text or "").split()).strip()
        if not user and not assistant:
            return
        self._maybe_inject_time()
        if user:
            self.history.append({"role": "user", "content": user[:400]})
        if assistant:
            self.history.append({"role": "assistant", "content": assistant[:400]})
        self.register_history(self.history, after_speaking=True)

    @staticmethod
    def _motion_age_text(age_seconds: float) -> str:
        seconds = max(0, int(age_seconds))
        if seconds < 5:
            return "just now"
        if seconds < 60:
            return f"{seconds}s ago"
        return f"{seconds // 60}m ago"

    def _motion_context_locked(self, now: float) -> str:
        if not self._motion_available or now - self._motion_seen_at > 20.0:
            return "\nBody: unavailable."
        posture = MOTION_POSTURE_LABELS.get(
            self._motion_posture or "unknown", "orientation unknown"
        )
        if self._motion_moving is True:
            activity = "moving"
        elif self._motion_moving is False:
            still_since = self._motion_still_since
            still_for = now - still_since if still_since is not None else 0.0
            activity = f"still {int(still_for) // 60}m" if still_for >= 60 else "still"
        else:
            activity = "activity unknown"
        parts = [f"{posture}, {activity}"]

        if self._motion_event and self._motion_event_at:
            event_age = now - self._motion_event_at
            lifetime = 600.0 if self._motion_event in _LONG_LIVED_MOTION_EVENTS else 45.0
            if event_age <= lifetime:
                parts.append(
                    f"{MOTION_EVENT_LABELS[self._motion_event]} "
                    f"{self._motion_age_text(event_age)}"
                )
        mood = motion_annoyance_label(self._motion_annoyance)
        if mood:
            parts.append(mood)
        return "\nBody: " + "; ".join(parts) + "."

    def _motion_context_suffix(self, now: float | None = None) -> str:
        moment = time.monotonic() if now is None else now
        with self._motion_lock:
            return self._motion_context_locked(moment)

    def _battery_remark_due(self, now: float | None = None) -> bool:
        """Offer one autonomous battery joke per 90 minutes of conversation.

        An opportunity is consumed when supplied to a turn, even if Kiki
        correctly skips it because the moment is serious. That makes the gate
        an actual upper bound rather than a prompt the model sees repeatedly
        until it happens to make the joke.
        """
        moment = time.monotonic() if now is None else now
        if self._conversation_started_at is None:
            self._conversation_started_at = moment
            return False
        if self._battery_percent is None:
            return False
        anchor = (
            self._last_battery_remark_opportunity_at
            if self._last_battery_remark_opportunity_at is not None
            else self._conversation_started_at
        )
        if moment - anchor < self.battery_remark_interval_seconds:
            return False
        self._last_battery_remark_opportunity_at = moment
        return True

    def _battery_context_suffix(self) -> str:
        """Text joined to the cache-safe periodic time anchor."""
        percent, on_usb, charging = self._battery_context
        if percent is None:
            return "\nPower: unavailable."
        name, _description = battery_personality(percent)
        if charging:
            source = " charging"
        elif on_usb:
            source = " plugged in"
        elif on_usb is False:
            source = " on battery"
        else:
            source = ""
        return f"\nPower: {percent}%{source} ({name})."

    def _build_battery_remark_opportunity(self) -> dict:
        """One append-only permission whose scope expires after the next reply."""
        name, _description = battery_personality(self._battery_percent)
        return {
            "role": "system",
            "content": (
                "## ONE-TURN BATTERY REMARK OPPORTUNITY\n"
                "This instruction applies only to the assistant reply to the immediately "
                "following user message. In that one reply, Kiki may include at most one "
                "brief, original, naturally placed battery joke or power-flavoured remark "
                f"and may mention the exact current value, {self._battery_percent}% "
                f"({name}). Skip it if the moment is serious or it would feel forced. "
                "As soon as that assistant reply exists, this permission is expired and "
                "must never be reused on a later turn."
            ),
        }

    def _inject_time_and_battery(self, rewarm: bool = False) -> bool:
        """Append one shared, cache-safe clock, power and body anchor.

        Without this she has no clock at all on the speaking path: observed live,
        she answered "what's happening today" by web-searching for *the current
        time*, because nothing in her context said what it was. The gateway uses
        this same method for foreground turns and replaces this session's idle-
        mind callback with it, preventing competing clocks or battery-free
        background anchors.
        """
        interval = (self.full_config.get("agent", {}) or {}).get(
            "time_injection_threshold_minutes", 5
        ) * 60
        now = time.time()
        with self._motion_lock:
            motion_dirty = self._motion_revision != self._motion_injected_revision
        health_lock = getattr(self, "_health_lock", None)
        if health_lock is not None:
            with health_lock:
                health_dirty = getattr(self, "_health_revision", 0) != getattr(
                    self, "_health_injected_revision", 0)
        else:
            health_dirty = False
        time_due = now - self._last_time_injected > interval
        # Idle Mind polls this callback every 15 seconds. Motion alone must not
        # cause background prompt appends/rewarm work; it waits for a real turn.
        if not time_due and (not (motion_dirty or health_dirty) or rewarm):
            return False
        if rewarm and self.local_llm.conversation_hot():
            return False
        stamp = datetime.now().strftime("%I:%M %p %a, %b %d, %Y")
        motion_now = time.monotonic()
        with self._motion_lock:
            motion_revision = self._motion_revision
            motion_suffix = self._motion_context_locked(motion_now)
        if health_lock is not None:
            with health_lock:
                health_revision = getattr(self, "_health_revision", 0)
                health_line = getattr(self, "_health_line", "")
                health_suffix = "\n" + health_line if health_line else ""
        else:
            health_revision = 0
            health_suffix = ""
        self.history.append(
            {
                "role": "system",
                "content": f"Now: {stamp}."
                + self._battery_context_suffix()
                + motion_suffix
                + health_suffix
                + _care_context_suffix(),
            }
        )
        self._last_time_injected = now
        with self._motion_lock:
            self._motion_injected_revision = max(
                self._motion_injected_revision, motion_revision
            )
        if health_lock is not None:
            with health_lock:
                self._health_injected_revision = max(
                    getattr(self, "_health_injected_revision", 0), health_revision
                )
        if rewarm:
            self.register_history(self.history)
            LOG.info("pre-injected compact live context: %s", stamp)
        else:
            LOG.info("injected compact live context: %s", stamp)
        return True

    def _maybe_inject_time(self) -> None:
        self._inject_time_and_battery(rewarm=False)
    async def stream_reply(self, text: str, abort: threading.Event) -> AsyncIterator[tuple[str, object]]:
        loop = asyncio.get_running_loop()
        output: asyncio.Queue[tuple[str, object] | None] = asyncio.Queue()
        # Before the history snapshot, so this turn actually sees the anchor.
        self._maybe_inject_time()
        if self._battery_remark_due():
            # Keep the one-shot row in history. Its explicit "immediately
            # following user message" scope expires once this turn's assistant
            # row follows it, while append-only history remains byte-identical
            # to llama.cpp's cached sequence on the next turn.
            self.history.append(self._build_battery_remark_opportunity())
        if getattr(self, "context_budget", None) is not None:
            await self._prepare_context(text)
        messages = list(self.history) + [{"role": "user", "content": text}]

        def emit(item: tuple[str, object] | None) -> None:
            loop.call_soon_threadsafe(output.put_nowait, item)

        def run() -> None:
            try:
                tool_round = 0
                empty_retries = 0
                budget = getattr(self, "context_budget", None)
                while not abort.is_set():
                    raw = ""
                    pending_tools = None
                    said_something = False
                    if budget is not None:
                        # Includes tool-result follow-ups, which can be much
                        # larger than the original spoken question.
                        messages[:] = budget.fit(messages)
                    observer = getattr(self, "observability", None)
                    if observer is not None:
                        observer.context(budget.normalize(messages) if budget else messages)
                    for event, data in self.stream_response(
                        messages,
                        verify_prefill=(tool_round == 0),
                        abort_event=abort,
                    ):
                        if event == "sentence":
                            # A tool turn is required to contain the call and
                            # nothing else, but small speaking models sometimes
                            # append a confident confirmation after the closing
                            # tag.  That text was reaching TTS before the tool
                            # result was inspected.  For play_music it meant the
                            # confirmation and the song were sent to the board
                            # together.  Ignore same-round prose after a call;
                            # a failed tool gets its grounded explanation from
                            # the normal result-follow-up round below.
                            if pending_tools is None:
                                said_something = said_something or bool(str(data).strip())
                                emit(("sentence", data))
                        elif event == "tool_calls":
                            pending_tools = data
                            emit(("tool_calls", data))
                        elif event == "done":
                            raw = data
                    if abort.is_set():
                        break
                    if not pending_tools and not said_something and str(raw or "").strip():
                        emit(("sentence", raw))
                        said_something = True
                    if not pending_tools and not said_something:
                        if empty_retries == 0 and budget is not None:
                            empty_retries += 1
                            LOG.warning("empty model reply; retrying once with verified context")
                            messages[:] = budget.fit(messages, budget.hard_limit - 256)
                            continue
                        raw = "I'm having trouble forming a reply right now. Please try asking me again."
                        emit(("sentence", raw))
                    messages.append({"role": "assistant", "content": raw})
                    if not pending_tools:
                        break
                    names = {call.get("name") for call in pending_tools.get("calls", [])}
                    media_only = bool(names) and names <= {
                        "play_music", "play_liked_songs", "play_last_song",
                        "control_music", "dance",
                    }
                    if tool_round == 0 and not raw.strip() and not media_only:
                        emit(("sentence", "One sec."))
                    calls = pending_tools.get("calls", [])
                    result = self._execute_calls(calls)
                    emit(("tool_result", result))
                    if "switch_mode" in names and self._replace_system_prompt(messages):
                        LOG.info("system prompt switched inside tool turn")
                    # Music that started needs no narration -- the song IS the
                    # reply. This is intentionally independent of `media_only`:
                    # if the model batches a volume/control call with playback,
                    # a result follow-up would still overlap the new song.
                    # Music that FAILED does need a grounded explanation; ask
                    # the device whether anything is actually playing rather
                    # than trusting the tool's own prose.
                    started = bool(self.media_active and self.media_active())
                    if (
                        started
                        or not result
                        or tool_round >= self.max_followup_tool_rounds
                    ):
                        break
                    messages.append(
                        {"role": "system", "content": self._tool_result_note(calls, result)}
                    )
                    tool_round += 1
                if not abort.is_set():
                    # Background managers retain this list by reference. Mutate it
                    # in place so workers, idle mind and the Web UI see every turn.
                    # This list must be the same append-only sequence llama.cpp
                    # just saw, or the next request loses its KV-cache prefix.
                    self.history[:] = messages
                    self.register_history(self.history, after_speaking=True)
                    emit(("done", self.history[-1].get("content", "")))
            except BaseException as exc:
                emit(("error", exc))
            finally:
                emit(None)

        threading.Thread(target=run, daemon=True, name="legacy-kiki-stream").start()
        while True:
            item = await output.get()
            if item is None:
                return
            yield item

    async def stream_proactive(
        self,
        prompt: str,
        abort: threading.Event,
    ) -> AsyncIterator[tuple[str, object]]:
        """Run the RPi autonomous-system turn through Kiki's cloud speaker.

        RPi main.py appends the source-grounded prompt as a system row and then
        invokes the ordinary response pipeline with no fabricated user message.
        Preserve that mechanism here. ``use_fallback=True`` selects the
        configured Vertex/Gemini cloud route for this autonomous turn, as
        requested, while ordinary user turns keep their configured speaker.
        """

        loop = asyncio.get_running_loop()
        output: asyncio.Queue[tuple[str, object] | None] = asyncio.Queue()
        self._maybe_inject_time()
        self.history.append({"role": "system", "content": prompt})
        messages = list(self.history)

        def emit(item: tuple[str, object] | None) -> None:
            loop.call_soon_threadsafe(output.put_nowait, item)

        def run() -> None:
            try:
                tool_round = 0
                while not abort.is_set():
                    raw = ""
                    pending_tools = None
                    for event, data in self.stream_response(
                        messages,
                        use_fallback=True,
                        verify_prefill=(tool_round == 0),
                        abort_event=abort,
                    ):
                        if event == "sentence":
                            emit(("sentence", data))
                        elif event == "tool_calls":
                            pending_tools = data
                            emit(("tool_calls", data))
                        elif event == "done":
                            raw = str(data or "")
                    if abort.is_set():
                        break
                    messages.append({"role": "assistant", "content": raw})
                    if not pending_tools:
                        break
                    calls = pending_tools.get("calls", [])
                    result = self._execute_calls(calls)
                    emit(("tool_result", result))
                    if (
                        any(call.get("name") == "switch_mode" for call in calls)
                        and self._replace_system_prompt(messages)
                    ):
                        LOG.info("system prompt switched inside proactive tool turn")
                    if not result or tool_round >= self.max_followup_tool_rounds:
                        break
                    messages.append(
                        {"role": "system", "content": self._tool_result_note(calls, result)}
                    )
                    tool_round += 1
                if not abort.is_set():
                    self.history[:] = messages
                    self.register_history(self.history, after_speaking=True)
                    response = self.history[-1].get("content", "")
                    if self.idle_manager is not None:
                        self.idle_manager.mark_next_turn_note_used(response)
                    emit(("done", response))
            except BaseException as exc:
                emit(("error", exc))
            finally:
                emit(None)

        threading.Thread(
            target=run,
            daemon=True,
            name="legacy-kiki-proactive",
        ).start()
        while True:
            item = await output.get()
            if item is None:
                return
            yield item

    async def stream_web_instruction(
        self,
        instruction: str,
        abort: threading.Event,
    ) -> AsyncIterator[tuple[str, object]]:
        """Execute one operator instruction as system context, never as user text."""
        prompt = (
            "## ONE-TURN WEBUI SPEAKING INSTRUCTION\n"
            "The operator has explicitly instructed Kiki what to say next. Follow the "
            "instruction now in Kiki's natural voice. Do not quote or mention these "
            "instructions and do not describe what you are doing. If it asks you to ask "
            "the person something, ask it directly and then stop so they can answer.\n\n"
            f"Instruction: {instruction.strip()}"
        )
        async for event, data in self.stream_proactive(prompt, abort):
            yield event, data
        if not abort.is_set():
            # Append-only deactivation preserves the model prefix cache while
            # making it explicit that this instruction must not affect later
            # conversation turns.
            self.history.append({
                "role": "system",
                "content": (
                    "The one-turn WebUI speaking instruction immediately above has been "
                    "completed. Do not repeat or continue it unless a new instruction arrives."
                ),
            })
            self.register_history(self.history, after_speaking=True)

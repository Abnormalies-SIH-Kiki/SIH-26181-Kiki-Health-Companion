#!/usr/bin/env python3
"""
Cerebras vs Groq benchmark for the complex_query action agent.

Run it BEFORE trusting any latency claim about the agent loop — it replaces
estimates with numbers measured from this Pi, on this network.

    source /home/kiki/Kiki/kiki/bin/activate
    python scripts/bench_action_agent.py            # every provider/model
    python scripts/bench_action_agent.py --quick    # one model each

Why this shape: an agent turn is NOT a chat turn. The model must emit a
COMPLETE JSON object before any tool can run, so what matters is
TTFT + (output_tokens / throughput) — not TTFT alone. And every turn resends
the whole conversation, so the run also reports cumulative prompt tokens, which
is what collides with Groq's per-key tokens-per-minute budget.

The simulated run mirrors core/brain/action_agent.py: a compact tool catalog,
the run_agent_loop JSON protocol, and growing tool results fed back each turn.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass


# Python-urllib/requests default UAs get 403'd by both providers' edge; a plain
# browser-ish UA is what curl sends and what works.
_UA = "KikiFast-bench/1.0"

CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

CEREBRAS_MODELS = ["zai-glm-4.7", "gpt-oss-120b", "gemma-4-31b"]
GROQ_MODELS = ["openai/gpt-oss-120b", "qwen/qwen3.6-27b"]


def _groq_keys():
    try:
        keys = json.loads(os.getenv("GROQ_API_KEY_LIST", "[]")) or []
    except json.JSONDecodeError:
        keys = []
    single = os.getenv("GROQ_API_KEY")
    if single and single not in keys:
        keys.append(single)
    return keys


# --- The simulated agent conversation -------------------------------------
# Kept close to the real thing so token counts are honest.

_CATALOG = """- search_contacts(query): Find a WhatsApp contact by name or number.
- list_chats(query?, limit?): List/search WhatsApp chats INCLUDING groups.
- list_messages(after?, before?, sender_phone_number?, chat_jid?, query?, limit?): Read WhatsApp messages.
- get_chat(chat_jid): Get one WhatsApp chat.
- get_last_interaction(jid): Latest interaction with a contact.
- get_message_context(message_id, before?, after?): Context around one message.
- send_message(recipient, message): Send a WhatsApp text message.
- send_file(recipient, media_path): Send a file/image/document over WhatsApp.
- send_audio_message(recipient, media_path): Send an audio file as a voice note.
- download_media(message_id, chat_jid): Download WhatsApp media to a local path.
- read_whatsapp_image(message_id, chat_jid, question?): Describe an image in a WhatsApp message.
- record_voice_note(seconds?): Record from the microphone, return a wav path.
- search_web(query, time_range?): Search the live web.
- read_gmail(query?, max_results?): List Gmail messages with bounded snippets.
- read_gmail_message(message_id): Read one Gmail message body.
- search_notion(query, max_results?): Search the Notion workspace.
- read_notion(entity_id, max_chars?): Read one Notion page.
- set_timer(seconds, label?): Set a countdown timer.
- schedule_worker(name, task_description, trigger_type, trigger_value): Schedule background work.
- update_knowledge(category, action, data): Save a durable memory.
- recall_memory(query): Search past conversations, knowledge and research.
- get_current_time(): Current local date and time."""

_PROTOCOL = """Respond with ONE JSON object and nothing else.
To use tools: {"tool_calls":[{"tool":"name","args":{...}}]}
When finished:  {"status":"completed","summary":"<= 600 chars, spoken aloud"}
If impossible:  {"status":"failed","reason":"..."}"""


def _base_prompt(task: str) -> str:
    return f"""You are Kiki's fast action agent. Vaibhav asked for something that needs
several steps. Do it end to end, then report what actually happened.

REQUEST: {task}

TIME: 2026-07-26T18:40:00

RULES:
- Resolve a recipient with search_contacts or list_chats FIRST. Never invent a JID.
- Chat names are often misheard. "burrito time" may really be the group "Burgito".
  Pick the closest real chat; if two are equally close, ask instead of guessing.
- Never claim you sent, saved, or scheduled anything unless the tool returned success.
- Be efficient: batch independent calls into one tool_calls array.

AVAILABLE TOOLS:
{_CATALOG}

{_PROTOCOL}"""


# Canned tool results, sized like the real thing (capped at 1500 chars).
_TURN_FEEDBACK = [
    """TOOL RESULTS:
Tool 'list_chats' returned: [{"jid":"120363041234567890@g.us","name":"Burgito","last_message_time":"2026-07-26T17:02:11","last_message":"guys tomorrow 7pm at the usual place?","last_sender":"919812345678"},{"jid":"120363099887766554@g.us","name":"Burgito Planning","last_message_time":"2026-07-24T11:20:03","last_message":"ok","last_sender":"919812345670"},{"jid":"919876543210@s.whatsapp.net","name":"Bharat","last_message_time":"2026-07-26T09:11:00","last_message":"call me","last_sender":"919876543210"}]

Continue with your task. If done, respond with {"status": "completed", "summary": "..."}.""",
    """TOOL RESULTS:
Tool 'list_messages' returned: [{"timestamp":"2026-07-26T17:02:11","sender":"919812345678","content":"guys tomorrow 7pm at the usual place?","is_from_me":false,"id":"3EB0A1"},{"timestamp":"2026-07-26T17:03:40","sender":"919812345679","content":"yes 7pm works, Cafe Nirvana","is_from_me":false,"id":"3EB0A2"},{"timestamp":"2026-07-26T17:05:02","sender":"919812345671","content":"I will be 15 min late","is_from_me":false,"id":"3EB0A3"},{"timestamp":"2026-07-26T17:20:44","sender":"919812345678","content":"[image - Message ID: 3EB0A9 - Chat JID: 120363041234567890@g.us] menu screenshot","is_from_me":false,"id":"3EB0A9"}]

ACTIONS ALREADY TAKEN (do NOT repeat these or near-identical ones): list_chats({"query":"burgito"})

Continue with your task. If done, respond with {"status": "completed", "summary": "..."}.""",
    """TOOL RESULTS:
Tool 'schedule_worker' returned: {"ok":true,"worker_id":"w_7f31","name":"Burgito dinner reminder","trigger":"scheduled_time","value":"2026-07-27T18:00:00"}

ACTIONS ALREADY TAKEN (do NOT repeat these or near-identical ones): list_chats({"query":"burgito"}); list_messages({"chat_jid":"120363041234567890@g.us","limit":20})

Continue with your task. If done, respond with {"status": "completed", "summary": "..."}.""",
]

TASK = ("check the recent messages at burgito time and create a reminder if "
        "there are any events tomorrow")


def _post_stream(url, key, model, messages, max_tokens=900, extra=None):
    """Stream one completion. Returns (ttft, total, text, usage, status, err)."""
    body = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if extra:
        body.update(extra)
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": _UA,
    }
    t0 = time.perf_counter()
    ttft = None
    text = []
    usage = {}
    try:
        resp = requests.post(url, headers=headers, json=body,
                             stream=True, timeout=90)
        if resp.status_code != 200:
            return None, time.perf_counter() - t0, "", {}, resp.status_code, resp.text[:200]
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            chunk = line[6:]
            if chunk == "[DONE]":
                break
            try:
                obj = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            if obj.get("usage"):
                usage = obj["usage"]
            for choice in obj.get("choices") or []:
                delta = choice.get("delta") or {}
                piece = delta.get("content") or ""
                if piece:
                    if ttft is None:
                        ttft = time.perf_counter() - t0
                    text.append(piece)
    except Exception as e:
        return ttft, time.perf_counter() - t0, "".join(text), usage, -1, repr(e)
    return ttft, time.perf_counter() - t0, "".join(text), usage, 200, None


def run_case(label, url, key, model, turns=4, extra=None):
    """Simulate a `turns`-turn agent loop and report the aggregate cost."""
    conversation = [{"role": "user", "content": _base_prompt(TASK)}]
    per_turn = []
    prompt_tokens = 0
    completion_tokens = 0
    rate_limited = 0

    for i in range(turns):
        ttft, total, text, usage, status, err = _post_stream(
            url, key, model, conversation, extra=extra)
        if status == 429:
            rate_limited += 1
            print(f"    turn {i+1}: 429 RATE LIMITED — {err[:120]}")
            per_turn.append((None, total))
            break
        if status != 200:
            print(f"    turn {i+1}: HTTP {status} — {err}")
            return None
        prompt_tokens += usage.get("prompt_tokens", 0)
        completion_tokens += usage.get("completion_tokens", 0)
        per_turn.append((ttft, total))
        print(f"    turn {i+1}: ttft={ttft if ttft is None else round(ttft,2)}s "
              f"total={total:.2f}s in={usage.get('prompt_tokens',0)} "
              f"out={usage.get('completion_tokens',0)}")
        if i >= len(_TURN_FEEDBACK):
            break
        conversation.append({"role": "assistant", "content": text})
        conversation.append({"role": "user", "content": _TURN_FEEDBACK[i]})

    loop_time = sum(t for _, t in per_turn)
    ttfts = [t for t, _ in per_turn if t is not None]
    return {
        "label": label,
        "model": model,
        "turns": len(per_turn),
        "loop_seconds": loop_time,
        "avg_ttft": (sum(ttfts) / len(ttfts)) if ttfts else None,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "rate_limited": rate_limited,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="one model per provider")
    ap.add_argument("--turns", type=int, default=4)
    args = ap.parse_args()

    cerebras_key = os.getenv("CEREBRAS_API_KEY")
    groq_keys = _groq_keys()
    results = []

    cere_models = CEREBRAS_MODELS[:1] if args.quick else CEREBRAS_MODELS
    groq_models = GROQ_MODELS[:1] if args.quick else GROQ_MODELS

    if cerebras_key:
        for model in cere_models:
            print(f"\n[Cerebras] {model}")
            r = run_case("cerebras", CEREBRAS_URL, cerebras_key, model, args.turns)
            if r:
                results.append(r)
    else:
        print("CEREBRAS_API_KEY missing — skipping Cerebras")

    if groq_keys:
        # Use a DIFFERENT key per model so one drained key doesn't poison the
        # next measurement (this is exactly what the pool does in production).
        for idx, model in enumerate(groq_models):
            key = groq_keys[idx % len(groq_keys)]
            print(f"\n[Groq] {model} (key #{idx % len(groq_keys)})")
            r = run_case("groq", GROQ_URL, key, model, args.turns)
            if r:
                results.append(r)
    else:
        print("GROQ_API_KEY_LIST missing — skipping Groq")

    print("\n" + "=" * 92)
    print(f"{'provider':10s} {'model':26s} {'turns':>5s} {'loop_s':>7s} "
          f"{'avg_ttft':>9s} {'prompt_tok':>11s} {'out_tok':>8s} {'429':>4s}")
    print("-" * 92)
    for r in results:
        ttft = f"{r['avg_ttft']:.2f}" if r["avg_ttft"] else "-"
        print(f"{r['label']:10s} {r['model'][:26]:26s} {r['turns']:5d} "
              f"{r['loop_seconds']:7.2f} {ttft:>9s} {r['prompt_tokens']:11d} "
              f"{r['completion_tokens']:8d} {r['rate_limited']:4d}")
    print("=" * 92)
    print("\nRead it as: loop_s is the MODEL time for a complex query (tool execution")
    print("adds ~2-4s on top). prompt_tok is what one query costs against Groq's")
    print("8K-per-minute PER-KEY budget — over ~8000 means one key cannot serve one")
    print("query, and rotation is doing the heavy lifting.")


if __name__ == "__main__":
    main()

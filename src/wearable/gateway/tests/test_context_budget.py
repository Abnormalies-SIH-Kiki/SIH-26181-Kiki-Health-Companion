import asyncio
import copy
import threading
from types import SimpleNamespace

import pytest

from kiki_gateway.context_budget import ContextBudget
from kiki_gateway.inference import LegacyKikiCore


def row(role, content):
    return {"role": role, "content": content}


def budget(tmp_path, summarize=lambda text: "short summary"):
    b = ContextBudget("http://unused/v1/chat/completions", lambda m: m,
                      summarize, tmp_path, reply_tokens=512)
    b.n_ctx = 2500
    b.count = lambda messages: sum(len(m["content"]) + 10 for m in messages)
    return b


def test_model_count_uses_normalized_template_and_real_server_capacity(monkeypatch, tmp_path):
    calls = []
    def get(url, **kwargs):
        return SimpleNamespace(raise_for_status=lambda: None,
            json=lambda: {"default_generation_settings": {"n_ctx": 8192}})
    def post(url, json, **kwargs):
        calls.append((url, json))
        if url.endswith("apply-template"):
            assert json["messages"][-1]["content"] == "tool instructions"
            result = {"prompt": "<template>actual model prompt"}
        else:
            assert json["parse_special"] and json["add_special"]
            assert json["content"].startswith("<template>")
            result = {"tokens": list(range(7000))}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: result)
    monkeypatch.setattr("kiki_gateway.context_budget.requests.get", get)
    monkeypatch.setattr("kiki_gateway.context_budget.requests.post", post)
    b = ContextBudget("http://test/v1/chat/completions",
        lambda m: m + [row("system", "tool instructions")], lambda _: "", tmp_path)
    assert b.count([row("user", "hello")]) == 7000
    assert b.hard_limit == 6864  # includes 1200 answer + 128 slack
    assert b.count([row("user", "hello")]) == 7000
    assert len(calls) == 2  # unchanged count reuses cache, never KV generation


def test_oversized_configured_answer_cap_cannot_consume_the_prompt_budget(tmp_path):
    b = ContextBudget("http://test/v1/chat/completions", lambda m: m,
                      lambda _: "", tmp_path, reply_tokens=6000)
    assert b.reply_tokens == 1200
    assert b.hard_limit == 6864


def test_repeated_worker_failures_after_user_are_coalesced_before_tokenization(tmp_path):
    b = budget(tmp_path)
    history = [row("system", "persona"), row("user", "exercise please")]
    history += [row("system", "[Worker 'senior:exercise:one' failed]: failure " + str(i)) for i in range(900)]
    history += [row("system", "Actual tool result: preserve this"), row("assistant", "reply")]
    counts = []
    def count(messages):
        counts.append(len(messages))
        return sum(len(m["content"]) for m in messages)
    b.count = count
    fitted = b.fit(history)
    assert max(counts) == 5
    assert fitted[2]["content"].endswith("899")
    assert fitted[-2:] == history[-2:]
    assert len(history) == 904 and list(tmp_path.glob("*.json"))


def test_cache_prewarm_does_not_submit_oversized_or_renormalized_prefix(tmp_path):
    b = budget(tmp_path)
    calls = []
    local = SimpleNamespace(generate_background=lambda **kw: calls.append(kw) or "ok")
    b.count_normalized = lambda m: 9999 if len(m) > 2 else 100
    b.guard_background(local)
    assert local.generate_background(messages=[row("system", "normalized")] * 3,
        is_rewarm=True, rewarm_hash="oversized") == ""
    assert not calls
    assert local.generate_background(messages=[row("system", "normalized")],
        is_rewarm=True) == "ok"


@pytest.mark.asyncio
async def test_cloud_compaction_survives_cancelled_waiter_and_preserves_new_turns(tmp_path):
    release = threading.Event()
    started = threading.Event()
    def summarize(_):
        started.set()
        assert release.wait(3)
        return "remember the original question"
    b = budget(tmp_path, summarize)
    history = [row("system", "persona"), row("user", "first"), row("assistant", "reply")]
    original = copy.deepcopy(history)
    task = b.start(history)
    async def wait():
        return await asyncio.shield(task)
    waiter = asyncio.create_task(wait())
    try:
        assert await asyncio.to_thread(started.wait, 1)
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        assert not task.cancelled()
        tail = [row("user", "second"), row("assistant", "new reply")]
        history.extend(tail)
        release.set()
        await task
        assert history == original + tail  # cloud never mutates live history
        assert b.apply_ready(history)
        assert history[-2:] == tail
        assert history[0] == original[0]
        assert "original question" in history[1]["content"]
        assert list(tmp_path.glob("*.json"))
    finally:
        release.set()
        await task


def test_stale_summary_cannot_overwrite_replaced_history(tmp_path):
    b = budget(tmp_path)
    old = [row("system", "old"), row("user", "question")]
    b.ready = (old, "summary")
    history = [row("system", "new mode"), row("user", "new question")]
    before = copy.deepcopy(history)
    assert not b.apply_ready(history)
    assert history == before


def test_emergency_trim_preserves_current_turn_and_archives_removed_text(tmp_path):
    b = budget(tmp_path)
    messages = [row("system", "persona"), row("user", "old"),
                row("assistant", "x" * 2000), row("system", "old tool result"),
                row("user", "current"), row("system", "current tool result")]
    fitted = b.fit(messages)
    assert fitted == [messages[0], *messages[-2:]]
    assert len(messages) == 6
    assert "old tool result" in next(tmp_path.glob("*.json")).read_text()
    with pytest.raises(RuntimeError, match="current request"):
        b.fit([row("system", "persona"), row("user", "x" * 3000)])


def test_optional_memory_is_bounded_without_changing_persona(tmp_path):
    b = budget(tmp_path)
    prompt = b.bound_memory("PERSONA\n", "saved memory " * 300)
    assert prompt.startswith("PERSONA\n")
    assert "recall_memory" in prompt
    assert b.count([row("system", prompt)]) <= b.hard_limit - 1400


def core_with_budget(tmp_path, stream):
    core = LegacyKikiCore.__new__(LegacyKikiCore)
    core.context_budget = budget(tmp_path)
    core.history = [row("system", "persona")]
    core._maybe_inject_time = lambda: None
    core._battery_remark_due = lambda: False
    core.register_history = lambda *a, **k: None
    core.schedule_compaction = lambda: None
    core.stream_response = stream
    core.media_active = None
    core.max_followup_tool_rounds = 1
    return core


@pytest.mark.asyncio
async def test_empty_reply_retries_once_and_never_saves_empty_assistant(tmp_path):
    calls = []
    def stream(messages, **kwargs):
        calls.append(copy.deepcopy(messages))
        yield "done", ""
    core = core_with_budget(tmp_path, stream)
    events = [e async for e in core.stream_reply("Can you hear me?", threading.Event())]
    assert len(calls) == 2
    assert any(e == "sentence" and text for e, text in events)
    assert core.history[-1]["content"]


@pytest.mark.asyncio
async def test_new_turn_fits_before_generation_when_cloud_is_not_ready(tmp_path):
    seen = []
    def stream(messages, **kwargs):
        seen.append(copy.deepcopy(messages))
        yield "sentence", "Yes, I can hear you."
        yield "done", "Yes, I can hear you."
    core = core_with_budget(tmp_path, stream)
    core.history += [row("user", "old question"), row("assistant", "x" * 2000)]
    events = [e async for e in core.stream_reply("Can you hear me?", threading.Event())]
    assert core.context_budget.count(seen[0]) <= core.context_budget.hard_limit
    assert seen[0][-1] == row("user", "Can you hear me?")
    assert ("sentence", "Yes, I can hear you.") in events


@pytest.mark.asyncio
async def test_stop_does_not_trigger_empty_reply_retries(tmp_path):
    abort = threading.Event()
    calls = []
    def stream(messages, **kwargs):
        calls.append(1)
        abort.set()
        yield "done", ""
    core = core_with_budget(tmp_path, stream)
    events = [e async for e in core.stream_reply("question", abort)]
    assert calls == [1]
    assert events == []


@pytest.mark.asyncio
async def test_repeated_turns_stay_in_budget_even_with_cloud_offline(tmp_path):
    def stream(messages, **kwargs):
        assert core.context_budget.count(messages) <= core.context_budget.hard_limit
        yield "sentence", "answer " * 45
        yield "done", "answer " * 45
    core = core_with_budget(tmp_path, stream)
    for i in range(40):
        events = [e async for e in core.stream_reply(f"question {i}", threading.Event())]
        assert any(kind == "sentence" for kind, _ in events)
        assert core.history[-2] == row("user", f"question {i}")
    assert list(tmp_path.glob("*.json"))


@pytest.mark.asyncio
async def test_failed_cloud_summary_leaves_history_and_local_guard_intact(tmp_path):
    def fail(_):
        raise OSError("cloud offline")
    b = budget(tmp_path, fail)
    history = [row("system", "persona"), row("user", "old"), row("assistant", "x" * 2000)]
    before = copy.deepcopy(history)
    assert await b.start(history) is False
    assert history == before
    assert b.ready is None
    candidate = history + [row("user", "new")]
    assert b.count(b.fit(candidate)) <= b.hard_limit

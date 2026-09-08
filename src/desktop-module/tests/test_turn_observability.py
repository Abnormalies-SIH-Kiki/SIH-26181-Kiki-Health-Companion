"""The Web UI's live context feed, and the silent TypeError that killed it.

`Recorder.set_context(self, messages, **info)` was called as
`set_context(message_history, messages=len(message_history))` -- the count
collided with the positional parameter, so every turn raised TypeError into a
bare `except Exception: pass`.

The whole observability block went down with it, not just the snapshot:

* `/api/context` stayed empty, so nothing Kiki was actually fed -- the time
  anchor, the CARE NOW row, idle-mind notes, vision context -- was ever visible
  in the Web UI;
* no `turn` session was ever opened, so the end-to-end turn view was empty
  while every other kind (idle_mind, care_voice, action_agent) worked;
* `_turn_sid` stayed None, so the later ttfw and reply steps silently attached
  to nothing.

Confirmed live on 2026-09-01: ten `turn`/`reply` events recorded from a
different call site, zero `turn`/`start` events, and `/api/context` -> `{}`.
"""

import ast
import re
from pathlib import Path

import pytest

from core.observability import Recorder

MAIN = Path(__file__).resolve().parents[1] / "main.py"


@pytest.fixture
def recorder():
    return Recorder()


# --- the recorder itself ---

def test_a_snapshot_is_readable_by_the_web_ui(recorder):
    history = [{"role": "system", "content": "CARE NOW: AQI ~307 very poor"},
               {"role": "user", "content": "how is the air outside?"}]
    recorder.set_context(history, count=len(history))

    latest = recorder.get_latest_context()
    assert [m["role"] for m in latest["messages"]] == ["system", "user"]
    assert "AQI ~307" in latest["messages"][0]["content"]
    assert latest["info"] == {"count": 2}


def test_the_injections_are_what_the_feed_is_for(recorder):
    """Every system row Kiki was fed has to survive into the snapshot -- that
    is the whole reason to look at this page."""
    history = [
        {"role": "system", "content": "[TIME] 10:47 PM on Monday"},
        {"role": "system", "content": "CARE NOW: OUTSIDE New Delhi: feels 37C"},
        {"role": "system", "content": "[BACKGROUND NOTE n1 ...]"},
        {"role": "user", "content": "what's going on?"},
    ]
    recorder.set_context(history, count=len(history))
    rendered = " ".join(m["content"]
                        for m in recorder.get_latest_context()["messages"])
    for injected in ("[TIME]", "CARE NOW", "BACKGROUND NOTE"):
        assert injected in rendered


def test_the_count_may_not_be_passed_as_messages(recorder):
    """The exact bug, pinned: `messages` is the positional parameter."""
    with pytest.raises(TypeError):
        recorder.set_context([{"role": "user", "content": "hi"}], messages=1)


def test_a_turn_session_can_be_opened_and_stepped(recorder):
    sid = recorder.start_session("turn", name="speaking", last_user="hello")
    recorder.log_step(sid, "context", messages=2, content="[user]\nhello")
    recorder.end_session(sid, status="done")

    sessions = recorder.get_sessions(limit=5, kind="turn")["sessions"]
    assert [s["kind"] for s in sessions] == ["turn"]


def test_an_oversized_message_is_capped_not_dropped(recorder):
    recorder.set_context([{"role": "user", "content": "x" * 20000}], count=1)
    content = recorder.get_latest_context()["messages"][0]["content"]
    assert len(content) < 20000
    assert "more chars" in content or "chars]" in content


# --- the call site, which is where it actually broke ---

def _set_context_call():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "set_context"):
            return node
    return None


def test_main_still_snapshots_the_context():
    assert _set_context_call() is not None, "the live context feed is unwired"


def test_main_does_not_reintroduce_the_keyword_collision():
    call = _set_context_call()
    assert [kw.arg for kw in call.keywords] == ["count"]


def test_the_failure_is_no_longer_swallowed_silently():
    """A bare `except: pass` around the snapshot is what let this run for
    weeks. Whatever guards it now has to say something."""
    source = MAIN.read_text(encoding="utf-8")
    block = source[source.index("_rec.set_context("):]
    handler = block[:block.index("def stop_sfx_on_first_play")]
    assert re.search(r"except Exception as \w+:", handler)
    assert "[Observability]" in handler

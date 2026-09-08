"""The dashboard's "Speak" button, end to end.

The button is a director's cue: the operator types what Kiki should DO ("ask
Vaibhav to drink water"), and Kiki performs it out loud in character, then keeps
talking. The path is

    browser  ->  POST /api/query  ->  webui.server._query_handler
             ->  make_webui_speak_handler  ->  loop.call_soon_threadsafe
             ->  main.py's foreground event queue as ("webui_instruction", cue)
             ->  the TURN_OPENING_EVENTS branch, which injects the cue as a
                 `system` message and runs the ordinary speaking lifecycle.

Every hop below is the real code; only the asyncio loop is stood up by the test.
"""

import asyncio
import json
import threading

import pytest

import main
from webui import server as webui_server


@pytest.fixture
def live_loop():
    """A real asyncio loop on its own thread — what main() gives the handler."""
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def run():
        asyncio.set_event_loop(loop)
        loop.call_soon(ready.set)
        loop.run_forever()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    ready.wait(5)
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)
    loop.close()


@pytest.fixture
def dashboard(live_loop):
    """The real Flask app, wired to the real handler over a real queue."""
    flask = pytest.importorskip("flask")           # noqa: F841 - optional dep
    queue = asyncio.Queue()
    handler = main.make_webui_speak_handler(live_loop, queue)
    previous = webui_server._query_handler
    webui_server.set_query_handler(handler)
    app = webui_server._build_app()
    app.config["TESTING"] = True
    try:
        yield app.test_client(), queue
    finally:
        webui_server.set_query_handler(previous)


def _drain(queue, loop, timeout=5.0):
    """Read one event from the loop's queue, from the test's thread."""
    return asyncio.run_coroutine_threadsafe(
        asyncio.wait_for(queue.get(), timeout), loop).result(timeout + 1)


# --------------------------------------------------------------------------- #
#  The cue itself                                                              #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("cue", [
    "ask vaibhav to drink water",
    "ask vaibhav how is his health",
    "tell the whatsapp updates and news for the day",
])
def test_the_cue_is_carried_into_the_system_message(cue):
    instruction = main.build_directed_speech_instruction(cue)

    assert cue in instruction
    # It must be recognisable as a stage direction, not as something the person
    # in the room said — that framing is the whole feature.
    assert "DIRECTOR CUE" in instruction


def test_a_blank_cue_is_refused_rather_than_performed():
    assert main.build_directed_speech_instruction("") == ""
    assert main.build_directed_speech_instruction("   \n\t ") == ""
    assert main.build_directed_speech_instruction(None) == ""


def test_the_cue_tells_kiki_not_to_read_the_instruction_out():
    """The two observed failure modes for prompts of this shape.

    Gemma recites matching EXAMPLE/INSTRUCTION text verbatim, and models love
    prefacing a directed line with "you asked me to tell you...". Both break the
    roleplay outright, so both are named explicitly in the cue.
    """
    instruction = main.build_directed_speech_instruction("ask him to drink water")

    lowered = instruction.lower()
    assert "do not read it out" in lowered
    assert "do not quote it" in lowered
    assert "as if you thought of it yourself" in lowered


# --------------------------------------------------------------------------- #
#  HTTP -> foreground queue                                                    #
# --------------------------------------------------------------------------- #

def test_pressing_speak_queues_one_foreground_turn(dashboard, live_loop):
    client, queue = dashboard

    response = client.post("/api/query",
                           json={"query": "ask vaibhav to drink water"})

    assert response.status_code == 202
    assert response.get_json()["accepted"] is True
    assert _drain(queue, live_loop) == (
        "webui_instruction", "ask vaibhav to drink water")


def test_the_queued_event_is_one_the_turn_loop_actually_handles(dashboard,
                                                                live_loop):
    """The link that silently does nothing when it breaks.

    An event name the `elif event in TURN_OPENING_EVENTS` branch does not list
    is dropped by the loop without a word: the button would report success and
    Kiki would stay silent forever.
    """
    client, queue = dashboard

    client.post("/api/query", json={"query": "tell me the news"})
    event, _cue = _drain(queue, live_loop)

    assert event in main.TURN_OPENING_EVENTS


def test_the_cue_is_normalised_before_it_reaches_the_turn(dashboard, live_loop):
    client, queue = dashboard

    client.post("/api/query",
                json={"query": "  ask   vaibhav\n how is  his health  "})

    assert _drain(queue, live_loop) == (
        "webui_instruction", "ask vaibhav how is his health")


@pytest.mark.parametrize("payload", [{"query": ""}, {"query": "   "}, {}])
def test_an_empty_box_never_starts_a_turn(dashboard, live_loop, payload):
    client, queue = dashboard

    response = client.post("/api/query", json=payload)

    assert response.status_code == 400
    assert response.get_json()["accepted"] is False
    assert queue.qsize() == 0


def test_an_over_long_cue_is_refused(dashboard):
    client, _queue = dashboard

    response = client.post("/api/query", json={"query": "a" * 1001})

    assert response.status_code == 400
    assert response.get_json()["accepted"] is False


def test_the_button_reports_failure_when_no_kiki_is_attached():
    """start_webui() without a query_handler — the dashboard alone, no robot."""
    pytest.importorskip("flask")
    previous = webui_server._query_handler
    webui_server.set_query_handler(None)
    try:
        app = webui_server._build_app()
        app.config["TESTING"] = True
        response = app.test_client().post("/api/query", json={"query": "hi"})
    finally:
        webui_server.set_query_handler(previous)

    assert response.status_code == 503
    assert response.get_json()["accepted"] is False


def test_a_dead_loop_is_reported_instead_of_swallowed():
    """Kiki shutting down must surface in the UI, not look like success."""
    loop = asyncio.new_event_loop()
    queue = asyncio.Queue()
    handler = main.make_webui_speak_handler(loop, queue)
    loop.close()

    result = handler("ask vaibhav to drink water")

    assert result["accepted"] is False
    assert result["error"]


# --------------------------------------------------------------------------- #
#  Surviving the way to the turn                                               #
# --------------------------------------------------------------------------- #

def test_a_cue_queued_mid_turn_survives_the_post_tts_drain():
    """Press Speak while Kiki is already talking.

    The end-of-turn drain deletes late microphone events. A cue queued during
    the previous turn is not a microphone event, and deleting it would lose the
    instruction with the UI still showing "speaking ✓".
    """
    queue = asyncio.Queue()
    queue.put_nowait(("interim", "late echo"))
    queue.put_nowait(("webui_instruction", "ask vaibhav to drink water"))
    queue.put_nowait(("final", "stale"))

    assert main.drain_stale_input_events(queue) == 1
    assert queue.get_nowait() == (
        "webui_instruction", "ask vaibhav to drink water")
    assert queue.empty()


def test_the_turn_injects_the_cue_as_system_and_invents_no_user_turn():
    """A fake `user` message would be a lie the KV cache carries forever.

    Kiki would "remember" that the person in the room asked for this, and the
    summarizer would write it into long-term memory as their request.
    """
    history = [
        {"role": "system", "content": "You are Kiki."},
        {"role": "user", "content": "what is the time"},
        {"role": "assistant", "content": "It is nine."},
    ]
    users_before = [m for m in history if m["role"] == "user"]

    injected = main.open_directed_speech_turn(
        history, "ask vaibhav to drink water")

    assert history[-1] == {"role": "system", "content": injected}
    assert "ask vaibhav to drink water" in injected
    assert [m for m in history if m["role"] == "user"] == users_before


def test_a_blank_cue_leaves_the_history_untouched():
    history = [{"role": "system", "content": "You are Kiki."}]

    assert main.open_directed_speech_turn(history, "  ") == ""
    assert history == [{"role": "system", "content": "You are Kiki."}]


def test_main_actually_hands_the_handler_to_the_dashboard():
    """The link that made the button dead on arrival.

    `webui/server.py` shipped the Speak bar and the /api/query route, but
    `main()` called `start_webui()` without a `query_handler` -- so the route
    answered "No Kiki device is connected" forever. Nothing else in this file
    can catch that, because the call sits inside `main()`'s 1500-line body,
    behind the microphone and the Hailo pipeline. A source check is crude, but
    it is the only thing standing between a working button and a silent one.
    """
    import inspect

    body = inspect.getsource(main.main)

    assert "query_handler=make_webui_speak_handler(loop, stt_queue)" in body


def test_the_speak_bar_is_actually_rendered_on_the_page():
    """The button and the input the JS binds to must exist in the served HTML."""
    page = webui_server._PAGE

    assert 'id="askform"' in page
    assert 'id="asktext"' in page
    assert 'id="askbutton"' in page
    assert "'/api/query'" in page

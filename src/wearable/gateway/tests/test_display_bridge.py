import asyncio
import threading

import pytest

from kiki_gateway.display_bridge import DisplayBridge


class Session:
    def __init__(self):
        self.events = []

    async def send_event(self, kind, **fields):
        self.events.append((kind, fields))


class FakeLCD:
    """Stands in for the legacy lcd_manager's observer contract."""

    def __init__(self):
        self._commit_observer = None

    def set_commit_observer(self, callback):
        self._commit_observer = callback

    def commit(self, line1, line2):
        self._commit_observer({"line1": line1, "line2": line2, "stream_id": None})


async def drain():
    # Let the coroutines scheduled from the worker thread actually run.
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_lcd_rows_are_forwarded_and_truncated_to_the_panel_width():
    session = Session()
    bridge = DisplayBridge(session)
    lcd = FakeLCD()
    bridge._lcd = lcd
    lcd.set_commit_observer(bridge._on_lcd_commit)

    lcd.commit("Listening...", "say something at all")
    await drain()

    assert session.events == [
        ("lcd", {"line1": "Listening...", "line2": "say something at"})
    ]


@pytest.mark.asyncio
async def test_an_unchanged_commit_is_not_resent():
    session = Session()
    bridge = DisplayBridge(session)
    lcd = FakeLCD()
    bridge._lcd = lcd
    lcd.set_commit_observer(bridge._on_lcd_commit)

    lcd.commit("Thinking", "")
    lcd.commit("Thinking", "")
    lcd.commit("Speaking", "")
    await drain()

    assert [fields["line1"] for _, fields in session.events] == ["Thinking", "Speaking"]


@pytest.mark.asyncio
async def test_only_background_states_are_forwarded():
    """The session reports turn states earlier and more precisely; the bridge
    must not race it for the face mid-sentence."""
    session = Session()
    bridge = DisplayBridge(session)

    bridge._on_oled_state("speaking", "")
    bridge._on_oled_state("listening", "")
    bridge._on_oled_state("thinking", "")
    await drain()
    assert session.events == []

    bridge._on_oled_state("summarizing", "saving memory")
    bridge._on_oled_state("music", "some song")
    await drain()
    assert [fields["state"] for _, fields in session.events] == ["summarizing", "music"]


@pytest.mark.asyncio
async def test_commits_from_a_legacy_worker_thread_reach_the_loop():
    session = Session()
    bridge = DisplayBridge(session)
    lcd = FakeLCD()
    bridge._lcd = lcd
    lcd.set_commit_observer(bridge._on_lcd_commit)

    # The real observer is invoked from lcd_display's own worker thread, never
    # from the event loop, which is the whole reason _send goes through
    # run_coroutine_threadsafe.
    thread = threading.Thread(target=lcd.commit, args=("Warming up", "Please wait"))
    thread.start()
    thread.join()
    await drain()

    assert session.events == [("lcd", {"line1": "Warming up", "line2": "Please wait"})]


@pytest.mark.asyncio
async def test_detach_restores_the_previous_observer():
    session = Session()
    bridge = DisplayBridge(session)
    lcd = FakeLCD()
    seen = []
    lcd.set_commit_observer(seen.append)

    bridge._lcd = lcd
    bridge._previous_observer = lcd._commit_observer
    lcd.set_commit_observer(bridge._on_lcd_commit)
    bridge.detach()

    lcd.commit("after", "detach")
    await drain()
    assert session.events == []
    assert seen and seen[0]["line1"] == "after"


class FakeLegacyLCD(FakeLCD):
    """Also records the update_status/display_stream calls the Pi's main.py makes."""

    def __init__(self):
        super().__init__()
        self.status_calls = []
        self.stream_calls = []

    def update_status(self, action, details=None):
        self.status_calls.append((action, details))

    def display_stream(self, prefix, text):  # never called: see test below
        self.stream_calls.append((prefix, text))


@pytest.mark.asyncio
async def test_states_drive_the_legacy_lcd_so_the_rows_actually_change():
    """The tap alone was silent: nothing in the gateway ever called
    update_status, because on the Pi that is main.py's job."""
    bridge = DisplayBridge(Session())
    lcd = FakeLegacyLCD()
    bridge._lcd = lcd

    bridge.status_for_state("warming")
    bridge.status_for_state("listening")
    bridge.status_for_state("thinking")
    bridge.status_for_state("idle")

    assert [call[0] for call in lcd.status_calls] == [
        "Warming up model", "Listening...", "Thinking", "Kiki is Idle",
    ]


@pytest.mark.asyncio
async def test_thinking_passes_no_detail_so_the_legacy_spinner_runs():
    bridge = DisplayBridge(Session())
    lcd = FakeLegacyLCD()
    bridge._lcd = lcd
    bridge.status_for_state("thinking")
    # lcd_display only starts its spinner for a bare "thinking".
    assert lcd.status_calls == [("Thinking", None)]


@pytest.mark.asyncio
async def test_an_unknown_state_touches_nothing():
    bridge = DisplayBridge(Session())
    lcd = FakeLegacyLCD()
    bridge._lcd = lcd
    bridge.status_for_state("music")
    assert lcd.status_calls == []


@pytest.mark.asyncio
async def test_status_falls_back_to_a_direct_row_without_the_legacy_lcd():
    session = Session()
    bridge = DisplayBridge(session)
    bridge._lcd = None
    bridge.status_for_state("listening")
    await drain()
    assert session.events == [("lcd", {"line1": "Listening...", "line2": ""})]


@pytest.mark.asyncio
async def test_speaking_leaves_the_rows_to_the_streaming_text():
    """lcd_display.update_status ignores "speaking" for the same reason: the
    state arrives on first PCM, after the sentence is already on screen."""
    bridge = DisplayBridge(Session())
    lcd = FakeLegacyLCD()
    bridge._lcd = lcd

    bridge.status_for_state("speaking")

    assert lcd.status_calls == []


@pytest.mark.asyncio
async def test_the_bridge_never_writes_speech_rows():
    """The board renders the whole sentence itself, timed to the audio. A 16x2
    commit here would arrive right after the caption and blank it, because any
    lcd row hands the caption band back to the status labels."""
    bridge = DisplayBridge(Session())
    lcd = FakeLegacyLCD()
    bridge._lcd = lcd

    bridge.status_for_state("thinking")
    bridge.status_for_state("speaking")

    assert lcd.stream_calls == []
    assert not hasattr(bridge, "stream")


def test_devanagari_becomes_hinglish_for_the_panel(monkeypatch):
    """The panel font has no Devanagari glyphs, so untransliterated Hindi shows
    as empty boxes. The Pi does the same for its HD44780.

    The romanizer itself is the Pi's, so this checks it is reached and applied
    rather than re-testing its transliteration table.
    """
    import sys, types

    from kiki_gateway import display_bridge

    module = types.ModuleType("core.tts_sync")
    module.romanize_hindi_for_lcd = lambda value: value.replace("नमस्ते", "namaste")
    core = sys.modules.setdefault("core", types.ModuleType("core"))
    monkeypatch.setitem(sys.modules, "core", core)
    monkeypatch.setitem(sys.modules, "core.tts_sync", module)
    monkeypatch.setattr(display_bridge, "_romanizer", None)

    assert display_bridge.panel_text("नमस्ते vaibhav") == "namaste vaibhav"


def test_the_panel_still_shows_something_if_the_romanizer_is_missing(monkeypatch):
    """A failed optional import must not blank the caption."""
    import sys

    from kiki_gateway import display_bridge

    monkeypatch.setattr(display_bridge, "_romanizer", None)
    monkeypatch.setitem(sys.modules, "core.tts_sync", None)
    assert display_bridge.panel_text("[sigh] hello") == "Sigh... hello"


def test_plain_english_is_untouched():
    from kiki_gateway.display_bridge import panel_text

    assert panel_text("just a normal sentence") == "just a normal sentence"


def test_voice_tags_become_captions_instead_of_raw_brackets():
    from kiki_gateway.display_bridge import panel_text

    assert panel_text("[laughter] that's funny") == "Ha ha ha! that's funny"
    # Unsupported tags the model invents are dropped, not printed.
    assert panel_text("[deadpan] well then") == "well then"


def test_conversion_is_display_only():
    """A regression guard: if this ever ran on the TTS path, Kiki would stop
    speaking Hindi and stop sighing."""
    import inspect
    from kiki_gateway import session

    source = inspect.getsource(session.DeviceSession._tts_worker)
    assert "panel_text" not in source


def test_your_speech_is_romanized_for_the_panel_too():
    """The caption converts Kiki's Hindi; the transcript of what *you* said has
    to go through the same conversion or it renders as boxes on the panel."""
    import ast
    import inspect

    from kiki_gateway import session

    tree = ast.parse(inspect.getsource(session))
    romanized = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = getattr(node.func, "attr", None)
        if target != "send_event" or not node.args:
            continue
        name = getattr(node.args[0], "value", None)
        text = next((k.value for k in node.keywords if k.arg == "text"), None)
        if isinstance(text, ast.Call) and getattr(text.func, "id", "") == "panel_text":
            romanized.add(name)

    assert {"transcript_partial", "transcript_final", "ambient", "speech"} <= romanized


class MusicSession(Session):
    """A session whose bridge reports a song loaded (playing or paused)."""

    def __init__(self, media_active: bool):
        super().__init__()
        self.device = type("Bridge", (), {"media_loaded": media_active})()


@pytest.mark.asyncio
async def test_background_states_do_not_steal_the_panel_from_a_playing_song():
    # The board picks its one button's meaning from the state name, so letting
    # the idle mind waking up overwrite `music` takes away Stop for the thing
    # currently making noise.
    session = MusicSession(media_active=True)
    bridge = DisplayBridge(session)
    bridge._on_oled_state("idle_mind")
    await drain()
    assert session.events == []

    bridge._on_oled_state("music", "Nice Music")
    await drain()
    assert session.events == [
        ("state", {"state": "music", "detail": "Nice Music", "source": "legacy"})
    ]


@pytest.mark.asyncio
async def test_background_states_still_arrive_when_nothing_is_playing():
    session = MusicSession(media_active=False)
    bridge = DisplayBridge(session)
    bridge._on_oled_state("idle_mind")
    await drain()
    assert session.events == [
        ("state", {"state": "idle_mind", "detail": "", "source": "legacy"})
    ]

from __future__ import annotations

import asyncio
import logging
import re
from typing import Callable


LOG = logging.getLogger(__name__)


_BRACKET_TAG = re.compile(r"\[([^\[\]]{1,40})\]")

# Spoken-word captions for the voice model's non-verbal tags, mirroring
# tts_sync.TAG_DISPLAY_TEXT. Held locally rather than imported because the
# gateway already owns the supported-tag set, and because raw "[sigh]" on screen
# is a visible defect that should not depend on an optional import succeeding.
TAG_CAPTIONS = {
    "laughter": "Ha ha ha!",
    "sigh": "Sigh...",
    "confirmation-en": "Mm-hmm.",
    "question-en": "Hmm?",
    "question-ah": "Ah?",
    "question-oh": "Oh?",
    "question-ei": "Eh?",
    "question-yi": "Yeah?",
    "surprise-ah": "Ahh!",
    "surprise-oh": "Oh!",
    "surprise-wa": "Wow!",
    "surprise-yo": "Yo!",
    "dissatisfaction-hnn": "Hnn...",
}

_romanizer = None


def _romanize(text: str) -> str:
    """The Pi's own Devanagari romanizer, imported once.

    It lives in core/tts_sync.py, which is deliberately dependency-free -- no
    model, no network, no transliteration package -- so it imports cleanly here
    and stays the single source of truth rather than being duplicated.
    """
    global _romanizer
    if _romanizer is None:
        try:
            from core.tts_sync import romanize_hindi_for_lcd

            _romanizer = romanize_hindi_for_lcd
        except Exception:
            LOG.info("legacy romanizer unavailable; Devanagari will not be converted")
            _romanizer = lambda value: value
    return _romanizer(text)


def panel_text(text: str) -> str:
    """What the panel should show for a sentence Kiki is speaking.

    Two conversions, both display-only -- the text sent to TTS keeps its
    Devanagari and its tags, or Kiki would stop speaking Hindi and stop sighing:

    * Voice tags become captions ("[laughter]" -> "Ha ha ha!") and anything the
      model invented is dropped, so the panel stops showing raw brackets.
    * Devanagari becomes ASCII Hinglish. The Pi does this for its HD44780; here
      the reason differs but the need is identical -- the panel's font has no
      Devanagari glyphs, so Hindi would render as empty boxes.
    """
    if not text:
        return ""

    def render_tag(match: re.Match) -> str:
        return TAG_CAPTIONS.get(match.group(1).strip().lower(), "")

    cleaned = re.sub(r"\s+", " ", _BRACKET_TAG.sub(render_tag, text)).strip()
    return _romanize(cleaned)


class DisplayBridge:
    """Mirrors the legacy runtime's two displays onto the board's AMOLED.

    KikiFast drives a 16x2 character LCD and a 128x64 OLED face. Neither exists
    on the laptop, but both subsystems degrade gracefully rather than switching
    off: `lcd_display` still runs its worker thread and still calls the commit
    observer for every row it would have written, and `oled_display.set_state`
    is just a guarded flag flip. So instead of re-deriving display state from
    the gateway's own view of a turn -- which would drift from the Pi the moment
    anyone touched either side -- this taps the real ones.

    That matters most for the calls the gateway cannot see at all: a worker
    finishing, a summary being written, the idle mind waking up, music
    metadata. Those never pass through `DeviceSession`, and without this bridge
    the face would sit on `idle` through all of them.
    """

    # The session owns the states of a *turn* -- it is the thing that knows when
    # STT committed and when the first PCM went out, and it reports them earlier
    # and more precisely than the legacy status strings do. The bridge therefore
    # forwards only background states, so the two sources cannot fight over the
    # face mid-sentence.
    BACKGROUND_STATES = frozenset({
        "music", "summarizing", "idle_mind", "workers", "vision",
        "warming", "goodbye", "boot", "disconnected", "sleeping",
    })

    def __init__(self, session):
        self.session = session
        self.loop = asyncio.get_running_loop()
        self._lcd = None
        self._oled = None
        self._previous_observer: Callable | None = None
        self._original_set_state: Callable | None = None
        self._wrapped_set_state: Callable | None = None
        self._last_lcd: tuple[str, str] | None = None

    def attach(self) -> bool:
        """Install the taps. Returns False when the legacy modules are absent."""
        try:
            from core.lcd_display import lcd_manager
        except Exception:
            LOG.info("legacy LCD unavailable; the board will show gateway state only")
            return False
        self._lcd = lcd_manager
        self._previous_observer = getattr(lcd_manager, "_commit_observer", None)
        lcd_manager.set_commit_observer(self._on_lcd_commit)

        try:
            from core.oled_display import oled_manager
        except Exception:
            LOG.info("legacy OLED unavailable; background states will not reach the face")
            return True
        self._oled = oled_manager
        self._original_set_state = oled_manager.set_state

        def wrapped(state: str, detail: str = "", _original=oled_manager.set_state):
            _original(state, detail)
            self._on_oled_state(state, detail)

        self._wrapped_set_state = wrapped
        oled_manager.set_state = wrapped
        LOG.info("display bridge attached to the legacy LCD and OLED")
        return True

    def detach(self) -> None:
        """Remove our taps -- but only if they are still the ones installed.

        A board reconnects before the old socket has finished closing, so two
        bridges overlap for a moment and the departing one used to restore the
        state it captured on the way in: that unhooks the ARRIVING session and
        leaves the panel showing nothing at all. Now that the runtime is shared
        across reconnects this happens on every dropped link, so each side only
        unwinds a tap it can still see is its own.
        """
        if self._lcd is not None:
            try:
                # `==`, not `is`: self._on_lcd_commit builds a fresh bound
                # method object on every attribute access, so an identity test
                # can never match the one that was installed. Bound methods do
                # compare equal when they wrap the same function and instance.
                if getattr(self._lcd, "_commit_observer", None) == self._on_lcd_commit:
                    self._lcd.set_commit_observer(self._previous_observer)
            except Exception:
                LOG.debug("could not restore the LCD observer", exc_info=True)
        if (self._oled is not None and self._original_set_state is not None
                and getattr(self._oled, "set_state", None) is self._wrapped_set_state):
            self._oled.set_state = self._original_set_state
        self._lcd = None
        self._oled = None
        self._wrapped_set_state = None

    # main.py is what calls update_status on the Pi, and it is not running here,
    # so the tap above was live but silent: the board sat on whatever row it had
    # last been given. These two drive the real lcd_display so its own mapping
    # -- including the spinner verbs while thinking -- produces the rows, and the
    # commit observer forwards them like any other.
    STATE_STATUS = {
        "warming": ("Warming up model", "Please wait..."),
        # The idle hint is filled in from the ACTIVE mode's hotword below: in
        # rohan mode the panel telling you to say "kiki" is simply wrong.
        "idle": ("Kiki is Idle", ""),
        "followup": ("Listening...", ""),
        "listening": ("Listening...", ""),
        "thinking": ("Thinking", ""),
        "tool": ("Working", ""),
        # `speaking` is deliberately absent, matching lcd_display.update_status:
        # the streaming sentence updates own the screen while Kiki talks. Giving
        # it a row here would overwrite the first spoken sentence with the word
        # "Speaking", because the state arrives on first PCM, after the sentence
        # has already been displayed.
    }

    def status_for_state(self, state: str, detail: str = "") -> None:
        row = self.STATE_STATUS.get(state)
        if row is None:
            return
        action, default_detail = row
        if state == "idle" and not detail and not default_detail:
            default_detail = self._wake_hint()
        self.status(action, detail or default_detail)

    def _wake_hint(self) -> str:
        """"Say 'rohan'" -- whatever this mode actually answers to."""
        try:
            matcher = self.session._hotword_matcher()
            words = matcher.hotwords if matcher is not None else ()
        except Exception:
            words = ()
        return f"Say '{words[0]}'" if words else "Hold to talk"

    def status(self, action: str, details: str = "") -> None:
        if self._lcd is not None:
            try:
                # "Thinking" with no detail starts lcd_display's own spinner.
                self._lcd.update_status(action, details or None)
                return
            except Exception:
                LOG.debug("legacy update_status failed", exc_info=True)
        self._send("lcd", line1=action[:16], line2=(details or "")[:16])

    # Both callbacks below run on legacy worker threads, never on the gateway's
    # event loop, so every send has to be handed across explicitly.
    def _on_lcd_commit(self, commit: dict) -> None:
        line1 = str(commit.get("line1", ""))[:16]
        line2 = str(commit.get("line2", ""))[:16]
        if (line1, line2) == self._last_lcd:
            return
        self._last_lcd = (line1, line2)
        self._send("lcd", line1=line1, line2=line2)

    def _on_oled_state(self, state: str, detail: str = "") -> None:
        name = (state or "").strip().lower()
        if name not in self.BACKGROUND_STATES:
            return
        # Music outranks the rest of this set while it is actually playing. The
        # board decides from the state name whether its one button says "Stop"
        # or "Hold to talk", so letting the idle mind waking up overwrite
        # `music` quietly takes away the control for the thing making noise.
        device = getattr(self.session, "device", None)
        if name != "music" and device is not None and device.media_loaded:
            return
        self._send("state", state=name, detail=(detail or "")[:48], source="legacy")

    def _send(self, event_type: str, **fields) -> None:
        try:
            asyncio.run_coroutine_threadsafe(
                self.session.send_event(event_type, **fields), self.loop
            )
        except RuntimeError:
            # The loop is shutting down; a display update is never worth raising.
            pass
        except Exception:
            LOG.debug("could not forward %s to the device", event_type, exc_info=True)

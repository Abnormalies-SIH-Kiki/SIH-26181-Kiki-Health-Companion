from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import random
import re
import sys
import time
import numpy as np
import requests

from . import dance as choreography
from .audio import Decimator48To16
from .beatgrid import BeatGrid, analyse_url
from .inference import amplify_pcm_s16
from .protocol import AudioFrame, BinaryKind


LOG = logging.getLogger(__name__)


NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60,
}


class DeviceToolBridge:
    """Device-side effects for tools whose old implementation targeted Pi hardware."""

    # How long _play_query may spend searching, correcting and retrying.
    # Must stay under DeviceSession._execute_device_tool_sync's 30 s bound,
    # or main.py reports a timeout while the search is still going.
    resolve_deadline = 24.0
    # Search results kept as the next/previous queue until the mix loads.
    queue_size = 8
    # Tracks pulled from YouTube's own autoplay mix for the playing song.
    radio_size = 20
    # Which YouTube player client yt-dlp should ask as.
    #
    # This is not a preference. yt-dlp's default path now needs a JavaScript
    # runtime (deno) to solve YouTube's player challenge; without one it falls
    # back to the `android vr` client, whose URLs googlevideo answers with
    # **403 Forbidden** -- so every song stopped playing on 2026-08-15 and the
    # log filled with "skipping unplayable result". Measured on the laptop on
    # 2026-08-18: `ios`, `tv`, `web_safari`, `web_embedded` and `mweb` all fail
    # the same way; `android` works, needs no runtime, and its URLs are
    # readable by ffmpeg directly -- which is the part that matters, because
    # playback and the dance's beat analysis both hand the URL straight to it.
    YTDLP_CLIENT = ("--extractor-args", "youtube:player_client=android")
    # The whole dance preparation budget: agent + search + beat analysis. Sits
    # under _execute_device_tool_sync's 30 s so this reports its own failure
    # instead of being abandoned mid-flight.
    dance_deadline = 26.0

    # Songs to fall back on when neither the user nor the agent named one.
    # Kept short, upbeat and evergreen; the search is fuzzy enough that any of
    # them resolves.
    DANCE_FALLBACK_QUERIES = (
        "Dua Lipa - Levitating",
        "Pharrell Williams - Happy",
        "Daft Punk - One More Time",
        "Bruno Mars - Uptown Funk",
        "ABBA - Dancing Queen",
    )
    # Said before the music, never over it (see config.dance_intro_line).
    DANCE_INTRO_LINES = (
        "Okay, give me one second -- I am picking something good.",
        "Ooh, dancing! Hold on, let me find the right song.",
        "Say no more. Choosing a track...",
        "One moment -- warming up my claws.",
    )

    HANDLED = {
        "adjust_volume",
        "play_music",
        "like_current_song",
        "play_liked_songs",
        "play_last_song",
        "control_music",
        "set_timer",
        "switch_voice",
        "dance",
    }

    def __init__(self, session, legacy_root: str):
        self.session = session
        self.root = Path(legacy_root)
        self.library = self.root / "liked_songs.json"
        self.current: dict | None = None
        self.liked: list[dict] = []
        self.history: list[dict] = []
        self.queue: list[dict] = []
        self.index = -1
        self.player: asyncio.subprocess.Process | None = None
        self.player_task: asyncio.Task | None = None
        self.volume = 70
        # None means "follow the active assistant mode's voice".  An empty
        # string is distinct: it is an explicit switch_voice(default), which
        # asks the TTS server for its default voice.  Treating both as "" made
        # this always-present bridge silently override every mode voice.
        self.voice: str | None = None
        self.media_stream_id = 1000
        self.media_sequence = 0
        self.repeat_queue = False
        # Set means "the pump may send". Two independent things close it -- a
        # conversation ducking the song, and the user pressing pause -- and
        # neither may resume out from under the other, so they are tracked as
        # reasons rather than one flag. SIGSTOP on ffmpeg was the obvious way
        # to pause and is the wrong one: the pacing clock keeps running, so
        # resuming dumps the whole pause into the board's 128 KiB ring at
        # socket speed. Pause and duck share this gate for that reason.
        self.media_gate = asyncio.Event()
        self.media_gate.set()
        self._holds: set[str] = set()
        # Latched by duck(), consumed by the pump. A flag rather than "did the
        # pump see the gate shut": between two sends the pump is asleep for a
        # whole pacing interval, so a short duck can open and close again
        # without it ever observing the closed gate -- and it would then carry
        # on under a stream id the board has already blacklisted, which is
        # silence for the rest of the song.
        self._media_restart = False
        # Music narrowed to 16 kHz for a remote link. Stateful filters, so they
        # belong to the stream and are reset with it: carrying a decimator
        # across songs smears the end of one into the start of the next.
        self._media_decimator = Decimator48To16()
        self._media_tail = np.zeros(0, dtype=np.float32)
        self._radio_task: asyncio.Task | None = None
        self._load_library()

    def _load_library(self) -> None:
        try:
            data = json.loads(self.library.read_text())
            self.liked = list(data.get("liked_songs", []))
            self.history = list(data.get("history", []))[-100:]
        except Exception:
            self.liked, self.history = [], []

    def _save_library(self) -> None:
        self.library.write_text(
            json.dumps({"liked_songs": self.liked, "history": self.history[-100:]}, indent=2)
            + "\n"
        )

    async def execute(self, name: str, arguments: dict) -> str | None:
        if name not in self.HANDLED:
            return None
        if name == "adjust_volume":
            return await self._volume(arguments)
        if name == "play_music":
            return await self._play_query(str(arguments.get("song", "")))
        if name == "like_current_song":
            return self._like_current()
        if name == "play_liked_songs":
            return await self._play_entries(self.liked, 0)
        if name == "play_last_song":
            return await self._play_entries(self.history[-1:] if self.history else [], 0)
        if name == "control_music":
            return await self._control(str(arguments.get("action", "")))
        if name == "set_timer":
            return await self._set_timer(arguments.get("duration"))
        if name == "switch_voice":
            return await self._switch_voice(str(arguments.get("voice", "")))
        if name == "dance":
            return await self._dance(arguments)
        return None

    async def _volume(self, arguments: dict) -> str:
        action = str(arguments.get("action", "")).lower()
        amount = arguments.get("amount")
        if action == "set" and amount is not None:
            target = int(amount)
        elif action in {"increase", "up", "raise"}:
            target = self.volume + int(amount or 10)
        elif action in {"decrease", "down", "lower"}:
            target = self.volume - int(amount or 10)
        else:
            return "Volume action must be increase, decrease, or set."
        self.volume = max(0, min(100, target))
        await self.session.send_event("volume", percent=self.volume)
        return f"Volume set to {self.volume} percent."

    async def _switch_voice(self, requested: str) -> str:
        requested = requested.strip().lower()
        if requested in {"", "default", "reset"}:
            self.voice = ""
            return "Voice reset to the default."

        def fetch() -> list[str]:
            response = requests.get("http://127.0.0.1:8082/v1/voices", timeout=(2, 5))
            response.raise_for_status()
            return [item.get("name", "") for item in response.json().get("voices", [])]

        try:
            available = await asyncio.to_thread(fetch)
        except Exception as exc:
            return f"Could not reach the voice server: {exc}"
        match = next(
            (name for name in available if requested == name or requested in name or name in requested),
            None,
        )
        if not match:
            return f"Unknown voice '{requested}'. Available voices: {', '.join(available)}."
        self.voice = match
        return f"Voice switched to '{match}'."

    async def _resolve(self, query: str) -> tuple[dict | None, str]:
        target = query if query.startswith(("http://", "https://")) else f"ytsearch1:{query}"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "yt_dlp",
            "--dump-single-json",
            "--no-playlist",
            *self.YTDLP_CLIENT,
            "-f",
            "ba/b",
            target,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            # Was 25 s, which is the whole retry budget spent on one candidate.
            # This resolves a single known video; it is quick or it is broken.
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return None, "YouTube lookup timed out."
        if process.returncode:
            return None, (stderr.decode(errors="replace").splitlines() or ["lookup failed"])[-1]
        try:
            data = json.loads(stdout)
        except (ValueError, TypeError):
            return None, "YouTube returned nothing usable."
        if isinstance(data.get("entries"), list):
            data = next((item for item in data["entries"] if isinstance(item, dict)), {})
        stream_url = data.get("url") or ((data.get("requested_downloads") or [{}])[0].get("url"))
        webpage_url = data.get("webpage_url") or data.get("original_url")
        if not stream_url or not webpage_url:
            return None, "YouTube returned no playable audio."
        try:
            # Dance mode needs it to know how long the routine has to last;
            # everything else ignores it.
            duration = float(data.get("duration") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        return {
            "title": str(data.get("title") or query),
            "webpage_url": webpage_url,
            "video_id": str(data.get("id") or ""),
            "duration": duration,
            "stream_url": stream_url,
        }, ""

    async def _search(self, query: str, limit: int) -> list[dict]:
        """Flat YouTube search: titles and page URLs, no stream extraction.

        Flat is the whole point. Extracting playable URLs for N results costs
        N times as long, and we only ever need the one we are about to play --
        `_play_entries` resolves each entry lazily. That makes both a real
        next/previous queue and several retry attempts affordable inside one
        tool call.
        """
        return await self._search_url(f"ytsearch{limit}:{query}", limit, query)

    async def _search_url(self, target: str, limit: int, label: str = "") -> list[dict]:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "yt_dlp",
            "--dump-single-json", "--flat-playlist",
            *self.YTDLP_CLIENT,
            "--playlist-end", str(limit), target,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=12)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return []
        if process.returncode:
            return []
        try:
            data = json.loads(stdout)
        except (ValueError, TypeError):
            return []
        entries = []
        for item in data.get("entries") or []:
            if not isinstance(item, dict):
                continue
            page = item.get("url") or item.get("webpage_url")
            if not page or not str(page).startswith("http"):
                continue
            entries.append({
                "title": str(item.get("title") or label or "Unknown"),
                "webpage_url": str(page),
                "video_id": str(item.get("id") or ""),
            })
        return entries

    async def _llm_song_queries(self, spoken: str) -> list[str]:
        """Ask a cloud model what the user actually meant, as search queries.

        Speech recognition mangles song and artist names it has never seen --
        "farq hai by suzonn" for the track "Farq" by Suzonn -- and YouTube's
        search is unforgiving enough that the mangled form returns nothing at
        all. A model that has heard of the song can spell it; this is the
        cheapest thing that turns a failed request into a playing one.
        """
        prompt = (
            "A user asked a voice assistant to play a song. The request came "
            "through speech recognition and the artist or title may be "
            "misspelled. Give the most likely real song as a YouTube search "
            'query, plus up to two alternatives.\n\nRequest: "'
            + spoken
            + '"\n\nReply with ONLY a JSON object:\n'
            '{"query": "Artist - Title", "alternates": ["...", "..."]}'
        )
        try:
            from core.brain.fast_cloud import complete

            raw = await asyncio.to_thread(complete, prompt)
            data = json.loads(raw)
        except Exception as exc:
            LOG.info("song-name correction unavailable: %s", exc)
            return []
        out = []
        for value in [data.get("query")] + list(data.get("alternates") or []):
            text = str(value or "").strip()
            if text and text.lower() != spoken.strip().lower():
                out.append(text)
        return out[:3]

    async def _play_query(self, query: str) -> str:
        """Find something that actually plays, then play it.

        The old version ran one `ytsearch1` and gave up. When it found nothing
        the turn produced no reply and no song, which is how "play farq hai by
        suzonn" turned into silence.
        """
        query = query.strip()
        if not query:
            return "Please say which song to play."
        deadline = time.monotonic() + self.resolve_deadline

        attempts = [query]
        # Spoken filler that helps a human and hurts a search engine.
        stripped = re.sub(
            r"\b(play|the song|song|track|please|for me)\b", " ", query, flags=re.I
        )
        stripped = re.sub(r"\s+", " ", stripped).strip()
        if stripped and stripped.lower() != query.lower():
            attempts.append(stripped)

        tried: list[str] = []
        asked_model = False
        while attempts and time.monotonic() < deadline:
            attempt = attempts.pop(0)
            tried.append(attempt)
            entries = await self._search(attempt, self.queue_size)
            if entries:
                result = await self._play_entries(entries, 0, deadline=deadline)
                if self.media_loaded:
                    return result
                # Found results but none of them would play (age-gated,
                # region-blocked, dead extractor). Keep looking.
            if not attempts and not asked_model and time.monotonic() < deadline:
                # Only now, so a query that works costs nothing extra.
                asked_model = True
                attempts.extend(
                    q for q in await self._llm_song_queries(query) if q not in tried
                )

        return (
            f"Could not find anything playable for '{query}'. Do not claim it "
            "is playing. Say you could not find it and ask for the artist."
        )

    async def _play_entries(
        self, entries: list[dict], index: int, deadline: float | None = None,
        radio: bool = True,
    ) -> str:
        if not entries:
            return "There are no saved songs to play."
        # A search result is not a promise: individual videos are age-gated,
        # region-blocked or simply broken for yt-dlp on any given day. Walk
        # forward until one of them actually resolves rather than reporting the
        # whole request as failed because the top hit was unplayable.
        error = "no playable result"
        entry = None
        start = index
        while index < len(entries):
            if deadline is not None and time.monotonic() > deadline:
                break
            candidate = dict(entries[index])
            if candidate.get("stream_url"):
                entry = candidate
                break
            candidate, error = await self._resolve(candidate.get("webpage_url", ""))
            if candidate:
                entry = candidate
                break
            LOG.info("skipping unplayable result %d: %s", index, error)
            index += 1
        if entry is None:
            self.index = start
            return f"Music playback failed: {error}"
        replacing = self.player is not None
        await self.stop()
        if replacing:
            await self.session.send_event("audio_stop", reason="media_replace")
        self.queue = [dict(item) for item in entries]
        self.index = index
        self.current = {k: v for k, v in entry.items() if k != "stream_url"}
        self.history.append(dict(self.current))
        self._save_library()
        self.player = await asyncio.create_subprocess_exec(
            "ffmpeg", "-loglevel", "error", "-i", entry["stream_url"], "-vn",
            "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", "48000", "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self.media_stream_id += 1
        self.media_sequence = 0
        self._media_decimator = Decimator48To16()
        self._media_tail = np.zeros(0, dtype=np.float32)
        self.session.playing = True
        await self.session.send_event("state", state="music")
        await self.session.send_event("media_started", title=self.current["title"])
        self.player_task = asyncio.create_task(self._pump_media(self.player, self.media_stream_id))
        await self.notify_media()
        if radio and entry.get("video_id"):
            # After playback starts, never before: this costs a second yt-dlp
            # call and the song should not wait for it.
            self._radio_task = asyncio.create_task(self._load_radio(entry["video_id"]))
        return f"Now playing {self.current['title']} - {self.current['webpage_url']}"

    def _narrow_media(self, pcm: bytes) -> bytes:
        """48 kHz music down to 16 kHz for a link that cannot carry 768 kbps.

        The same decimation Kiki's voice already gets on a remote link, and for
        the same reason -- measured over a phone hotspot and the public tunnel,
        the board receives about 396 kbps, so uncompressed 48 kHz media is
        roughly twice what fits and the difference comes out as underruns.

        Phase-sensitive: the filter keeps every third sample of its own output,
        so whole groups of three have to reach it or each chunk restarts the
        pattern and the music develops a periodic warble. The remainder is
        carried into the next chunk.
        """
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        if self._media_tail.size:
            samples = np.concatenate((self._media_tail, samples))
        usable = samples.size - (samples.size % 3)
        self._media_tail = samples[usable:].copy()
        if usable == 0:
            return b""
        narrow = self._media_decimator.process(samples[:usable])
        return np.clip(np.rint(narrow), -32768, 32767).astype("<i2").tobytes()

    async def _load_radio(self, video_id: str) -> None:
        """Replace the queue with YouTube's own autoplay mix for this track.

        Search results are a terrible queue. "Suzume by RADWIMPS" found the
        right song and then sat next to "How to Make Homemade Sazón Seasoning"
        and "SAZON VIDOS - Masters At Work", because those are simply what a
        near-miss text search returns. YouTube's RD<id> mix is the list it
        would play on its own, which is what "next" is expected to mean.
        """
        try:
            entries = await self._search_url(
                f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}",
                self.radio_size,
            )
        except Exception:
            LOG.debug("radio lookup failed", exc_info=True)
            return
        if not entries or self.current is None:
            return
        # Keep the playing track wherever the mix puts it so "previous" after a
        # "next" returns here rather than to a different song.
        position = next(
            (i for i, item in enumerate(entries)
             if item.get("video_id") == video_id), 0)
        if entries[position].get("video_id") != video_id:
            entries.insert(0, dict(self.current))
            position = 0
        self.queue = entries
        self.index = position
        LOG.info("queue is now the YouTube mix (%d tracks, at %d)",
                 len(entries), position)
        await self.notify_media()

    async def notify_media(self) -> None:
        """Tell the panel what the transport controls should look like."""
        await self.session.send_event(
            "media_state",
            loaded=self.media_loaded,
            paused=self.paused,
            title=str((self.current or {}).get("title", ""))[:64],
            index=self.index,
            count=len(self.queue),
        )

    @property
    def media_loaded(self) -> bool:
        """A song is loaded -- playing OR paused."""
        return self.player_task is not None and not self.player_task.done()

    @property
    def media_active(self) -> bool:
        """A song is actually streaming to the speaker right now."""
        return self.media_loaded and not self._holds

    @property
    def paused(self) -> bool:
        """Paused by the user, as opposed to ducked for a conversation."""
        return "user" in self._holds

    @property
    def ducked(self) -> bool:
        return "duck" in self._holds

    async def _hold(self, reason: str) -> bool:
        """Stop feeding the speaker, and drop what the board already holds."""
        if reason in self._holds or not self.media_loaded:
            return False
        first = not self._holds
        self._holds.add(reason)
        if first:
            self.media_gate.clear()
            self._media_restart = True
            self.session.playing = False
            # Not merely "stop sending". The board still holds up to
            # playback_lead_seconds of music and keeps playing it -- and its
            # microphone stays shut for every millisecond of that.
            await self.session.send_event("audio_stop", reason=reason)
        return True

    async def _release(self, reason: str) -> bool:
        if reason not in self._holds:
            return False
        self._holds.discard(reason)
        if self._holds:
            # Still held for another reason: a song the user paused must stay
            # paused when the conversation that ducked it ends.
            return False
        self.media_gate.set()
        if not self.media_loaded:
            return False
        self.session.playing = True
        await self.session.send_event("state", state="music")
        return True

    async def duck(self) -> bool:
        """Pause the song so the board's microphone can come back.

        The firmware does not transmit microphone audio while its speaker is
        running, so "wake up and listen" is not something that can happen over
        the top of music: the board would sit in `listening` with nothing to
        send, no endpoint would ever fire, and the turn would never end. That
        is the hang -- ears animating, hold-to-talk doing nothing, until the
        track runs out. Observed live on a 91-minute mix.

        Pausing rather than stopping, because asking the time should not cost
        you the song. ffmpeg is left running and simply stops being read; the
        pipe fills and it blocks, so the music resumes exactly where it was.

        A *dance* is different: it is a performance, not background music, and
        resuming a routine halfway through a conversation would put Kiki back
        on a beat grid the listener has lost the thread of. Talking to her ends
        it, exactly like tapping the screen does.
        """
        if getattr(self.session, "dance_active", False):
            await self.session.end_dance("interrupted")
            return False
        return await self._hold("duck")

    async def unduck(self) -> None:
        """Resume a ducked song once the turn is over."""
        await self._release("duck")

    async def _pump_media(self, process, stream_id: int) -> None:
        playback_started = time.perf_counter()
        playback_seconds_sent = 0.0
        # Music gets its own, larger lead: a song has no latency requirement,
        # and a stuttering song is the most obvious failure on a slow link.
        playback_lead_seconds = getattr(
            self.session, "media_lead", self.session.config.media_lead_seconds
        )
        try:
            while True:
                chunk = await process.stdout.read(3840)
                if not chunk:
                    break
                playback_seconds_sent += len(chunk) / (2 * 48000)
                lead = playback_seconds_sent - (time.perf_counter() - playback_started)
                if lead > playback_lead_seconds:
                    await asyncio.sleep(lead - playback_lead_seconds)
                # Checked here, immediately before the send rather than at the
                # top of the loop, so a duck landing during the pacing sleep
                # cannot leak one more frame out behind it.
                if not self.media_gate.is_set():
                    await self.media_gate.wait()
                if self._media_restart:
                    self._media_restart = False
                    # Ducking dropped the board's in-flight audio and advanced
                    # its media watermark past this stream, so resuming needs a
                    # fresh id or every frame is silently discarded. The pacing
                    # clock is rebased at the same moment: left alone it would
                    # believe it owed the listener the whole length of the
                    # conversation and send it as fast as the socket allowed,
                    # overrunning the board's 128 KiB ring.
                    self.media_stream_id += 1
                    stream_id = self.media_stream_id
                    self.media_sequence = 0
                    playback_started = time.perf_counter()
                    playback_seconds_sent = len(chunk) / (2 * 48000)
                payload = amplify_pcm_s16(chunk, self.session.config.media_gain)
                kind = BinaryKind.MEDIA_PCM_S16_48K_MONO
                if getattr(self.session, "narrowband_out", False):
                    payload = self._narrow_media(payload)
                    kind = BinaryKind.MEDIA_PCM_S16_16K_MONO
                    if not payload:
                        continue      # held back for the filter's phase
                frame = AudioFrame(
                    kind, 0, stream_id, self.media_sequence,
                    time.time_ns() // 1000, payload,
                )
                self.media_sequence += 1
                await self.session.ws.send(frame.encode())
            await process.wait()
            await self.session.send_event(
                "audio_end", kind="media", stream_id=stream_id, sequence=self.media_sequence
            )
            if stream_id == self.media_stream_id and self.index + 1 < len(self.queue):
                next_index = self.index + 1
                entries = list(self.queue)
                self.player = None
                self.player_task = None
                self.session.playing = False
                await self._play_entries(entries, next_index)
            elif stream_id == self.media_stream_id:
                self.player = None
                self.player_task = None
                self.session.playing = False
                if getattr(self.session, "dance_active", False):
                    # The routine's bow lands in the last two bars, so the song
                    # running out IS the end of the performance. Nothing is
                    # queued behind it: a dance plays one track by design.
                    await self.session.end_dance("song_ended")
                elif self.repeat_queue and self.queue:
                    entries = [
                        {key: value for key, value in item.items() if key != "stream_url"}
                        for item in self.queue
                    ]
                    await self._play_entries(entries, 0)
                else:
                    await self.session.send_event(
                        "state", state="listening" if self.session.awake else "idle"
                    )
                    # Nothing left playing: the transport row has to go, or it
                    # sits on screen over an idle Kiki forever.
                    await self.notify_media()
        except (asyncio.CancelledError, ConnectionError):
            return

    # ------------------------------------------------------------- dancing --

    @staticmethod
    def _dance_song_hint(request: str) -> str:
        """The song the user named, if they named one.

        "dance" on its own is not a search query -- running it as one returns
        dance *tutorials*. Only an explicit "to <something>" or "play <song>"
        counts, and everything else is left to the choreography agent.
        """
        text = " ".join(str(request or "").split())
        match = re.search(
            r"\b(?:to|on|with)\s+(?:the\s+)?(?:song\s+|track\s+)?(.{3,80})$",
            text, flags=re.I,
        )
        if not match:
            return ""
        hint = match.group(1).strip(" .!?,")
        # "dance with me" / "dance to the beat" are not song names.
        if hint.lower() in {"me", "us", "the beat", "music", "it", "something"}:
            return ""
        return hint

    def _dance_context(self) -> str:
        """A couple of lines of grounding for the choreographer."""
        parts = []
        hour = time.localtime().tm_hour
        part_of_day = ("late night" if hour < 5 else "morning" if hour < 12
                       else "afternoon" if hour < 17 else "evening")
        parts.append(f"It is {part_of_day}.")
        battery = getattr(self.session, "_battery_bucket", None)
        if isinstance(battery, int) and battery <= 20:
            parts.append("Kiki's battery is low, so keep the routine gentle.")
        return "\n".join(parts)

    async def _dance_intro(self, request: str) -> None:
        """One short line while the song is being found.

        It exists for two reasons and both matter: it fills the several seconds
        the agent, the search and the beat analysis need, and it is the only
        speech in the whole feature -- once the music starts, a spoken line
        would be talking over it (the documented media/TTS overlap failure).
        """
        line = random.choice(self.DANCE_INTRO_LINES)
        try:
            await self.session.speak_background(line)
        except Exception:
            LOG.debug("dance intro line failed", exc_info=True)

    async def _dance_pick_song(self, query: str, deadline: float) -> tuple[dict | None, str]:
        """Resolve something playable, walking past dead results."""
        entries = await self._search(query, 4)
        if not entries:
            return None, f"nothing on YouTube for '{query}'"
        error = "no playable result"
        for entry in entries:
            if time.monotonic() > deadline:
                break
            resolved, error = await self._resolve(entry.get("webpage_url", ""))
            if resolved:
                return resolved, ""
        return None, error

    async def _dance(self, arguments: dict) -> str:
        """Choose a song, choreograph it, and hand the board a beat grid.

        Ordering here is the whole feature. The routine and the beat grid must
        reach the board BEFORE the first sample of music, because the board
        starts its dance clock on that sample -- so the analysis happens while
        Kiki is still talking, and playback is the last thing that happens.
        """
        config = self.session.config
        if not getattr(config, "dance_enabled", True):
            return "Dance mode is switched off in this gateway's configuration."
        if getattr(self.session, "dance_active", False):
            return ("Kiki is already dancing. Do not start another dance; "
                    "tell the user she is already going.")
        if getattr(self.session, "do_not_disturb", False):
            return ("Kiki is face down, which means do not disturb. "
                    "Do not claim she is dancing.")

        request = str(arguments.get("request") or arguments.get("song") or "").strip()
        started = time.monotonic()
        # One hard budget for the whole thing, split across the three phases
        # below. `_execute_device_tool_sync` abandons a device tool after 30 s,
        # and an abandoned dance is the worst outcome available: the coroutine
        # would carry on and start the music while the model was already being
        # told the tool failed. Finishing early with a hinted tempo beats that
        # every time.
        hard_deadline = started + self.dance_deadline
        intro_task = None
        if getattr(config, "dance_intro_line", True):
            intro_task = asyncio.create_task(self._dance_intro(request))

        try:
            plan = await choreography.request_plan(
                request, self._dance_context(),
                deadline=min(float(getattr(config, "dance_agent_deadline", 12.0)),
                             max(1.0, hard_deadline - time.monotonic() - 10.0)),
            )
            # Song choice, in falling order of authority: the agent's pick, a
            # song the user actually named, then a known-good fallback.
            hint = self._dance_song_hint(request)
            query = (plan.song if plan and plan.song else "") or hint
            if not query:
                query = random.choice(self.DANCE_FALLBACK_QUERIES)
                LOG.info("no song from the agent or the user; falling back to %r", query)

            # Leave the analysis its share of the budget; a song found with no
            # time left to measure it still dances, just to a hinted tempo.
            search_deadline = min(hard_deadline - 6.0, time.monotonic() + 12.0)
            entry, error = await self._dance_pick_song(query, search_deadline)
            if entry is None and hint and query != hint:
                entry, error = await self._dance_pick_song(hint, search_deadline)
            if entry is None:
                if intro_task:
                    await intro_task
                return (f"Could not find anything playable for '{query}' ({error}). "
                        "Do not claim Kiki is dancing. Say you could not find a "
                        "track and ask for a song.")

            grid = await self._dance_beat_grid(
                entry, plan, config, hard_deadline - time.monotonic())
            duration = float(entry.get("duration") or 0.0) or 210.0
            duration = min(duration, float(getattr(config, "dance_max_seconds", 480.0)))
            routine = choreography.build_routine(
                grid, duration, plan, str(entry.get("title") or query), request
            )
            LOG.info(
                "dance: %r %.1f BPM (conf %.2f) %d steps from %s, ready in %.1fs",
                routine.song, grid.bpm, grid.confidence, len(routine.steps),
                routine.source, time.monotonic() - started,
            )

            # The intro line has to be *finished*, not merely sent: the board
            # buffers up to a few hundred milliseconds, and starting the song
            # on top of it would cut her off mid-sentence.
            if intro_task:
                await intro_task
                await self.session.await_playback_drained(6.0)

            await self.session.begin_dance(routine, grid)
            result = await self._play_entries([entry], 0, radio=False)
            if not self.media_loaded or not await self._dance_music_started():
                await self.session.end_dance("no_audio")
                return (f"The music would not start ({result}). Do not claim "
                        "Kiki is dancing; say the song would not play.")
            return (f"Dancing to {routine.song}. The music and the dance are "
                    "already running -- say nothing more.")
        except asyncio.CancelledError:
            await self.session.end_dance("cancelled")
            raise
        except Exception as exc:
            LOG.exception("dance failed")
            await self.session.end_dance("failed")
            return (f"The dance could not be started ({exc}). Do not claim "
                    "Kiki is dancing.")

    async def _dance_music_started(self, timeout: float = 3.0) -> bool:
        """Wait for the song to actually produce audio, not merely to be loaded.

        `media_loaded` only says the pump task exists. An unplayable URL --
        a 403 from googlevideo, a dead extractor, a missing ffmpeg -- makes
        ffmpeg exit immediately, the pump reads nothing and ends, and the whole
        dance is over about sixty milliseconds after it began. What that looks
        like from the sofa is the panel going black and coming back, with Kiki
        having just announced she was about to dance. Observed live on
        2026-08-18.

        So success is defined as "frames have been sent", which is the only
        statement about the music that cannot be wrong.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.media_sequence > 0:
                return True
            if not self.media_loaded:
                return False        # the pump already gave up; no point waiting
            await asyncio.sleep(0.05)
        LOG.info("no media frames %.1fs after starting the song", timeout)
        return False

    async def _dance_beat_grid(self, entry: dict, plan, config,
                               budget: float = 15.0) -> BeatGrid:
        """Measured tempo when the audio has one, the model's hint when not."""
        grid = None
        timeout = min(float(getattr(config, "dance_analysis_timeout", 15.0)),
                      max(0.0, budget))
        if timeout < 2.0:
            LOG.info("no time left to measure the beat; using a hinted tempo")
            return self._hinted_grid(plan, config, None)
        try:
            grid = await analyse_url(
                entry.get("stream_url", ""),
                seconds=float(getattr(config, "dance_analysis_seconds", 45.0)),
                timeout=timeout,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception("beat analysis failed; using the model's hint")
        minimum = float(getattr(config, "dance_min_beat_confidence", 0.25))
        if grid is not None and grid.confidence >= minimum:
            return grid
        if grid is not None:
            LOG.info("beat grid confidence %.2f below %.2f; using a hinted tempo",
                     grid.confidence, minimum)
        return self._hinted_grid(plan, config, grid)

    @staticmethod
    def _hinted_grid(plan, config, measured: BeatGrid | None) -> BeatGrid:
        """The model's BPM, or the configured default, with beat 0 at the top.

        An unmeasured grid still keeps the *energy* track when the analysis
        produced one, so the stage lighting reacts to the real song even when
        its tempo could not be trusted.
        """
        bpm = float(plan.bpm) if plan is not None and plan.bpm else 0.0
        if not bpm:
            bpm = float(getattr(config, "dance_default_bpm", 112.0))
        return BeatGrid(
            bpm=bpm, beat0=0.0, confidence=0.0,
            energy=measured.energy if measured is not None else (),
            analysed_seconds=measured.analysed_seconds if measured is not None else 0.0,
        )

    def _like_current(self) -> str:
        if not self.current:
            return "No song is currently playing."
        identity = self.current.get("video_id") or self.current.get("webpage_url")
        if any((item.get("video_id") or item.get("webpage_url")) == identity for item in self.liked):
            return f"{self.current['title']} is already liked."
        self.liked.append(dict(self.current))
        self._save_library()
        return f"Added {self.current['title']} to liked songs."

    async def _control(self, action: str) -> str:
        action = action.lower().replace(" ", "_")
        title = str((self.current or {}).get("title", "the song"))
        if action in {"pause", "hold"}:
            if not await self._hold("user"):
                return "No song is playing right now."
            await self.notify_media()
            return f"Paused {title}."
        if action in {"resume", "play", "unpause", "continue"}:
            if not self.media_loaded:
                return "No song is loaded to resume."
            await self._release("user")
            await self.notify_media()
            return f"Resumed {title}."
        if action in {"toggle", "play_pause", "pause_play"}:
            return await self._control("resume" if self.paused else "pause")
        if action in {"next", "next_song", "skip", "previous", "previous_song", "prev", "back"}:
            forward = action.startswith(("next", "skip"))
            target = self.index + (1 if forward else -1)
            if target < 0:
                return "This is the first song in the queue."
            if target >= len(self.queue):
                return "This is the last song in the queue."
            # A user pause must not survive an explicit skip -- the new track
            # would start silently and look like another hang.
            await self._release("user")
            return await self._play_entries(self.queue, target)
        if action in {"stop", "cancel"}:
            self.repeat_queue = False
            await self.stop()
            await self.session.send_event("audio_stop", reason="music_stop")
            await self.notify_media()
            return "Music stopped."
        return "No song is playing right now."

    @staticmethod
    def _duration_seconds(value) -> int:
        if isinstance(value, bool):
            raise ValueError("Timer duration must be a positive number.")
        if isinstance(value, (int, float)):
            seconds = float(value)
        elif isinstance(value, str):
            text = value.strip().lower().replace("-", " ")
            match = re.fullmatch(
                r"(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)?",
                text,
            )
            if match:
                number = float(match.group(1))
                unit = match.group(2) or "seconds"
            else:
                words_match = re.fullmatch(
                    r"([a-z ]+)\s+(seconds?|secs?|minutes?|mins?|hours?|hrs?)", text
                )
                if not words_match:
                    raise ValueError("Use a duration such as 30 seconds or 5 minutes.")
                words = [word for word in words_match.group(1).split() if word != "and"]
                if not words or any(word not in NUMBER_WORDS for word in words):
                    raise ValueError("Use a duration such as 30 seconds or 5 minutes.")
                number = float(sum(NUMBER_WORDS[word] for word in words))
                unit = words_match.group(2)
            multiplier = 3600 if unit.startswith(("h", "hour")) else 60 if unit.startswith(("m", "min")) else 1
            seconds = number * multiplier
        else:
            raise ValueError("Timer duration must be a positive number.")
        rounded = int(round(seconds))
        if rounded < 1:
            raise ValueError("Timer duration must be at least one second.")
        if rounded > 7 * 24 * 60 * 60:
            raise ValueError("Timer duration cannot be longer than seven days.")
        return rounded

    async def _set_timer(self, duration) -> str:
        try:
            seconds = self._duration_seconds(duration)
        except (TypeError, ValueError) as exc:
            return str(exc)

        async def alarm():
            await asyncio.sleep(seconds)
            path = self.root / "sound_effects" / "soundeffects" / "timer.mp3"
            if path.exists():
                await self._play_entries([{"title": "Timer", "webpage_url": path.as_uri(), "stream_url": str(path)}], 0)
            await self.session.send_event("timer", state="expired")

        asyncio.create_task(alarm())
        return f"Timer set for {seconds} seconds."

    async def stop(self) -> None:
        # Clear the duck first. Stopping a paused song while the gate stayed
        # shut would park the *next* song's pump the moment it started.
        self._holds.clear()
        self._media_restart = False
        self.media_gate.set()
        if self._radio_task:
            self._radio_task.cancel()
            self._radio_task = None
        task, self.player_task = self.player_task, None
        process, self.player = self.player, None
        if task:
            task.cancel()
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=1)
            except asyncio.TimeoutError:
                process.kill()
        self.session.playing = False

    async def close(self) -> None:
        await self.stop()

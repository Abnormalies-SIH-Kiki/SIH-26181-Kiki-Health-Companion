"""Choreography: what Kiki dances, and how it reaches the board.

THE SHAPE OF THIS
-----------------
The model does not animate anything. It composes.

The board owns a fixed library of hand-built moves -- each one a parametric,
beat-locked animation with anticipation, squash-and-stretch and follow-through
-- and this module turns a song into an ordered list of *which* move happens on
*which* beat, at what intensity, with what stage effect. That split is what
makes the result look like a dancer rather than a random pose generator: the
craft lives in the moves, the musicality lives in the arrangement, and a
language model is good at exactly one of those two things.

Three sources feed the arrangement, in falling order of authority:

1. **The choreography agent** -- the cloud action-agent path (same provider and
   guards as `complex_query`), which picks the song and writes a routine with
   real intent: "wave on the intro, freeze on the drop, heart hands on the last
   chorus, bow at the end".
2. **The beat grid** (`beatgrid.py`) -- real tempo, real downbeat, real per-beat
   loudness measured from the audio.
3. **The composer below** -- which fills every gap the agent left, extends the
   routine to the full length of the song, and can produce the entire routine
   on its own when the agent is unavailable.

Layer 3 always runs. That is deliberate: a dance that depends on a cloud call
succeeding is a dance that sometimes doesn't happen, and "Kiki refused to dance"
is a much worse failure than "Kiki danced to a slightly less imaginative
routine".

WIRE FORMAT
-----------
The routine goes to the board as a compact string, not JSON. A three-minute
song at 128 BPM is ~380 beats; at phrase granularity that is 100-250 entries,
which as a cJSON object tree is tens of kilobytes of parse on a device with
~297 KiB of internal RAM. Five comma-separated small integers per entry costs
about 14 bytes and parses with a pointer and `strtol`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import logging
import math
import random
import re
import time

from .beatgrid import BeatGrid


LOG = logging.getLogger(__name__)


# --------------------------------------------------------------- vocabulary --
# ORDER IS THE WIRE FORMAT. These indices are what the board receives, so a
# move may be appended but never reordered or removed without changing the
# firmware table in the same commit. tests/test_dance.py parses the firmware
# header and asserts the two lists are identical, because a silent skew here
# means Kiki does the wrong move rather than failing loudly.
MOVES: tuple[str, ...] = (
    "groove",        # 0  the neutral two-step everything returns to
    "bounce",        # 1
    "hip_sway",      # 2
    "side_step",     # 3
    "body_roll",     # 4
    "claw_wave",     # 5
    "disco_point",   # 6
    "spin",          # 7
    "jump",          # 8
    "shimmy",        # 9
    "robot",         # 10
    "pop_lock",      # 11
    "moonwalk",      # 12
    "kick",          # 13
    "head_bang",     # 14
    "sprinkler",     # 15
    "crab_walk",     # 16
    "starfish",      # 17
    "heart_hands",   # 18
    "dab",           # 19
    "wink_push_in",  # 20
    "snap_freeze",   # 21
    "bow",           # 22
    "wave_crowd",    # 23
    "twirl",         # 24
    "stomp",         # 25
)
MOVE_IDS = {name: index for index, name in enumerate(MOVES)}

# Stage effects. Also positional, also mirrored in the firmware.
EFFECTS: tuple[str, ...] = (
    "none",      # 0
    "sparkle",   # 1
    "confetti",  # 2
    "hearts",    # 3
    "burst",     # 4
    "rings",     # 5
    "flash",     # 6
    "stars",     # 7
)
EFFECT_IDS = {name: index for index, name in enumerate(EFFECTS)}

# The moods are the panel's existing expression palettes (kiki_theme.hpp), so a
# dance recolours the whole room in a language the rest of the UI already
# speaks instead of inventing a second one.
MOODS: tuple[str, ...] = (
    "excited", "happy", "giggle", "mischief", "proud", "love", "curious",
    "wink", "awe", "idea", "surprised", "dizzy", "sleepy", "shy",
)
DEFAULT_MOOD = "excited"

MAX_ENTRIES = 256
MAX_ENERGY_BEATS = 1024
# Two bars of lead-in. Long enough that the routine's first real move lands on
# a downbeat the listener has already felt, short enough that nobody waits.
LEAD_IN_BEATS = 8


@dataclass(frozen=True)
class Step:
    """One move, starting on an absolute beat of the song."""

    beat: int
    move: int
    beats: int          # how long it holds, in beats
    intensity: int      # 0..15
    effect: int         # index into EFFECTS

    def encode(self) -> str:
        return f"{self.beat},{self.move},{self.beats},{self.intensity},{self.effect}"


@dataclass
class Routine:
    song: str
    mood: str
    steps: list[Step] = field(default_factory=list)
    why: str = ""
    source: str = "composed"   # "agent", "agent+composed" or "composed"

    def encode(self) -> str:
        return ";".join(step.encode() for step in self.steps)


# ------------------------------------------------------------ move metadata --
# What the composer needs to arrange the library musically: which band of song
# energy a move belongs to, how long it wants to be, and whether it is a big
# one-shot statement rather than something to groove on.
@dataclass(frozen=True)
class MoveInfo:
    energy: tuple[float, float]   # usable song-energy band, 0..1
    beats: int                    # natural length
    accent: bool = False          # a one-shot hit, not a loop
    finisher: bool = False


MOVE_INFO: dict[str, MoveInfo] = {
    "groove":       MoveInfo((0.00, 1.00), 4),
    "bounce":       MoveInfo((0.25, 1.00), 4),
    "hip_sway":     MoveInfo((0.00, 0.70), 4),
    "side_step":    MoveInfo((0.20, 0.85), 4),
    "body_roll":    MoveInfo((0.15, 0.75), 4),
    "claw_wave":    MoveInfo((0.10, 0.80), 4),
    "disco_point":  MoveInfo((0.45, 1.00), 2, accent=True),
    "spin":         MoveInfo((0.55, 1.00), 2, accent=True),
    "jump":         MoveInfo((0.60, 1.00), 2, accent=True),
    "shimmy":       MoveInfo((0.35, 0.95), 4),
    "robot":        MoveInfo((0.30, 0.90), 4),
    "pop_lock":     MoveInfo((0.40, 1.00), 4),
    "moonwalk":     MoveInfo((0.25, 0.85), 8),
    "kick":         MoveInfo((0.50, 1.00), 2, accent=True),
    "head_bang":    MoveInfo((0.65, 1.00), 4),
    "sprinkler":    MoveInfo((0.40, 0.95), 4),
    "crab_walk":    MoveInfo((0.20, 0.80), 8),
    "starfish":     MoveInfo((0.55, 1.00), 2, accent=True),
    "heart_hands":  MoveInfo((0.00, 0.70), 4, accent=True),
    "dab":          MoveInfo((0.45, 1.00), 2, accent=True),
    "wink_push_in": MoveInfo((0.00, 0.65), 4, accent=True),
    "snap_freeze":  MoveInfo((0.50, 1.00), 2, accent=True),
    "bow":          MoveInfo((0.00, 1.00), 8, accent=True, finisher=True),
    "wave_crowd":   MoveInfo((0.10, 0.80), 4, accent=True),
    "twirl":        MoveInfo((0.35, 1.00), 4),
    "stomp":        MoveInfo((0.55, 1.00), 2, accent=True),
}


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


# ----------------------------------------------------------------- composer --

def _phrase_energy(energy: tuple[int, ...], start: int, beats: int) -> float:
    """Mean 0..1 loudness across one phrase, tolerant of a short energy list."""
    if not energy:
        return 0.55
    end = min(len(energy), start + beats)
    if start >= len(energy):
        # Past the analysed window: reuse the tail, so a long song keeps the
        # dynamics of the part we did measure instead of flattening out.
        start = start % len(energy)
        end = min(len(energy), start + beats)
    window = energy[start:end] or energy[-beats:]
    return _clamp(sum(window) / (len(window) * 15.0), 0.0, 1.0)


def _candidates(level: float, accent: bool) -> list[str]:
    out = []
    for name, info in MOVE_INFO.items():
        if info.finisher or info.accent != accent:
            continue
        low, high = info.energy
        if low - 0.05 <= level <= high + 0.05:
            out.append(name)
    return out or ["groove"]


def compose(
    grid: BeatGrid,
    total_beats: int,
    seed: str = "",
    agent_steps: list[Step] | None = None,
) -> list[Step]:
    """Produce a full routine for `total_beats`, honouring the agent's plan.

    The agent's steps are kept exactly where it put them. Everything around
    them -- the gaps, the tail past its last idea, the closing bow -- is filled
    here from the measured energy, so the routine is complete and phrase-
    aligned no matter how much or how little the model contributed.
    """
    rng = random.Random(f"{seed}|{grid.bpm:.2f}|{total_beats}")
    energy = grid.energy
    steps: list[Step] = []
    booked: list[Step] = sorted(agent_steps or [], key=lambda step: step.beat)

    # Never repeat a move twice running, and keep the last four out of the way
    # of the next pick: repetition is what makes generated choreography read as
    # generated.
    recent: list[str] = []

    def choose(level: float, accent: bool) -> str:
        pool = [name for name in _candidates(level, accent) if name not in recent[-4:]]
        pool = pool or _candidates(level, accent)
        pick = rng.choice(pool)
        recent.append(pick)
        return pick

    beat = 0
    booked_index = 0
    while beat < total_beats:
        # An agent step that starts here (or that we have walked past) wins.
        while booked_index < len(booked) and booked[booked_index].beat < beat:
            booked_index += 1
        if booked_index < len(booked) and booked[booked_index].beat == beat:
            step = booked[booked_index]
            steps.append(step)
            recent.append(MOVES[step.move])
            beat += max(1, step.beats)
            booked_index += 1
            continue

        next_booked = booked[booked_index].beat if booked_index < len(booked) else total_beats
        room = min(next_booked, total_beats) - beat
        if room <= 0:
            beat = next_booked
            continue

        level = _phrase_energy(energy, beat, 4)
        # A phrase that is much louder than the one before it is a drop or a
        # chorus entry. Those get an accent move and an effect -- this is the
        # single biggest difference between "moving to music" and "dancing to
        # this song".
        previous = _phrase_energy(energy, max(0, beat - 4), 4)
        lift = level - previous
        accent = room >= 2 and (lift > 0.22 or (beat % 32 == 0 and level > 0.55))

        name = choose(level, accent)
        info = MOVE_INFO[name]
        length = min(info.beats, room)
        if not accent:
            # Hold a groove for a musical length: two phrases when there is
            # room, so the eye can settle before the next idea.
            # ...but never past two bars. A move held for four bars stops
            # reading as a choice and starts reading as a stuck animation.
            if info.beats <= 4 and room >= info.beats * 2 and rng.random() < 0.45:
                length = min(info.beats * 2, room, 8)
        length = max(1, length)

        effect = "none"
        if accent:
            effect = rng.choice(["burst", "sparkle", "rings", "flash"])
        elif beat % 32 == 0 and level > 0.5:
            effect = "stars"
        elif level > 0.8 and rng.random() < 0.25:
            effect = "confetti"

        steps.append(Step(
            beat=beat,
            move=MOVE_IDS[name],
            beats=length,
            intensity=int(round(_clamp(0.35 + level * 0.75, 0.0, 1.0) * 15)),
            effect=EFFECT_IDS[effect],
        ))
        beat += length

    # Land the ending. A routine that simply stops when the audio does looks
    # like a crash; a bow reads as a performance that finished.
    if total_beats >= 16:
        bow_at = max(0, total_beats - 8)
        steps = [step for step in steps if step.beat < bow_at]
        steps.append(Step(bow_at, MOVE_IDS["bow"], 8,
                          12, EFFECT_IDS["confetti"]))

    return _tidy(steps)


def _tidy(steps: list[Step]) -> list[Step]:
    """Sort, de-overlap and cap. The board trusts what it is given."""
    ordered = sorted(steps, key=lambda step: step.beat)
    out: list[Step] = []
    for step in ordered:
        if out and step.beat <= out[-1].beat:
            continue                      # same downbeat: first one wins
        if out:
            gap = step.beat - out[-1].beat
            if out[-1].beats > gap:       # trim an overlap rather than drop it
                out[-1] = Step(out[-1].beat, out[-1].move, gap,
                               out[-1].intensity, out[-1].effect)
        out.append(step)
    return out[:MAX_ENTRIES]


# -------------------------------------------------------------- agent input --

_AGENT_PROMPT = """You are Kiki's choreographer. Kiki is a small, cute pixel \
crab robot with two claws who is about to dance on her round display for the \
person in front of her.

Pick ONE real song that can be found on YouTube, then write a routine for it.

Request: "{request}"
{context}
MOVE VOCABULARY (use these names exactly):
{moves}

STAGE EFFECTS: {effects}
MOODS (sets the colour of the whole room): {moods}

Rules:
- Beats are counted from the song's first beat. Beat 0 is the first downbeat.
- Work in phrases of 4 or 8 beats, the way real choreography does.
- Put accent moves (spin, jump, snap_freeze, dab, stomp, kick, starfish, \
disco_point) on the big musical moments: the drop, the chorus entry, a stop.
- Use heart_hands, wink_push_in and wave_crowd sparingly. They are the charming \
moments and they only work if they are rare.
- 14 to 20 entries. Cover roughly the first ninety seconds; the rest is \
extended automatically, so do not pad the list.
- intensity is 0.0 to 1.0 and should follow the song's energy.

Reply with ONLY a JSON object, no prose:
{{"song": "Artist - Title", "mood": "excited", "bpm": 120, "why": "one short \
sentence", "routine": [{{"beat": 0, "move": "groove", "beats": 8, \
"intensity": 0.5, "effect": "none"}}]}}"""


def build_prompt(request: str, context: str = "") -> str:
    return _AGENT_PROMPT.format(
        request=(request or "dance").strip()[:200],
        context=(context.strip() + "\n") if context.strip() else "",
        moves=", ".join(MOVES),
        effects=", ".join(EFFECTS),
        moods=", ".join(MOODS),
    )


@dataclass
class AgentPlan:
    song: str = ""
    mood: str = DEFAULT_MOOD
    bpm: float = 0.0
    why: str = ""
    steps: list[Step] = field(default_factory=list)


def parse_agent_plan(raw: str) -> AgentPlan | None:
    """Validate the model's routine. Nothing here is trusted.

    Every field is range-checked and every move name is looked up, because the
    board decodes this into array indices and a bad one is a crash rather than
    a bad dance. Individual malformed entries are dropped, not fatal -- one
    hallucinated move name should cost that move, not the whole routine.
    """
    if not raw or not raw.strip():
        return None
    try:
        from core.brain.fast_cloud import first_json_object

        text = first_json_object(raw)
    except Exception:
        text = _first_json_object(raw)
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        # Almost always a routine that ran into the completion-token limit
        # mid-array. Everything before the cut is still perfectly good
        # choreography and the composer fills the rest, so salvage it rather
        # than throwing away a song choice and fifteen usable moves.
        data = _salvage(raw)
        if data is None:
            LOG.info("choreography agent returned unparseable JSON")
            return None
        LOG.info("salvaged a truncated routine (%d entries)",
                 len(data.get("routine") or []))
    if not isinstance(data, dict):
        return None

    plan = AgentPlan()
    plan.song = str(data.get("song") or "").strip()[:120]
    plan.why = str(data.get("why") or "").strip()[:200]
    mood = str(data.get("mood") or "").strip().lower()
    plan.mood = mood if mood in MOODS else DEFAULT_MOOD
    try:
        bpm = float(data.get("bpm") or 0)
    except (TypeError, ValueError):
        bpm = 0.0
    plan.bpm = bpm if 40.0 <= bpm <= 220.0 else 0.0

    entries = data.get("routine")
    if isinstance(entries, list):
        for entry in entries[: MAX_ENTRIES * 2]:
            step = _parse_step(entry)
            if step is not None:
                plan.steps.append(step)
    plan.steps = _tidy(plan.steps)
    if not plan.song and not plan.steps:
        return None
    return plan


_SALVAGE_STRING = r'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"'
_SALVAGE_NUMBER = r'"{key}"\s*:\s*(-?\d+(?:\.\d+)?)'


def _salvage(text: str) -> dict | None:
    """Rebuild what can be rebuilt from a truncated reply.

    The top-level object never closes when the completion is cut off, so
    json.loads sees nothing at all. The individual routine entries, however,
    are small and complete right up to the cut -- so they are recovered one
    balanced object at a time, and the scalar fields are read directly.
    """
    if not text:
        return None
    data: dict = {}
    for key in ("song", "mood", "why"):
        match = re.search(_SALVAGE_STRING.format(key=key), text)
        if match:
            data[key] = match.group(1)
    match = re.search(_SALVAGE_NUMBER.format(key="bpm"), text)
    if match:
        data["bpm"] = float(match.group(1))

    routine_at = text.find('"routine"')
    entries = []
    if routine_at >= 0:
        depth = 0
        start = -1
        for index in range(routine_at, len(text)):
            character = text[index]
            if character == "{":
                if depth == 0:
                    start = index
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        entries.append(json.loads(text[start : index + 1]))
                    except ValueError:
                        pass
                    start = -1
                elif depth < 0:
                    break
    if entries:
        data["routine"] = entries
    return data or None


def _parse_step(entry: object) -> Step | None:
    if not isinstance(entry, dict):
        return None
    move = str(entry.get("move") or "").strip().lower().replace(" ", "_")
    if move not in MOVE_IDS:
        return None
    try:
        beat = int(round(float(entry.get("beat", 0))))
        beats = int(round(float(entry.get("beats", MOVE_INFO[move].beats))))
        intensity = float(entry.get("intensity", 0.7))
    except (TypeError, ValueError):
        return None
    if beat < 0 or beat > 4096:
        return None
    beats = max(1, min(32, beats))
    if intensity > 1.5:      # a model that answered in percent
        intensity = intensity / 100.0
    intensity = _clamp(intensity, 0.0, 1.0)
    effect = str(entry.get("effect") or entry.get("fx") or "none").strip().lower()
    return Step(
        beat=beat,
        move=MOVE_IDS[move],
        beats=beats,
        intensity=int(round(intensity * 15)),
        effect=EFFECT_IDS.get(effect, 0),
    )


def _first_json_object(text: str) -> str:
    """Local stand-in for fast_cloud.first_json_object.

    The legacy runtime owns the real one; this exists so the module is
    importable and testable without it (and so a refactor there cannot silently
    take the dance offline).
    """
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, character in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            if depth == 0:
                start = index
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start : index + 1]
    return text


async def request_plan(request: str, context: str = "",
                       deadline: float = 14.0) -> AgentPlan | None:
    """Ask the cloud action-agent brain for a song and a routine.

    Same provider, key rotation and JSON-extraction guards as `complex_query`
    (`core.brain.fast_cloud`), which is what keeps this off the local speaking
    slot. Bounded and non-fatal: on timeout or any failure the composer writes
    the whole routine itself and Kiki still dances.
    """
    prompt = build_prompt(request, context)
    started = time.monotonic()
    try:
        from core.brain.fast_cloud import complete

        raw = await asyncio.wait_for(asyncio.to_thread(complete, prompt),
                                     timeout=deadline)
    except asyncio.TimeoutError:
        LOG.info("choreography agent exceeded its %.0fs deadline", deadline)
        return None
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        LOG.info("choreography agent unavailable: %s", exc)
        return None
    plan = parse_agent_plan(raw)
    LOG.info("choreography agent answered in %.1fs: song=%r mood=%s steps=%d",
             time.monotonic() - started,
             plan.song if plan else None,
             plan.mood if plan else "-",
             len(plan.steps) if plan else 0)
    return plan


# ------------------------------------------------------------ the router ---
# "kiki, dance" should not have to survive a round trip through the speaking
# model to reach the dance tool. This is the same two-triggers-one-path shape
# the rest of the system uses: a high-precision regex that fires in no time at
# all, with the tool still in the model's catalog for everything it misses.
#
# Because the tool IS in the catalog, this regex is free to be conservative --
# a miss costs one box round trip, not the feature. That is the opposite of
# complex_query's router, which is load-bearing because its tools were removed
# from the catalog entirely.
_DANCE_PHRASE = re.compile(
    r"^(?:(?:ok(?:ay)?|hey|yo|so|now|please|come on|c'?mon|kiki)[\s,]+)*"
    r"(?:(?:can|could|will|would)\s+you\s+)?"
    r"(?:please\s+)?"
    r"(?:(?:i\s+want\s+(?:you\s+)?to|let'?s|lets|do\s+a|do\s+some|"
    r"show\s+me\s+(?:your|some)|start|time\s+to)\s+)?"
    r"(?:dance|dancing|dance\s+party|bust\s+a\s+move|moves|naach|nach(?:o)?)"
    r"(?:\s+(?:for\s+me|for\s+us|with\s+me|now|please|kiki|"
    r"to\s+.{1,60}|on\s+.{1,60}))?"
    r"[\s.!?]*$",
    re.IGNORECASE,
)
# Words that turn a dance sentence into something that is not a request.
# Whole words only: substring matching rejected "show me your moves", because
# "how" lives inside "show".
_DANCE_NEGATIVES = re.compile(
    r"\b(?:stop|don'?t|do\s+not|did|video|class|lesson|tutorial|what|who|"
    r"when|where|why|how|song\s+about|remind|remember|yesterday|tomorrow)\b",
    re.IGNORECASE,
)


def is_dance_request(text: str) -> bool:
    """True for an unambiguous spoken request to dance."""
    cleaned = " ".join(str(text or "").split())
    if not cleaned or len(cleaned.split()) > 9:
        return False
    lowered = cleaned.lower()
    if _DANCE_NEGATIVES.search(lowered):
        return False
    return bool(_DANCE_PHRASE.match(lowered))


# ------------------------------------------------------------- the payload --

def encode_energy(energy: tuple[int, ...], limit: int = MAX_ENERGY_BEATS) -> str:
    """One hex digit of loudness per beat -- the board's whole stage-lighting
    input. A 0..15 range is all a five-bar equaliser and a background pulse can
    show, and it keeps a four-minute song's dynamics under a kilobyte."""
    return "".join(f"{min(15, max(0, value)):x}" for value in energy[:limit])


def build_routine(
    grid: BeatGrid,
    duration_seconds: float,
    plan: AgentPlan | None,
    song_title: str,
    request_seed: str = "",
) -> Routine:
    """Everything above, assembled into what the board is actually sent."""
    total_beats = int(max(8, min(4096, duration_seconds / grid.beat_seconds)))
    agent_steps = list(plan.steps) if plan else []
    mood = plan.mood if plan else DEFAULT_MOOD
    steps = compose(grid, total_beats, seed=request_seed or song_title,
                    agent_steps=agent_steps)
    if agent_steps and len(agent_steps) >= 4:
        source = "agent+composed"
    elif agent_steps:
        source = "agent-partial+composed"
    else:
        source = "composed"
    return Routine(
        song=song_title,
        mood=mood,
        steps=steps,
        why=plan.why if plan else "",
        source=source,
    )

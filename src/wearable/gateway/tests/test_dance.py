"""The choreographer: routing, the agent's output, and the wire format.

Two things here are load-bearing beyond ordinary correctness:

* the move and effect tables are a *shared* wire format with the firmware, and
  a silent skew means Kiki does the wrong move rather than failing, so the
  firmware header is parsed and compared;
* everything the model produces is untrusted input that ends up as an array
  index on a microcontroller, so the parser is tested with junk on purpose.
"""

from pathlib import Path
import re

from kiki_gateway import dance
from kiki_gateway.beatgrid import BeatGrid


FIRMWARE_HEADER = (
    Path(__file__).resolve().parent.parent.parent / "firmware" / "main" / "kiki_dance.hpp"
)


def _enum_values(name: str) -> list[str]:
    """The identifiers of one C++ enum class in the firmware header."""
    text = FIRMWARE_HEADER.read_text()
    body = re.search(rf"enum class {name} : uint8_t \{{(.*?)\}};", text, re.S)
    assert body, f"could not find enum {name} in {FIRMWARE_HEADER}"
    values = []
    for line in body.group(1).splitlines():
        line = line.split("//")[0].strip().rstrip(",")
        if not line:
            continue
        identifier = line.split("=")[0].strip()
        if identifier and identifier != "Count":
            values.append(identifier)
    return values


def _snake(camel: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", camel).lower()


def make_grid(bpm: float = 120.0, beats: int = 240) -> BeatGrid:
    energy = tuple((i * 7) % 16 for i in range(beats))
    return BeatGrid(bpm=bpm, beat0=0.05, confidence=0.9, energy=energy,
                    analysed_seconds=45.0)


# --------------------------------------------------------- the wire format --

def test_the_move_table_matches_the_firmware_exactly():
    assert [_snake(name) for name in _enum_values("DanceMove")] == list(dance.MOVES)


def test_the_effect_table_matches_the_firmware_exactly():
    assert [_snake(name) for name in _enum_values("DanceEffect")] == list(dance.EFFECTS)


def test_a_step_encodes_to_five_small_integers():
    step = dance.Step(beat=12, move=7, beats=2, intensity=14, effect=4)
    assert step.encode() == "12,7,2,14,4"


def test_a_whole_song_stays_small_enough_to_parse_on_the_board():
    routine = dance.build_routine(make_grid(), 240.0, None, "Test Song")
    encoded = routine.encode()
    assert len(routine.steps) <= dance.MAX_ENTRIES
    # Comfortably under a websocket frame the board can hold twice over.
    assert len(encoded) < 6000
    assert all(part.count(",") == 4 for part in encoded.split(";"))


def test_energy_is_one_hex_digit_per_beat():
    assert dance.encode_energy((0, 15, 7, 16, -3)) == "0f7f0"


# ------------------------------------------------------------- the router --

def test_plain_requests_route_straight_to_the_dance():
    for text in ["dance", "kiki dance", "let's dance", "ok dance for me",
                 "can you dance", "show me your moves", "dance party",
                 "dance to levitating by dua lipa", "please dance for me"]:
        assert dance.is_dance_request(text), text


def test_sentences_that_merely_mention_dancing_do_not():
    for text in ["stop dancing", "did you dance yesterday", "what is dancing",
                 "play a dance song", "i went to dance class",
                 "show me a dance video", "how do you dance",
                 "remind me about the dance recital",
                 "i love dancing in the rain when it is warm outside"]:
        assert not dance.is_dance_request(text), text


def test_show_me_your_moves_is_not_rejected_by_the_word_how():
    # "how" is a substring of "show". Whole-word matching is the fix, and this
    # is the case that found it.
    assert dance.is_dance_request("show me your moves")


# ------------------------------------------------- the agent's own output --

def test_a_good_plan_survives_prose_around_it():
    raw = (
        'Sure! Here is the routine:\n'
        '{"song": "Dua Lipa - Levitating", "mood": "excited", "bpm": 103, '
        '"why": "disco", "routine": ['
        '{"beat": 0, "move": "groove", "beats": 8, "intensity": 0.4},'
        '{"beat": 8, "move": "claw_wave", "beats": 4, "intensity": 0.6, '
        '"effect": "sparkle"}]}\n'
        'Hope you like it!'
    )
    plan = dance.parse_agent_plan(raw)
    assert plan.song == "Dua Lipa - Levitating"
    assert plan.mood == "excited"
    assert plan.bpm == 103.0
    assert [step.beat for step in plan.steps] == [0, 8]
    assert plan.steps[1].effect == dance.EFFECT_IDS["sparkle"]


def test_one_invented_move_costs_that_move_and_nothing_else():
    raw = (
        '{"song": "X", "routine": ['
        '{"beat": 0, "move": "groove", "beats": 4},'
        '{"beat": 4, "move": "backflip_through_a_window", "beats": 4},'
        '{"beat": 8, "move": "spin", "beats": 2}]}'
    )
    plan = dance.parse_agent_plan(raw)
    assert [dance.MOVES[step.move] for step in plan.steps] == ["groove", "spin"]


def test_out_of_range_values_are_clamped_not_trusted():
    raw = (
        '{"song": "X", "mood": "chartreuse", "bpm": 900, "routine": ['
        '{"beat": -5, "move": "spin", "beats": 999, "intensity": 95, '
        '"effect": "fireworks"}]}'
    )
    plan = dance.parse_agent_plan(raw)
    assert plan.mood == dance.DEFAULT_MOOD      # unknown moods fall back
    assert plan.bpm == 0.0                      # 900 BPM is not a tempo
    # The negative beat is dropped outright; percent intensity is understood.
    assert plan.steps == [] or plan.steps[0].intensity <= 15


def test_intensity_given_as_a_percentage_is_understood():
    raw = '{"song": "X", "routine": [{"beat": 0, "move": "spin", "intensity": 80}]}'
    plan = dance.parse_agent_plan(raw)
    assert plan.steps[0].intensity == 12        # 0.80 * 15


def test_a_routine_cut_off_by_the_token_limit_is_salvaged():
    # What an 800-token completion cap actually produces: the top-level object
    # never closes, so json.loads sees nothing. The song choice and every
    # complete entry before the cut are still good, and the composer fills the
    # rest -- throwing all of it away was costing the agent's whole
    # contribution on every long routine.
    raw = (
        '{"song": "Dua Lipa - Levitating", "mood": "excited", "bpm": 103, '
        '"why": "disco funk", "routine": ['
        '{"beat": 0, "move": "groove", "beats": 8, "intensity": 0.4},'
        '{"beat": 8, "move": "claw_wave", "beats": 4, "intensity": 0.6},'
        '{"beat": 16, "move": "spin", "beats": 2, "intensity": 0.9},'
        '{"beat": 20, "move": "hip_sw'
    )
    plan = dance.parse_agent_plan(raw)
    assert plan.song == "Dua Lipa - Levitating"
    assert plan.mood == "excited"
    assert plan.bpm == 103.0
    assert [dance.MOVES[step.move] for step in plan.steps] == [
        "groove", "claw_wave", "spin"]


def test_junk_returns_nothing_rather_than_half_a_routine():
    assert dance.parse_agent_plan("") is None
    assert dance.parse_agent_plan("I would love to dance!") is None
    assert dance.parse_agent_plan("{not json") is None


def test_overlapping_steps_are_trimmed_so_the_board_never_sees_two_at_once():
    steps = dance._tidy([
        dance.Step(0, 1, 8, 8, 0),
        dance.Step(4, 2, 4, 8, 0),
        dance.Step(4, 3, 4, 8, 0),      # same downbeat: the first one wins
    ])
    assert [step.beat for step in steps] == [0, 4]
    assert steps[0].beats == 4           # trimmed to make room
    assert steps[1].move == 2


# -------------------------------------------------------------- composing --

def test_a_routine_is_written_even_with_no_agent_at_all():
    routine = dance.build_routine(make_grid(), 180.0, None, "Test Song")
    assert routine.source == "composed"
    assert len(routine.steps) > 20
    assert all(a.beat < b.beat for a, b in zip(routine.steps, routine.steps[1:]))


def test_the_agent_keeps_its_own_beats_and_the_composer_fills_the_rest():
    plan = dance.AgentPlan(song="X", steps=[
        dance.Step(0, dance.MOVE_IDS["groove"], 8, 8, 0),
        dance.Step(64, dance.MOVE_IDS["heart_hands"], 4, 10, 3),
    ])
    routine = dance.build_routine(make_grid(), 180.0, plan, "X")
    booked = {step.beat: dance.MOVES[step.move] for step in routine.steps}
    assert booked[0] == "groove"
    assert booked[64] == "heart_hands"
    assert routine.source.endswith("composed")


def test_every_routine_ends_on_a_bow():
    routine = dance.build_routine(make_grid(), 120.0, None, "X")
    assert dance.MOVES[routine.steps[-1].move] == "bow"


def test_a_short_clip_still_produces_something_danceable():
    routine = dance.build_routine(make_grid(), 6.0, None, "X")
    assert routine.steps
    assert all(step.beats >= 1 for step in routine.steps)


def test_composition_is_deterministic_for_the_same_song():
    grid = make_grid()
    first = dance.build_routine(grid, 180.0, None, "Same Song").encode()
    second = dance.build_routine(grid, 180.0, None, "Same Song").encode()
    assert first == second


def test_the_routine_reacts_to_the_song_rather_than_ignoring_it():
    quiet = BeatGrid(120.0, 0.0, 0.9, tuple([2] * 240), 45.0)
    loud = BeatGrid(120.0, 0.0, 0.9, tuple([15] * 240), 45.0)
    quiet_steps = dance.build_routine(quiet, 120.0, None, "X").steps
    loud_steps = dance.build_routine(loud, 120.0, None, "X").steps
    quiet_mean = sum(s.intensity for s in quiet_steps) / len(quiet_steps)
    loud_mean = sum(s.intensity for s in loud_steps) / len(loud_steps)
    assert loud_mean > quiet_mean + 3

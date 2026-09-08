from pathlib import Path
import re

from kiki_gateway.expressions import EXPRESSION_STATES, expression_prompt_note


ROOT = Path(__file__).resolve().parents[2]
THEME = ROOT / "firmware" / "main" / "kiki_theme.hpp"
UI = ROOT / "firmware" / "main" / "kiki_ui.cpp"
# The expression registry moved out of kiki_ui.cpp when state and pose were
# split apart: the panel state owns the list of names a mood may take,
# because that is exactly the set that must NOT disable the Stop button.
PANEL_STATE = ROOT / "firmware" / "main" / "kiki_panel_state.hpp"
FACE = ROOT / "firmware" / "main" / "kiki_face.cpp"

TOUCH_REACTIONS = {
    "tap_ouch_left",
    "tap_ouch_right",
    "tap_boop",
    "tap_pat",
    "tap_pinch",
    "tap_blaster",
    "tap_tumble",
}


def _luminance(hex_value: str) -> float:
    values = [int(hex_value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in values
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first: str, second: str) -> float:
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _palettes() -> dict[str, tuple[str, str, str]]:
    source = THEME.read_text()
    rows = re.findall(
        r'\{"([a-z_]+)",\s*0x([0-9A-F]{6}),\s*0x([0-9A-F]{6}),\s*0x([0-9A-F]{6})\}',
        source,
    )
    return {name: (background, mascot, accent) for name, background, mascot, accent in rows}


def test_every_pi_expression_has_exactly_one_esp32_palette_and_pose():
    palettes = _palettes()
    assert set(palettes) == EXPRESSION_STATES

    registry = re.search(
        r"kExpressionStates\[\]\s*=\s*\{(.*?)\};", PANEL_STATE.read_text(), re.DOTALL
    )
    assert registry is not None
    poses = set(re.findall(r'"([a-z_]+)"', registry.group(1)))
    assert poses == EXPRESSION_STATES


def test_persona_colours_remain_crisp_on_the_dim_amoled():
    for name, (background, mascot, accent) in _palettes().items():
        assert _contrast(background, mascot) >= 4.5, name
        assert _contrast(background, accent) >= 7.0, name


def test_every_expression_is_taught_to_every_persona_in_a_stable_order():
    note = expression_prompt_note()
    tags = re.findall(r"`<oled:([a-z_]+)>`", note.split("Available:", 1)[1].split(".\n", 1)[0])
    assert tags == sorted(EXPRESSION_STATES)
    assert "Never start a reply or sentence with one" in note


def test_every_body_hit_has_a_drawable_reaction_and_unique_sound():
    ui_source = UI.read_text()
    face_source = FACE.read_text()
    mapped_reactions = set(re.findall(r'\{"[^\"]+", "(tap_[a-z_]+)"', ui_source))
    drawn_reactions = set(
        re.findall(r'\{"(tap_[a-z_]+)", draw_[a-z_]+, \d+\}', face_source)
    )
    effects = re.findall(
        r'\{"[^\"]+", "tap_[a-z_]+", LocalEffect::([A-Za-z]+), \d+\}',
        ui_source,
    )

    assert mapped_reactions == TOUCH_REACTIONS
    assert drawn_reactions == TOUCH_REACTIONS
    assert len(effects) == len(TOUCH_REACTIONS)
    assert len(set(effects)) == len(effects)

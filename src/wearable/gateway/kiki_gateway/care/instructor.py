"""Explicit agent-to-panel animation contract. Never infer moves from speech."""

import math

MOVES = {
    "arm_raise": "Seated front arm raise: straight arms forward to shoulder height, then lower.",
    "lateral_raise": "Seated side arm raise: arms out sideways to just below shoulder height, then lower.",
    "elbow_curl": "Seated elbow curl: upper arms stay beside the torso; bend and straighten elbows, no weights.",
    "shoulder_roll": "Seated shoulder roll: arms relaxed, shoulders slowly up, back, down and forward.",
    "seated_march": "Seated march: lift a bent knee a little, then lower the foot; torso stays upright.",
    "torso_twist": "Seated torso turn: arms crossed on chest, feet and hips fixed, gently turn the upper body and return.",
}

GUIDANCE = """
## THE WATCH'S 3D INSTRUCTOR — YOU CONTROL IT
On every physical instruction supply `instructor`, matching precisely the ONE
movement in your spoken summary. Choose only from this animation catalogue:
""" + "\n".join(f"- {key}: {value}" for key, value in MOVES.items()) + """
All demos sit upright on a stable armless chair. Say that setup when needed.
`instructor` is {"move":"arm_raise","side":"both","pattern":"repeat",
"period_seconds":6}. Side is the person's anatomical left/right/both; the
instructor faces them (not mirrored). For both-sided marching and twists, sides
alternate. `period_seconds` is 4–12 seconds for one full out-and-back movement;
match the spoken pace. `pattern` is repeat or hold. Hold eases into the target
pose and stays there for hold_seconds; use left/right for a held march or twist.
Shoulder rolls are always repeat, never hold.
The demo starts AFTER your speech finishes and runs for hold_seconds only.
Use a positive hold_seconds for every demo. Each step needs a fresh command.
Use null during greetings, questions, rest, safety checks, or unsupported
exercises. Do not choose a roughly similar animation for a different exercise.
Prefer supported movements when the routine allows; never replace a prescribed
movement just to fit the catalogue. If it is unsupported, say there is no visual
demo for that movement. Never claim the demo is evidence of the person's form.
"""


def validate(value):
    if not isinstance(value, dict) or not isinstance(value.get("move"), str) \
            or value["move"] not in MOVES:
        return None
    side = value.get("side", "both")
    pattern = value.get("pattern", "repeat")
    if not isinstance(side, str) or not isinstance(pattern, str) \
            or side not in {"left", "right", "both"} or pattern not in {"repeat", "hold"}:
        return None
    if pattern == "hold" and side == "both" and value["move"] in {"seated_march", "torso_twist"}:
        return None
    if pattern == "hold" and value["move"] == "shoulder_roll":
        return None
    period = value.get("period_seconds", 6)
    if isinstance(period, bool) or not isinstance(period, (int, float)):
        return None
    if not math.isfinite(period) or not 4 <= period <= 12:
        return None
    return {"move": value["move"], "side": side, "pattern": pattern,
            "period_seconds": float(period)}

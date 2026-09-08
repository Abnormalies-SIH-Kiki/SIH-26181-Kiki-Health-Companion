from __future__ import annotations


# Speech stays sparse even though every semantic IMU state has a question bank.
# The firmware already debounces events; these longer gateway cooldowns prevent
# carrying or fidgeting with Kiki from turning into a stream of questions.
_QUESTION_COOLDOWNS: dict[str, float] = {
    "picked_up": 120.0,
    "upright_alert": 180.0,
    "face_down": 300.0,
    "upside_down": 300.0,
    "sideways": 300.0,
    "gentle_wiggle": 120.0,
    "shake": 180.0,
    "repeated_shake": 120.0,
    "dizzy_after_shake": 180.0,
    "rocking": 180.0,
    "spin": 180.0,
    "carried": 300.0,
    "bump": 180.0,
    "freefall": 300.0,
    "hard_landing": 300.0,
    "set_down": 180.0,
    "bored": 600.0,
    "very_bored": 600.0,
    "dozing": 900.0,
    "charging_rest": 900.0,
    "music_dance": 300.0,
    "low_battery_tired": 900.0,
    "wake_from_sleep": 300.0,
}


def motion_question_cooldown(event: str) -> float | None:
    """Return the speech cooldown for one journal-backed movement state."""

    return _QUESTION_COOLDOWNS.get(str(event or "").strip().lower())

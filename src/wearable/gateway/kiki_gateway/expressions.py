from __future__ import annotations

import re


# Exact parity with KikiFast core.oled_display.EXPRESSION_STATES and the ESP32
# pose/palette registries. Keeping this in a hardware-independent module lets
# the gateway build the model prompt without importing the Pi OLED manager.
EXPRESSION_STATES = frozenset({
    "love", "shy", "giggle", "wink", "excited", "curious", "proud", "sulk",
    "surprised", "sleepy", "idea", "mischief", "scared", "awe",
    "happy", "sad", "dizzy", "confused", "sleeping",
})
OLED_DISPLAY_TAG = re.compile(r"<oled:([A-Za-z_]{2,20})>", re.IGNORECASE)


def last_expression_tag(text: str) -> str | None:
    """Return the last valid Pi-compatible mood in one streamed sentence."""
    for name in reversed(OLED_DISPLAY_TAG.findall(text or "")):
        normalized = name.lower()
        if normalized in EXPRESSION_STATES:
            return normalized
    return None


def expression_prompt_note() -> str:
    """Static prompt suffix teaching every persona the drawable vocabulary."""
    names = ", ".join(f"`<oled:{name}>`" for name in sorted(EXPRESSION_STATES))
    return (
        "\n\n## YOUR FACE (silent AMOLED expression tags)\n"
        "You have a small round screen showing your crab face. Set its expression "
        f"with a silent tag inline in your reply. Available: {names}.\n"
        "- The tag is NOT spoken and NOT part of your words; it only changes your face.\n"
        "- Never start a reply or sentence with one. Put it after the first few words "
        "or at the end of a sentence, so the face never delays your voice.\n"
        "- Use at most one per sentence, when your words genuinely carry that mood. "
        "It remains visible until you choose another expression or stop talking.\n"
        "- Examples: `Oh! <oled:surprised> I did not expect that.` / "
        "`That is really sweet <oled:shy> anyway, moving on.`"
    )

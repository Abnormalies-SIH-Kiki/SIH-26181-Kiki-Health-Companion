"""Keep Kiki's configured Bluetooth speaker as the PulseAudio output.

PulseAudio/PipeWire creates ``auto_null`` when the Bluetooth sink disappears.
That dummy sink can remain the default even after BlueZ reconnects, so callers
must not rely on ``@DEFAULT_SINK@`` repairing itself.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time


_MAC_RE = re.compile(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$")
_repair_lock = threading.Lock()


def _run(command: list[str], timeout: float = 3.0):
    try:
        return subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None


def _names(kind: str) -> list[str]:
    result = _run(["pactl", "list", "short", kind])
    if result is None or result.returncode != 0:
        return []
    names = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2:
            names.append(fields[1])
    return names


def configured_sink(settings: dict) -> str:
    """Return the live A2DP sink matching the configured Bluetooth MAC."""
    mac = str(settings.get("mac", "")).strip().upper()
    if not _MAC_RE.fullmatch(mac):
        return ""
    token = mac.replace(":", "_").casefold()
    matches = [name for name in _names("sinks") if token in name.casefold()]
    if not matches:
        return ""
    return next(
        (name for name in matches if "a2dp" in name.casefold()),
        matches[0],
    )


def _activate_a2dp_profile(settings: dict) -> None:
    """Best-effort profile activation for a connected card with no sink."""
    mac = str(settings.get("mac", "")).strip().upper()
    if not _MAC_RE.fullmatch(mac):
        return
    token = mac.replace(":", "_").casefold()
    card = next(
        (name for name in _names("cards") if token in name.casefold()),
        "",
    )
    if not card:
        return
    # PulseAudio and PipeWire-Pulse use different spellings.
    for profile in ("a2dp_sink", "a2dp-sink"):
        result = _run(["pactl", "set-card-profile", card, profile])
        if result is not None and result.returncode == 0:
            return


def ensure_bluetooth_sink(
    settings: dict,
    *,
    reconnect: bool = True,
    timeout_seconds: float = 6.0,
) -> str:
    """Select and return Kiki's A2DP sink, repairing ``auto_null`` if needed.

    The returned name can also be passed as ``PULSE_SINK`` to pin a child
    process to the speaker even if another process changes the global default.
    """
    if not settings.get("enabled", True):
        return ""
    mac = str(settings.get("mac", "")).strip().upper()
    if not _MAC_RE.fullmatch(mac):
        return ""

    with _repair_lock:
        sink = configured_sink(settings)
        if not sink:
            # A connected BlueZ device may exist only as a card after the
            # Pulse/PipeWire daemon restarts. Activating A2DP creates its sink
            # without requiring a disruptive Bluetooth reconnect.
            _activate_a2dp_profile(settings)
            sink = configured_sink(settings)
        if not sink and reconnect:
            _run(["bluetoothctl", "power", "on"], timeout=4)
            _run(["bluetoothctl", "connect", mac], timeout=12)
            _activate_a2dp_profile(settings)

            deadline = time.monotonic() + max(0.0, timeout_seconds)
            while not sink and time.monotonic() <= deadline:
                sink = configured_sink(settings)
                if sink:
                    break
                time.sleep(0.25)

        if not sink:
            return ""

        result = _run(["pactl", "set-default-sink", sink])
        if result is None or result.returncode != 0:
            return ""
        return sink


def playback_environment(sink: str) -> dict[str, str]:
    """Environment for an audio child process pinned to an exact Pulse sink."""
    env = os.environ.copy()
    if sink:
        env["PULSE_SINK"] = sink
    return env

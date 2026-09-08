from types import SimpleNamespace

import pytest

from kiki_gateway.device_tools import DeviceToolBridge
from kiki_gateway.session import DeviceSession


class Session:
    def __init__(self):
        self.events = []
        self.playing = False

    async def send_event(self, kind, **fields):
        self.events.append((kind, fields))


@pytest.mark.asyncio
async def test_volume_is_applied_on_device(tmp_path):
    session = Session()
    bridge = DeviceToolBridge(session, str(tmp_path))
    assert await bridge.execute("adjust_volume", {"action": "set", "amount": 125}) == "Volume set to 100 percent."
    assert session.events == [("volume", {"percent": 100})]


@pytest.mark.asyncio
async def test_mode_voice_is_used_until_voice_is_manually_overridden(tmp_path):
    bridge = DeviceToolBridge(Session(), str(tmp_path))
    session = object.__new__(DeviceSession)
    session.device = bridge
    session.core = SimpleNamespace(current_voice=lambda: "rohan")

    assert bridge.voice is None
    assert session._current_tts_voice() == "rohan"

    bridge.voice = "jarvis"
    assert session._current_tts_voice() == "jarvis"

    # Empty is an intentional request for the TTS server's default voice; it
    # must remain distinct from None (follow the mode).
    assert await bridge._switch_voice("default") == "Voice reset to the default."
    assert bridge.voice == ""
    assert session._current_tts_voice() == ""


def test_timer_duration_parser():
    assert DeviceToolBridge._duration_seconds("2 minutes") == 120
    assert DeviceToolBridge._duration_seconds("1.5 hours") == 5400
    assert DeviceToolBridge._duration_seconds("five minutes") == 300
    assert DeviceToolBridge._duration_seconds(15) == 15

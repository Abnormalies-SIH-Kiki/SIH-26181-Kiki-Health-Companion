from types import SimpleNamespace

import pytest

from kiki_gateway.audio_control import AudioControlState


class Session:
    def __init__(self):
        self.tts = SimpleNamespace(gain=1.0)
        self.device = SimpleNamespace(volume=0)
        self.speaker_volume = 0
        self.playing = False
        self.events = []
        self.spoken = []

    async def send_event(self, kind, **fields):
        self.events.append((kind, fields))

    async def speak_background(self, text):
        self.spoken.append(text)


@pytest.mark.asyncio
async def test_runtime_audio_controls_update_connected_device():
    state = AudioControlState(3.2, 100)
    session = Session()
    state.attach(session)
    assert session.tts.gain == 3.2
    assert session.speaker_volume == 100
    assert session.device.volume == 100

    await state.execute({"action": "gain", "value": 3.8})
    await state.execute({"action": "volume", "value": 85})
    result = await state.execute({"action": "speak", "text": "hello"})
    await next(iter(state._speech_tasks))

    assert result["queued"] == 1
    assert session.tts.gain == 3.8
    assert session.events == [("volume", {"percent": 85})]
    assert session.device.volume == 85
    assert session.spoken == ["hello"]


@pytest.mark.asyncio
async def test_runtime_audio_controls_reject_unsafe_ranges():
    state = AudioControlState(3.2, 100)
    with pytest.raises(ValueError, match="gain"):
        await state.execute({"action": "gain", "value": 6.1})
    with pytest.raises(ValueError, match="volume"):
        await state.execute({"action": "volume", "value": -1})

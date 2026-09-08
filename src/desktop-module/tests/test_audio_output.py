from types import SimpleNamespace
from unittest.mock import patch

from core import audio_output


SETTINGS = {
    "enabled": True,
    "mac": "EF:C0:B9:99:69:9C",
}
SINK = "bluez_sink.EF_C0_B9_99_69_9C.a2dp_sink"


def completed(stdout="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


def test_selects_configured_sink_when_default_is_dummy_output():
    def run(command, timeout=3.0):
        if command[:4] == ["pactl", "list", "short", "sinks"]:
            return completed(f"0 auto_null module-null-sink.c s16le 2ch 44100Hz IDLE\n"
                             f"1 {SINK} module-bluez5-device.c s16le 2ch 44100Hz RUNNING\n")
        return completed()

    with patch.object(audio_output, "_run", side_effect=run) as mocked:
        assert audio_output.ensure_bluetooth_sink(
            SETTINGS, reconnect=False) == SINK

    assert ["pactl", "set-default-sink", SINK] in [
        call.args[0] for call in mocked.call_args_list
    ]


def test_playback_environment_pins_child_to_selected_sink():
    with patch.dict(audio_output.os.environ, {}, clear=True):
        assert audio_output.playback_environment(SINK)["PULSE_SINK"] == SINK


def test_activates_a2dp_profile_when_card_exists_without_sink():
    sink_reads = iter([[], [SINK]])

    with (
        patch.object(audio_output, "_names",
                     side_effect=lambda kind: (
                         next(sink_reads) if kind == "sinks"
                         else ["bluez_card.EF_C0_B9_99_69_9C"]
                     )),
        patch.object(audio_output, "_run", return_value=completed()) as run,
    ):
        assert audio_output.ensure_bluetooth_sink(
            SETTINGS, reconnect=False) == SINK

    assert [
        "pactl", "set-card-profile",
        "bluez_card.EF_C0_B9_99_69_9C", "a2dp_sink",
    ] in [call.args[0] for call in run.call_args_list]

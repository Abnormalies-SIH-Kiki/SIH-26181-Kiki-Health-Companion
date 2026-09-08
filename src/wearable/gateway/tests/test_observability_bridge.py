import importlib.util
from pathlib import Path

from kiki_gateway.observability_bridge import ObservabilityBridge


def recorder(tmp_path):
    path = Path(__file__).parents[1] / "legacy_kiki/core/observability.py"
    spec = importlib.util.spec_from_file_location("isolated_recorder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._LOG_DIR = str(tmp_path)
    module._EVENTS_PATH = str(tmp_path / "events.jsonl")
    return module.Recorder()


def test_chat_context_and_reply_reach_existing_webui_recorder(tmp_path):
    rec = recorder(tmp_path)
    bridge = ObservabilityBridge(rec)
    sid = bridge.start_turn("Can you hear me?")
    messages = [{"role": "user", "content": "Can you hear me?"}]
    bridge.context(messages)
    bridge.finish_turn(sid, "Yes, I can.", 900)
    events = rec.get_events()
    assert events[-2]["meta"]["last_user"] == "Can you hear me?"
    assert events[-1]["meta"]["response"] == "Yes, I can."
    assert events[-1]["meta"]["status"] == "done"
    assert rec.get_latest_context()["messages"] == messages
    assert rec.get_events(since_id=9000) == events


def test_cancelled_reply_is_marked_cancelled(tmp_path):
    rec = recorder(tmp_path)
    bridge = ObservabilityBridge(rec)
    sid = bridge.start_turn("tell a story")
    bridge.finish_turn(sid, "Once upon", 400, cancelled=True)
    assert rec.get_events()[-1]["meta"]["status"] == "cancelled"


def test_broken_recorder_cannot_break_voice():
    class Broken:
        def record(self, *a, **k):
            return None
        def start_session(self, *a, **k):
            raise RuntimeError("recorder failed")
        def set_context(self, *a, **k):
            raise RuntimeError("recorder failed")
    bridge = ObservabilityBridge(Broken())
    assert bridge.start_turn("hello") is None
    assert bridge.context([]) is None
    assert bridge.finish_turn(None, "reply", 100) is None

import json

from core.brain.ambient_listening import AmbientListeningManager


def make_config(tmp_path, **overrides):
    settings = {
        "min_batch_words": 3,
        "max_buffer_sentences": 10,
        "buffer_file": str(tmp_path / "ambient-buffer.json"),
    }
    settings.update(overrides)
    return {"always_listen": True, "always_listen_config": settings}


def test_capture_is_crash_safe_and_consumed_by_id(tmp_path):
    cfg = make_config(tmp_path)
    manager = AmbientListeningManager(cfg)
    assert manager.add_sentence("We should leave for the airport soon.")
    item = manager.snapshot()[0]

    restored = AmbientListeningManager(cfg)
    assert restored.pending_count == 1
    assert restored.snapshot()[0]["text"] == item["text"]
    assert restored.consume([item["id"]]) == 1
    assert restored.pending_count == 0
    assert json.loads((tmp_path / "ambient-buffer.json").read_text()) == []


def test_snapshot_limit_keeps_newest_items(tmp_path):
    manager = AmbientListeningManager(make_config(tmp_path))
    for index in range(4):
        manager.add_sentence(f"Ambient sentence number {index}")
    assert [
        item["text"] for item in manager.snapshot(2)
    ] == [
        "Ambient sentence number 2",
        "Ambient sentence number 3",
    ]


def test_disabled_mode_does_not_buffer(tmp_path):
    manager = AmbientListeningManager({
        "always_listen": False,
        "always_listen_config": {
            "buffer_file": str(tmp_path / "disabled.json")
        },
    })
    assert manager.add_sentence("This should not be recorded.") is False
    assert manager.pending_count == 0

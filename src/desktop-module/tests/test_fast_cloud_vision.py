"""Direct Cerebras/Gemma image-input contracts for the care agent."""

from core.brain import fast_cloud


def test_cerebras_image_uses_chat_completions_multimodal_shape(monkeypatch):
    captured = {}

    def post(url, key, body, timeout):
        captured.update({"url": url, "body": body})
        return {
            "choices": [{"message": {"content": '{"status":"completed"}'}}],
            "usage": {"prompt_tokens": 300, "completion_tokens": 5,
                      "image_tokens": 264},
        }

    monkeypatch.setenv("CEREBRAS_API_KEY", "test-key")
    monkeypatch.setattr(fast_cloud, "_post", post)
    result = fast_cloud._call_cerebras(
        "inspect this", fast_cloud._cfg(), image_b64="aW1hZ2U=")

    assert result == '{"status":"completed"}'
    content = captured["body"]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "inspect this"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == (
        "data:image/jpeg;base64,aW1hZ2U=")
    assert captured["url"].endswith("/chat/completions")


def test_image_is_never_silently_dropped_to_text_only_fallback(monkeypatch):
    seen = []

    def cerebras(_prompt, _cfg, **_kwargs):
        seen.append("cerebras")
        raise RuntimeError("down")

    def groq(_prompt, _cfg, **_kwargs):
        seen.append("groq")
        raise RuntimeError("must reject image")

    monkeypatch.setattr(
        fast_cloud, "_PROVIDERS", {"cerebras": cerebras, "groq": groq})

    try:
        fast_cloud.complete("look", image_b64="aW1hZ2U=")
    except fast_cloud.FastCloudUnavailable as exc:
        assert "refusing" in str(exc) or "must reject" in str(exc)
    else:
        raise AssertionError("multimodal failure must not become a blind answer")
    assert seen == ["cerebras", "groq"]

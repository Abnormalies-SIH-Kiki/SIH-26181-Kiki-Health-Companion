from core.workers.worker_brain import (
    _WORKER_BLOCKED_TOOLS,
    _worker_tool_guard,
)
from tools_and_config.tools import get_detailed_tool_descriptions


def test_ordinary_workers_hide_and_block_removed_tools():
    descriptions = get_detailed_tool_descriptions(
        excluded_names=_WORKER_BLOCKED_TOOLS)

    for name in _WORKER_BLOCKED_TOOLS:
        assert f"- {name}:" not in descriptions
        allowed, reason = _worker_tool_guard(name, {}, {}, 0, ())
        assert not allowed
        assert "unavailable" in reason

    assert "- search_web:" in descriptions
    assert _worker_tool_guard("search_web", {}, {}, 0, ())[0]

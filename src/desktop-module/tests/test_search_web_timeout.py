"""
Regression test for the "Tool execution timed out after 15.0s" voice bug.

When the local box made a search_web tool call and the network was flaky, the
whole speaking turn stalled until main.py's 15s TOOL_EXEC_TIMEOUT ceiling and
the answer was spoken *without* the search result.

Root cause (confirmed by reproduction): search_web's blocking exa.search() ran
on the event loop's DEFAULT executor. When asyncio.wait_for(timeout=12s) fired,
asyncio.run() — which execute_tool() uses to drive the coroutine from a worker
thread — then blocked in loop.shutdown_default_executor() JOINING the orphaned,
still-hung exa.search thread. So the 12s ceiling was real but invisible: the
result Event was never set and main.py's 15s ceiling tripped every time.

The fix runs the blocking call on a DEDICATED executor (never joined by
asyncio.run teardown) plus a real socket timeout on the Exa request. These tests
drive the ACTUAL production path: tools.execute_tool("search_web", ...) exactly
as core/llm.execute_tool_calls -> main.py's run_tools_bg thread calls it.
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools_and_config.tools as tools


class _HangingExa:
    """Stand-in Exa client whose .search() hangs forever (dead network)."""

    def __init__(self):
        self.entered = threading.Event()
        self._never = threading.Event()  # never set

    def search(self, *a, **kw):
        self.entered.set()
        self._never.wait()  # block until the process dies


def _run_execute_tool_in_thread(timeout_join):
    """Mimic main.py's run_tools_bg: call the SYNC execute_tool from a plain
    thread (no running loop), which routes through asyncio.run()."""
    box = {"result": None, "done": False}

    def worker():
        box["result"] = tools.execute_tool("search_web", {"query": "news today"})
        box["done"] = True

    th = threading.Thread(target=worker, daemon=True)
    t0 = time.time()
    th.start()
    th.join(timeout=timeout_join)
    return box, th, time.time() - t0


def test_hung_search_returns_before_main_ceiling(monkeypatch):
    """A hung search_web must return well before main.py's 15s TOOL_EXEC_TIMEOUT
    so the result Event gets set and the turn doesn't stall."""
    # Short ceiling so the test is fast; the bug is independent of the value.
    monkeypatch.setattr(tools, "_SEARCH_TIMEOUT_S", 1.0)
    hanging = _HangingExa()
    monkeypatch.setattr(tools, "_exa_client", hanging)

    try:
        box, th, elapsed = _run_execute_tool_in_thread(timeout_join=5.0)

        assert hanging.entered.wait(2.0), "exa.search was never invoked"
        # The critical assertion: execute_tool RETURNED (the result Event would be
        # set), and did so promptly — not hung in asyncio.run teardown joining the
        # orphan thread, which is the production 15s-timeout bug.
        assert box["done"], (
            f"execute_tool did not return within 5s (still hung at {elapsed:.1f}s) "
            "— this is the production 15s-timeout bug."
        )
        assert elapsed < 3.0, f"returned but too slowly ({elapsed:.1f}s)"
        assert "timed out" in box["result"].lower()
    finally:
        # Real Exa honours the socket timeout so its worker thread dies; this fake
        # ignores it, so release the orphan or the dedicated pool's non-daemon
        # thread would block interpreter exit at the end of the suite.
        hanging._never.set()


def test_search_uses_hard_bounded_future_result():
    """Guard the fix: the blocking Exa call must run on the dedicated executor
    via submit(), and be awaited with Future.result(timeout=...) — the HARD
    bound that returns at the deadline without joining the worker. asyncio's
    wait_for/run_in_executor over the blocking call must NOT wrap it (it can't
    cancel the thread, so asyncio.run teardown joins the orphan and stalls)."""
    import inspect
    src = inspect.getsource(tools.search_web)
    assert "_SEARCH_EXECUTOR.submit" in src, \
        "blocking search must be submitted to the dedicated executor"
    assert ".result(timeout=_SEARCH_TIMEOUT_S)" in src, \
        "search must use Future.result(timeout) for a hard wall-clock bound"


def test_hard_bound_holds_even_if_socket_timeout_ignored():
    """The strongest guarantee: even if the underlying call ignores every
    timeout and hangs forever, search_web still returns at the deadline."""
    import asyncio as _asyncio

    class _ForeverExa:
        def __init__(self):
            self._never = threading.Event()

        def search(self, *a, **kw):
            self._never.wait()  # ignores socket timeout entirely

    forever = _ForeverExa()

    orig = tools._SEARCH_TIMEOUT_S
    tools._SEARCH_TIMEOUT_S = 1.0
    tools._exa_client = forever
    try:
        t0 = time.time()
        res = _asyncio.run(tools.search_web("anything"))
        elapsed = time.time() - t0
        assert elapsed < 2.5, f"hard bound failed: returned at {elapsed:.1f}s"
        assert "timed out" in res.lower()
    finally:
        tools._SEARCH_TIMEOUT_S = orig
        tools._exa_client = None
        forever._never.set()  # release the abandoned worker


def test_exa_request_timeout_patch_is_scoped_and_idempotent():
    """The socket-timeout patch must wrap Exa's requests namespace without
    touching the global requests module (which the llama.cpp path relies on)."""
    try:
        from exa_py import Exa
    except Exception:
        import pytest
        pytest.skip("exa_py not installed")

    import requests as global_requests
    tools._patch_exa_request_timeout()
    g = Exa.request.__globals__
    assert getattr(g["requests"], "_kiki_timeout_patched", False)
    # Global requests module is untouched (speaking path safety).
    assert not getattr(global_requests, "_kiki_timeout_patched", False)
    # Idempotent: a second call doesn't double-wrap.
    first = g["requests"]
    tools._patch_exa_request_timeout()
    assert g["requests"] is first


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))

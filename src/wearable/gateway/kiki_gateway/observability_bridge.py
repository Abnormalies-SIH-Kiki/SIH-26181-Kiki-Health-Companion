"""Publish ESP32 conversations to the existing desktop Web UI recorder."""

import itertools
import time
import functools
import logging

LOG = logging.getLogger(__name__)


def best_effort(method):
    @functools.wraps(method)
    def guarded(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except Exception:
            LOG.warning("Web UI recording unavailable", exc_info=True)
            return None
    return guarded


class ObservabilityBridge:
    def __init__(self, recorder):
        self.recorder = recorder
        # The browser keeps its last event id across a gateway restart. Small
        # process-local ids leave it polling past every new event indefinitely.
        recorder._ids = itertools.count(time.time_ns() // 1_000_000)
        recorder.record("gateway", name="startup", source="esp32")

    @best_effort
    def start_turn(self, text):
        self.recorder.record("turn", name="start", phase="start", last_user=text)
        return self.recorder.start_session("turn", name="speaking", last_user=text)

    @best_effort
    def context(self, messages):
        self.recorder.set_context(messages, message_count=len(messages), source="esp32")

    @best_effort
    def finish_turn(self, sid, response, elapsed_ms, cancelled=False):
        status = "cancelled" if cancelled else "done"
        self.recorder.record("turn", name="reply", phase="end",
                             duration_ms=elapsed_ms, response=response, status=status)
        self.recorder.log_step(sid, "reply", content=response, status=status)
        self.recorder.end_session(sid, status=status, response=response)

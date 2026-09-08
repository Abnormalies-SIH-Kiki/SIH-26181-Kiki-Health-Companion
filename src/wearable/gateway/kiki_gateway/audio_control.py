from __future__ import annotations

import asyncio
import json
from typing import Any


DEFAULT_CALIBRATION_TEXT = (
    "[confirmation-en] Hi Vaibhav. This is Kiki testing my speaker volume "
    "and digital voice gain. How does this level sound?"
)


class AudioControlState:
    """Runtime-only audio calibration shared by connected ESP32 sessions."""

    def __init__(self, gain: float, volume: int, sync_offset_ms: int = 120):
        self.gain = float(gain)
        self.volume = max(0, min(100, int(volume)))
        self.sync_offset_ms = int(sync_offset_ms)
        self.sessions: set[Any] = set()
        self._speech_tasks: set[asyncio.Task] = set()

    def attach(self, session: Any) -> None:
        session.tts.gain = self.gain
        session.speaker_volume = self.volume
        self._apply_sync(session)
        if session.device:
            session.device.volume = self.volume
        self.sessions.add(session)

    def detach(self, session: Any) -> None:
        self.sessions.discard(session)

    async def execute(self, request: dict) -> dict:
        action = str(request.get("action", "status")).strip().lower()
        if action == "status":
            return self.status()
        if action == "gain":
            gain = float(request["value"])
            if not 0.0 <= gain <= 6.0:
                raise ValueError("digital gain must be between 0.0 and 6.0")
            self.gain = gain
            for session in self.sessions:
                session.tts.gain = gain
            return self.status()
        if action == "volume":
            volume = int(request["value"])
            if not 0 <= volume <= 100:
                raise ValueError("speaker volume must be between 0 and 100")
            self.volume = volume
            for session in self.sessions:
                session.speaker_volume = volume
                if session.device:
                    session.device.volume = volume
            await asyncio.gather(
                *(session.send_event("volume", percent=volume) for session in self.sessions)
            )
            return self.status()
        if action == "sync":
            # Caption timing. The acoustic loopback KikiFast calibrates with
            # cannot run here -- the board mutes its microphone while speaking --
            # so this is dialled in by ear instead, live, the same way gain is.
            offset = int(request["value"])
            if not -2000 <= offset <= 5000:
                raise ValueError("sync offset must be between -2000 and 5000 ms")
            self.sync_offset_ms = offset
            for session in self.sessions:
                self._apply_sync(session)
            return self.status()
        if action == "speak":
            text = str(request.get("text") or DEFAULT_CALIBRATION_TEXT).strip()
            if not text:
                raise ValueError("speech text cannot be empty")
            queued = 0
            for session in tuple(self.sessions):
                if session.playing:
                    continue
                task = asyncio.create_task(session.speak_background(text))
                self._speech_tasks.add(task)
                task.add_done_callback(self._speech_tasks.discard)
                queued += 1
            result = self.status()
            result["queued"] = queued
            if not self.sessions:
                result["message"] = "no ESP32 is connected"
            elif not queued:
                result["message"] = "ESP32 is already playing audio"
            return result
        if action == "dance":
            # The same affordance `speak` is, for the other thing that is hard
            # to trigger without standing in front of the board: it starts a
            # real dance, through the real tool, without anyone having to say
            # the word out loud. Deliberately fire-and-forget -- preparing one
            # takes several seconds and the caller should not hold the socket
            # open for it.
            request_text = str(request.get("text") or "dance").strip()
            queued = 0
            for session in tuple(self.sessions):
                if session.playing or getattr(session, "dance_active", False):
                    continue
                device = getattr(session, "device", None)
                if device is None:
                    continue
                task = asyncio.create_task(
                    device.execute("dance", {"request": request_text})
                )
                self._speech_tasks.add(task)
                task.add_done_callback(self._speech_tasks.discard)
                queued += 1
            result = self.status()
            result["queued"] = queued
            if not self.sessions:
                result["message"] = "no ESP32 is connected"
            elif not queued:
                result["message"] = "ESP32 is busy or already dancing"
            return result
        raise ValueError(f"unknown action: {action}")

    def _apply_sync(self, session: Any) -> None:
        """GatewayConfig is frozen, so swap in a copy carrying the new offset."""
        from dataclasses import replace

        config = getattr(session, "config", None)
        if config is None:
            return
        session.config = replace(config, display_sync_offset_ms=self.sync_offset_ms)

    def status(self) -> dict:
        return {
            "ok": True,
            "gain": self.gain,
            "volume": self.volume,
            "sync_offset_ms": self.sync_offset_ms,
            "devices": len(self.sessions),
        }

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if len(raw) > 16_384:
                raise ValueError("request is too large")
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("request must be a JSON object")
            response = await self.execute(request)
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        writer.write((json.dumps(response, separators=(",", ":")) + "\n").encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()

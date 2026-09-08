from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import tempfile


LOG = logging.getLogger(__name__)


class ConfigStore:
    """The boot wizard's questions, answered against the legacy config.json.

    KikiFast asks these on the Pi's own LCD before any service starts
    (core/startup_config.py). Here the answers live on the laptop, so the board
    cannot ask them alone: it requests the options, shows them, and sends the
    choices back.

    Three questions change meaning on this hardware and are adapted rather than
    dropped:

    * Bluetooth speaker volume becomes the board's codec volume; there is no
      Bluetooth speaker in this route.
    * "Sync LCD+audio" becomes the caption delay. The Pi calibrates word timing
      by measuring speaker-to-microphone latency; that cannot run here because
      the board mutes its microphone while speaking, so the equivalent knob is
      set directly instead of measured.
    * Wi-Fi is handled entirely on the board, which owns the radio.
    """

    # Language used to be here too, but it is applied by rebuilding the system
    # prompt, which the session now does on commit -- so the only thing that
    # still needs the runtime rebuilt is which brain generates the speech.
    RESTART_REQUIRED = {"provider"}

    def __init__(self, legacy_root: str):
        self.root = Path(legacy_root)
        self.path = self.root / "tools_and_config" / "config.json"
        self._pending: dict[str, object] = {}

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except Exception:
            LOG.warning("could not read %s", self.path, exc_info=True)
            return {}

    def options(self, volume: int, gain: float, sync_ms: int) -> dict:
        """The question list, each with its choices and the current answer."""
        config = self._load()
        llm = config.get("llm", {}) or {}
        modes_cfg = config.get("assistant_modes", {}) or {}
        modes = modes_cfg.get("modes", {}) or {}
        mode_names = list(modes) if isinstance(modes, dict) and modes else ["default"]
        return {
            "provider": {
                "label": "Speaking brain",
                "choices": ["local", "cerebras"],
                "current": str(llm.get("speaking_provider", "local")),
            },
            "language": {
                "label": "Language",
                "choices": ["english", "hindi"],
                "current": str(modes_cfg.get("language_on_startup", "english")),
            },
            "mode": {
                "label": "Startup mode",
                "choices": mode_names,
                "current": str(modes_cfg.get("active_on_startup", "default")),
            },
            "volume": {"label": "Volume", "current": int(volume)},
            "gain": {"label": "Digital gain", "current": round(float(gain), 2)},
            "sync": {"label": "Caption delay", "current": int(sync_ms)},
        }

    def stage(self, key: str, value: object) -> bool:
        """Record an answer. Returns True when it needs a gateway restart."""
        self._pending[key] = value
        return key in self.RESTART_REQUIRED

    def commit(self) -> dict:
        """Write the staged answers atomically, as the Pi's wizard does."""
        if not self._pending:
            return {"saved": False, "restart_required": False}
        config = self._load()
        if not config:
            return {"saved": False, "restart_required": False, "error": "config unreadable"}

        llm = config.setdefault("llm", {})
        modes = config.setdefault("assistant_modes", {})
        for key, value in self._pending.items():
            if key == "provider":
                llm["speaking_provider"] = str(value)
            elif key == "language":
                modes["language_on_startup"] = str(value)
            elif key == "mode":
                modes["active_on_startup"] = str(value)
            elif key == "volume":
                # No Bluetooth speaker here, but keep the same key so a config
                # shared with the Pi still means the same thing.
                config.setdefault("bluetooth_speaker", {})["volume_percent"] = int(value)
            elif key == "gain":
                config.setdefault("tts", {})["esp32_digital_gain"] = float(value)
            elif key == "sync":
                config.setdefault("tts", {})["esp32_caption_delay_ms"] = int(value)

        restart = any(key in self.RESTART_REQUIRED for key in self._pending)
        try:
            self._write_atomic(config)
        except Exception as exc:
            LOG.exception("could not save config")
            return {"saved": False, "restart_required": False, "error": str(exc)}
        LOG.info("startup config saved: %s", sorted(self._pending))
        answered = dict(self._pending)
        self._pending.clear()
        return {"saved": True, "restart_required": restart, "answers": answered}

    def _write_atomic(self, config: dict) -> None:
        """Same-directory temp file plus fsync, then rename.

        config.json is the file the whole runtime reads; a half-written one
        after a power cut would take Kiki down entirely, and this box has
        already lost files to exactly that.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", dir=self.path.parent, prefix=".config.", suffix=".tmp", delete=False
        )
        try:
            with handle:
                json.dump(config, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, self.path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise

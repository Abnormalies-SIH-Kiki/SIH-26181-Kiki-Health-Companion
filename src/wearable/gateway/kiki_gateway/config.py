from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class GatewayConfig:
    bind_host: str = "0.0.0.0"
    bind_port: int = 8765
    auth_token: str = ""
    llama_url: str = "http://127.0.0.1:8080/v1/chat/completions"
    whisper_url: str = "http://127.0.0.1:5555/inference"
    tts_url: str = "http://127.0.0.1:8082/v1/audio/speech"
    legacy_root: str = ""
    model_path: str = str(Path(__file__).resolve().parent.parent / "models" / "silero_vad.onnx")
    vad_threshold: float = 0.50
    min_speech_ms: int = 250
    speculative_silence_ms: int = 240
    endpoint_silence_ms: int = 600
    partial_asr_interval_ms: int = 1000
    max_utterance_seconds: float = 20.0
    preroll_ms: int = 250
    near_field_enabled: bool = True
    near_field_engage_dbfs: float = -50.0
    near_field_open_margin_db: float = 9.0
    near_field_close_margin_db: float = 4.0
    # Kiki is woken by her name appearing in Whisper's TEXT, not by an acoustic
    # model listening for it (see hotword_text.py). Whisper already runs over
    # every utterance here, so this costs no extra inference -- and it is the
    # only way a hotword can arrive at the END of the sentence it addresses.
    hotword_enabled: bool = True
    # The names to answer to when the active mode does not choose its own. A
    # mode's own list (assistant_modes.modes.<mode>.hotwords, or its voice
    # name) replaces this entirely -- "rohan" answers to rohan, not to kiki.
    hotwords: str = "kiki"
    hotword_similarity: float = 0.75
    hotword_min_fuzzy_length: int = 4
    # Transcribe idle speech at the speculative endpoint (240 ms) rather than
    # waiting for the full one (600 ms). The transcript is reused by the commit
    # path, so this is not a second Whisper call -- it is the same call, moved
    # earlier, and it is what keeps text hotwording as fast as the acoustic
    # model it replaces.
    hotword_speculative: bool = True
    # "what's the capital of france" ... "what do you think kiki" arrives as
    # two separate utterances, and the second one alone is not a question.
    # Recent idle speech is carried into the query when the hotword lands at
    # the end of a short utterance -- see session._carry_back_text.
    hotword_carry_back_seconds: float = 25.0
    hotword_carry_back_max_utterances: int = 3
    hotword_carry_back_max_lead_words: int = 6
    hotword_carry_back_max_trail_words: int = 2
    # The openWakeWord model this replaced. Kept switchable rather than
    # deleted: it is the one detector that works when Whisper is down.
    wakeword_enabled: bool = False
    # Voice barge-in is deliberately stricter than ordinary endpointing. It
    # only runs on firmware frames marked both AEC-clean and device-VAD speech,
    # then asks an independent Silero instance for sustained near-field votes.
    barge_in_enabled: bool = True
    barge_in_guard_ms: int = 600
    barge_in_vad_threshold: float = 0.82
    barge_in_min_dbfs: float = -42.0
    barge_in_noise_margin_db: float = 6.0
    barge_in_window_frames: int = 10
    barge_in_min_votes: int = 6
    barge_in_min_consecutive: int = 3
    tts_output_rate: int = 48000
    tts_gain: float = 3.2
    speaker_volume: int = 100
    # Music is NOT boosted the way speech is. tts_gain exists because
    # OmniVoice output is quiet (-21.65 dBFS RMS raw); a YouTube track is
    # already mastered close to the ceiling, so the same 3.2x would spend the
    # whole reply on the limiter and sound crushed. 1.0 is a true bypass --
    # amplify_pcm_s16 returns the buffer untouched -- and this exists so the
    # balance between Kiki's voice and the music is tunable rather than
    # accidental.
    media_gain: float = 1.0
    # How far ahead of real time the device is allowed to be buffered. This is
    # pure jitter tolerance and costs nothing in time-to-first-word: pacing only
    # ever delays *later* frames, and the lead accumulates from zero because
    # generation outruns playback. At the previous 0.2s any Wi-Fi stall longer
    # than 200 ms emptied the device ring and clicked.
    playback_lead_seconds: float = 0.6
    # ...and how far ahead on a link that stalls. Raising the lead is the ONLY
    # way to add jitter tolerance that does not cost time-to-first-word: pacing
    # delays later frames and never the first one, which is why LATENCY.md
    # records 0.2 -> 0.6 costing nothing. The board's start gate is what would
    # cost TTFW, and it is now pinned at its floor precisely so this number can
    # carry the whole cushion.
    #
    # It only becomes reachable with a compressed stream: at 256 kbps PCM,
    # 2.5 s of cushion is 80 KB and takes 2.5 s of wire time to fill on a
    # saturated link, so it would never actually arrive.
    playback_lead_seconds_remote: float = 2.5
    # Music has no latency requirement at all -- nobody notices a song starting
    # 200 ms later, and everybody notices it stuttering.
    media_lead_seconds: float = 2.0
    media_lead_seconds_remote: float = 4.0
    # A board that reports a stuttered reply may ask for more cushion, up to
    # this. It asks rather than deepening its own start gate, because its start
    # gate is TTFW and this is not.
    playback_lead_seconds_max: float = 5.0
    # Opus bitrate for Kiki's voice on a remote link, in bits per second.
    # 32 kbps 16 kHz mono is transparent for speech -- it is well above
    # the rate at which Opus stops being distinguishable from the PCM it
    # replaces -- while being eight times smaller on the wire. Lower it
    # only if a link cannot carry even this.
    opus_bitrate: int = 32000
    # Master switch for the speech codec. Off falls straight back to 16 kHz
    # PCM with no reflash -- the board offers both, so this is the one
    # lever that can undo Opus while the board is out of reach.
    # On. The cracking that briefly turned this off was not the codec: the
    # board's frame parser required an even-length payload, which is true of
    # every PCM payload and false for about half of all Opus packets, so half
    # of each reply was discarded before it reached the decoder. See
    # firmware/tests/test_protocol_frames.cpp. Set KIKI_GATEWAY_OPUS_ENABLED=0
    # to fall back to 16 kHz PCM without reflashing.
    opus_enabled: bool = True
    # How long after the gateway hands a sentence's first PCM to the board that
    # audio actually reaches the speaker: the device's prebuffer gate plus its
    # codec/DMA tail. Captions are delayed by this so they land with the voice
    # rather than ahead of it. Tunable live over the calibration port, because
    # the acoustic loopback KikiFast uses to measure this cannot run here -- the
    # board's microphone is muted while it is speaking.
    display_sync_offset_ms: int = 120
    audio_control_host: str = "127.0.0.1"
    audio_control_port: int = 8770
    # The legacy dashboard's port. Configurable because 8090 is a popular
    # number: VS Code's own forwarding had taken it on the laptop, so the Web
    # UI died on "Address already in use" every boot while still printing its
    # URL first, which made it look like it had started.
    webui_port: int = 8090
    # Upper bound on the startup KV-cache warm. A cold prefill of this
    # context measures ~45 s; past this something is wrong and going idle
    # late beats never leaving "Warming up model".
    warm_timeout_seconds: float = 120.0
    # How long the device has to be gone before this counts as the end of a
    # conversation rather than a dropped link. Under it, a reconnect resumes the
    # same runtime, history and warm KV prefix; over it, the summary is written.
    idle_summary_seconds: float = 1800.0
    # Where this gateway can be reached from outside the LAN. Read from a file
    # rather than an env var because a Cloudflare quick tunnel gets a new
    # hostname every restart, and re-reading a file costs nothing while
    # re-reading the environment would need a gateway restart to match.
    public_uri_file: str = ""
    # A firmware URL dropped here is handed to the board on its next connect.
    pending_ota_file: str = ""
    # Raspberry Pi's additive wearable-health service. Reads happen only on a
    # background thread; the speaking path consumes an immutable local summary.
    # Tailscale MagicDNS is available to the laptop even when the wearable and
    # Pi are not on the same LAN; mDNS `kiki.local` is not resolved there.
    health_service_url: str = "http://kiki-1:8091"
    health_service_token: str = ""
    health_poll_seconds: float = 30.0

    # KikiFast's Raspberry Pi runtime periodically asks Unified Idle Mind for
    # its single chosen next-turn note, then injects that note into the ordinary
    # speaking history. main.py is deliberately not running on this route, so
    # the gateway owns only the timer/dispatch adapter; source selection stays
    # inside the byte-identical RPi UnifiedIdleMindManager.
    proactive_questions_enabled: bool = True
    proactive_question_min_seconds: float = 20 * 60
    proactive_question_max_seconds: float = 30 * 60
    proactive_question_busy_retry_seconds: float = 60.0

    # Kiki may receive one one-turn permission for an autonomous battery joke
    # after this much conversational wall time. Direct answers to explicit
    # battery questions are never gated. Default: 1.5 hours.
    battery_remark_interval_seconds: float = 90 * 60

    # --- Dance mode (dance.py / beatgrid.py) ---------------------------------
    dance_enabled: bool = True
    # The choreography agent's wall-clock budget. It sits under the tool
    # timeout on purpose: a routine that arrives late is worth nothing, and the
    # composer can write the whole thing without it.
    dance_agent_deadline: float = 12.0
    # How much of the song to analyse for tempo. 45 s covers an intro and a
    # chorus, which is what makes the energy track useful, and costs about
    # 0.15 s of CPU once the audio has been fetched.
    dance_analysis_seconds: float = 45.0
    dance_analysis_timeout: float = 15.0
    # Below this the measured grid is treated as "there was no beat in there"
    # and the model's BPM hint (or the default) is used instead. Calibrated
    # against synthetic material: click tracks score 1.0, noise under 0.13.
    dance_min_beat_confidence: float = 0.25
    dance_default_bpm: float = 112.0
    # One short spoken line before the music, never over it. Media playback
    # deliberately suppresses the tool follow-up (a reply on top of a starting
    # song is the documented overlap failure), so this is said while the song
    # is still being fetched -- which is also what covers the fetch.
    dance_intro_line: bool = True
    # Nothing dances forever. A 40-minute "song" is a mix, and the board should
    # go back to being Kiki long before the battery notices.
    dance_max_seconds: float = 480.0

    startup_speech: str = ""
    autoplay_query: str = ""
    autoplay_seconds: float = 0.0
    autoplay_repeat: bool = False
    log_level: str = "INFO"

    def default_hotwords(self) -> tuple[str, ...]:
        """The fallback hotword list, for a mode that names none of its own."""
        return tuple(
            word.strip().lower()
            for word in str(self.hotwords or "").split(",")
            if word.strip()
        )

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        # This repository ships the RPi-compatible core beside the gateway.
        # Falling back to DirectLlamaCore when LEGACY_ROOT was omitted silently
        # disabled Kiki's persona, Unified Idle Mind, workers and proactive
        # scheduler. An explicit environment value still wins, including an
        # intentional empty value; the bundled core is the production default.
        bundled_legacy_root = str(
            Path(__file__).resolve().parent.parent / "legacy_kiki"
        )

        def env(name: str, default: str) -> str:
            return os.getenv(f"KIKI_GATEWAY_{name}", default)

        def integer(name: str, default: int) -> int:
            return int(env(name, str(default)))

        def number(name: str, default: float) -> float:
            return float(env(name, str(default)))

        def boolean(name: str, default: bool) -> bool:
            return env(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}

        return cls(
            bind_host=env("HOST", cls.bind_host),
            bind_port=int(env("PORT", str(cls.bind_port))),
            auth_token=env("TOKEN", ""),
            llama_url=env("LLAMA_URL", cls.llama_url),
            whisper_url=env("WHISPER_URL", cls.whisper_url),
            tts_url=env("TTS_URL", cls.tts_url),
            legacy_root=env("LEGACY_ROOT", bundled_legacy_root),
            model_path=env("SILERO_MODEL", cls.model_path),
            vad_threshold=number("VAD_THRESHOLD", cls.vad_threshold),
            min_speech_ms=integer("MIN_SPEECH_MS", cls.min_speech_ms),
            speculative_silence_ms=integer(
                "SPECULATIVE_SILENCE_MS", cls.speculative_silence_ms
            ),
            endpoint_silence_ms=integer("ENDPOINT_SILENCE_MS", cls.endpoint_silence_ms),
            partial_asr_interval_ms=integer(
                "PARTIAL_ASR_INTERVAL_MS", cls.partial_asr_interval_ms
            ),
            max_utterance_seconds=number(
                "MAX_UTTERANCE_SECONDS", cls.max_utterance_seconds
            ),
            preroll_ms=integer("PREROLL_MS", cls.preroll_ms),
            near_field_enabled=boolean("NEAR_FIELD_ENABLED", cls.near_field_enabled),
            near_field_engage_dbfs=number(
                "NEAR_FIELD_ENGAGE_DBFS", cls.near_field_engage_dbfs
            ),
            near_field_open_margin_db=number(
                "NEAR_FIELD_OPEN_MARGIN_DB", cls.near_field_open_margin_db
            ),
            near_field_close_margin_db=number(
                "NEAR_FIELD_CLOSE_MARGIN_DB", cls.near_field_close_margin_db
            ),
            hotword_enabled=boolean("HOTWORD_ENABLED", cls.hotword_enabled),
            hotwords=env("HOTWORDS", cls.hotwords),
            hotword_similarity=number("HOTWORD_SIMILARITY", cls.hotword_similarity),
            hotword_min_fuzzy_length=integer(
                "HOTWORD_MIN_FUZZY_LENGTH", cls.hotword_min_fuzzy_length
            ),
            hotword_speculative=boolean("HOTWORD_SPECULATIVE", cls.hotword_speculative),
            hotword_carry_back_seconds=number(
                "HOTWORD_CARRY_BACK_SECONDS", cls.hotword_carry_back_seconds
            ),
            hotword_carry_back_max_utterances=integer(
                "HOTWORD_CARRY_BACK_MAX_UTTERANCES",
                cls.hotword_carry_back_max_utterances,
            ),
            hotword_carry_back_max_lead_words=integer(
                "HOTWORD_CARRY_BACK_MAX_LEAD_WORDS",
                cls.hotword_carry_back_max_lead_words,
            ),
            hotword_carry_back_max_trail_words=integer(
                "HOTWORD_CARRY_BACK_MAX_TRAIL_WORDS",
                cls.hotword_carry_back_max_trail_words,
            ),
            wakeword_enabled=boolean("WAKEWORD_ENABLED", cls.wakeword_enabled),
            barge_in_enabled=boolean("BARGE_IN_ENABLED", cls.barge_in_enabled),
            barge_in_guard_ms=integer("BARGE_IN_GUARD_MS", cls.barge_in_guard_ms),
            barge_in_vad_threshold=number(
                "BARGE_IN_VAD_THRESHOLD", cls.barge_in_vad_threshold
            ),
            barge_in_min_dbfs=number("BARGE_IN_MIN_DBFS", cls.barge_in_min_dbfs),
            barge_in_noise_margin_db=number(
                "BARGE_IN_NOISE_MARGIN_DB", cls.barge_in_noise_margin_db
            ),
            barge_in_window_frames=integer(
                "BARGE_IN_WINDOW_FRAMES", cls.barge_in_window_frames
            ),
            barge_in_min_votes=integer(
                "BARGE_IN_MIN_VOTES", cls.barge_in_min_votes
            ),
            barge_in_min_consecutive=integer(
                "BARGE_IN_MIN_CONSECUTIVE", cls.barge_in_min_consecutive
            ),
            tts_output_rate=integer("TTS_OUTPUT_RATE", cls.tts_output_rate),
            tts_gain=number("TTS_GAIN", cls.tts_gain),
            speaker_volume=integer("SPEAKER_VOLUME", cls.speaker_volume),
            media_gain=number("MEDIA_GAIN", cls.media_gain),
            playback_lead_seconds=number(
                "PLAYBACK_LEAD_SECONDS", cls.playback_lead_seconds
            ),
            playback_lead_seconds_remote=number(
                "PLAYBACK_LEAD_SECONDS_REMOTE", cls.playback_lead_seconds_remote
            ),
            media_lead_seconds=number("MEDIA_LEAD_SECONDS", cls.media_lead_seconds),
            media_lead_seconds_remote=number(
                "MEDIA_LEAD_SECONDS_REMOTE", cls.media_lead_seconds_remote
            ),
            playback_lead_seconds_max=number(
                "PLAYBACK_LEAD_SECONDS_MAX", cls.playback_lead_seconds_max
            ),
            opus_enabled=boolean("OPUS_ENABLED", cls.opus_enabled),
            opus_bitrate=integer("OPUS_BITRATE", cls.opus_bitrate),
            display_sync_offset_ms=integer(
                "DISPLAY_SYNC_OFFSET_MS", cls.display_sync_offset_ms
            ),
            audio_control_host=env("AUDIO_CONTROL_HOST", cls.audio_control_host),
            audio_control_port=integer("AUDIO_CONTROL_PORT", cls.audio_control_port),
            webui_port=integer("WEBUI_PORT", cls.webui_port),
            warm_timeout_seconds=number("WARM_TIMEOUT_SECONDS", cls.warm_timeout_seconds),
            idle_summary_seconds=number("IDLE_SUMMARY_SECONDS", cls.idle_summary_seconds),
            public_uri_file=env("PUBLIC_URI_FILE", cls.public_uri_file),
            pending_ota_file=env("PENDING_OTA_FILE", cls.pending_ota_file),
            health_service_url=env("HEALTH_URL", cls.health_service_url),
            health_service_token=env("HEALTH_TOKEN", cls.health_service_token),
            health_poll_seconds=number("HEALTH_POLL_SECONDS", cls.health_poll_seconds),
            proactive_questions_enabled=boolean(
                "PROACTIVE_QUESTIONS_ENABLED", cls.proactive_questions_enabled
            ),
            proactive_question_min_seconds=number(
                "PROACTIVE_QUESTION_MIN_SECONDS",
                cls.proactive_question_min_seconds,
            ),
            proactive_question_max_seconds=number(
                "PROACTIVE_QUESTION_MAX_SECONDS",
                cls.proactive_question_max_seconds,
            ),
            proactive_question_busy_retry_seconds=number(
                "PROACTIVE_QUESTION_BUSY_RETRY_SECONDS",
                cls.proactive_question_busy_retry_seconds,
            ),
            battery_remark_interval_seconds=number(
                "BATTERY_REMARK_INTERVAL_SECONDS",
                cls.battery_remark_interval_seconds,
            ),
            dance_enabled=boolean("DANCE_ENABLED", cls.dance_enabled),
            dance_agent_deadline=number("DANCE_AGENT_DEADLINE", cls.dance_agent_deadline),
            dance_analysis_seconds=number(
                "DANCE_ANALYSIS_SECONDS", cls.dance_analysis_seconds
            ),
            dance_analysis_timeout=number(
                "DANCE_ANALYSIS_TIMEOUT", cls.dance_analysis_timeout
            ),
            dance_min_beat_confidence=number(
                "DANCE_MIN_BEAT_CONFIDENCE", cls.dance_min_beat_confidence
            ),
            dance_default_bpm=number("DANCE_DEFAULT_BPM", cls.dance_default_bpm),
            dance_intro_line=boolean("DANCE_INTRO_LINE", cls.dance_intro_line),
            dance_max_seconds=number("DANCE_MAX_SECONDS", cls.dance_max_seconds),
            startup_speech=env("STARTUP_SPEECH", ""),
            autoplay_query=env("AUTOPLAY_QUERY", ""),
            autoplay_seconds=number("AUTOPLAY_SECONDS", 0.0),
            autoplay_repeat=boolean("AUTOPLAY_REPEAT", False),
            log_level=env("LOG_LEVEL", cls.log_level),
        )

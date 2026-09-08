#!/usr/bin/env python3
"""Build the offline Whisper calibration used by the TTS LCD scheduler.

This command is intentionally manual. It synthesizes known phrases, asks the
existing whisper.cpp server for token timestamps, fits small text-duration
models, validates them, and atomically writes the runtime profile. It is never
imported or executed by main.py.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import wave
from collections import defaultdict

import numpy as np
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.tts_sync import (  # noqa: E402
    PROFILE_VERSION, TimingCalibration, active_sink_name,
    normalize_word as runtime_normalize_word, script_for_word, visible_words,
)
from tools_and_config.config_loader import get_tts_config, get_stt_config  # noqa: E402


SAMPLE_RATE = 24000
CORPUS = {
    "en": [
        "Give me a moment.",
        "Okay, so, here is what I found.",
        "Vitamin D comes from sunlight, eggs, fish, and mushrooms.",
        "The quick brown robot checked three different tools before answering.",
        "[laughter] That was unexpectedly useful!",
    ],
    "hi": [
        "एक मिनट रुकिए।",
        "ठीक है, मुझे जवाब मिल गया।",
        "धूप, अंडे और मछली से विटामिन डी मिलता है।",
        "मैंने तीन अलग औज़ार जाँच कर जवाब दिया।",
        "[laughter] यह सच में काफ़ी उपयोगी था!",
    ],
    "mixed": [
        "Okay, Vitamin D धूप और eggs से भी मिलता है।",
        "एक second रुकिए, tool का जवाब आ रहा है।",
    ],
}
_TAG_RE = re.compile(r"\[[^\[\]]+\]")


def normalize_word(word: str) -> str:
    return runtime_normalize_word(word)


def whisper_words(payload: dict) -> list[dict]:
    """Merge whisper.cpp token pieces into whitespace-delimited words."""
    merged = []
    current = None
    for segment in payload.get("segments", []):
        for token in segment.get("words", []):
            raw = str(token.get("word", ""))
            if not raw:
                continue
            begins_word = bool(raw[:1].isspace()) or current is None
            piece = raw.strip()
            if begins_word and piece:
                if current is not None:
                    merged.append(current)
                current = {
                    "word": piece,
                    "start": float(token.get("start", 0.0)),
                    "end": float(token.get("end", token.get("start", 0.0))),
                }
            elif current is not None:
                current["word"] += piece
                current["end"] = float(token.get("end", current["end"]))
    if current is not None:
        merged.append(current)
    return merged


def align_known_words(text: str, recognized: list[dict]) -> list[dict]:
    known = visible_words(_TAG_RE.sub("", text))
    left = [normalize_word(word) for word in known]
    right = [normalize_word(item["word"]) for item in recognized]
    matcher = difflib.SequenceMatcher(a=left, b=right, autojunk=False)
    aligned = []
    for a0, b0, size in matcher.get_matching_blocks():
        for offset in range(size):
            if left[a0 + offset]:
                item = recognized[b0 + offset]
                aligned.append({
                    "index": a0 + offset,
                    "recognized_index": b0 + offset,
                    "word": known[a0 + offset],
                    "start": item["start"],
                    "end": item["end"],
                })
    return aligned


def pcm_to_wav(path: str, pcm: bytes):
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm)


def discover_capture_device() -> str:
    """Return an ALSA plug device without loading crash-prone PortAudio."""
    if shutil.which("arecord") is None:
        raise RuntimeError("arecord is required for acoustic latency calibration")
    result = subprocess.run(
        ["arecord", "-l"], capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"could not list ALSA capture devices: {detail}")
    devices = re.findall(
        r"^card\s+\d+:\s+([^\s\[]+).*device\s+(\d+):",
        result.stdout,
        flags=re.MULTILINE,
    )
    if not devices:
        raise RuntimeError("no ALSA capture device found; pass --capture-device")
    card, device = devices[0]
    return f"plughw:CARD={card},DEV={device}"


def measure_acoustic_latency(session, tts_url: str, whisper_url: str,
                             voice: str, capture_device: str,
                             temp_dir: str) -> float:
    """Measure pre-opened aplay -> speaker -> microphone latency with Whisper.

    Capture deliberately runs in the external ``arecord`` process. Some ALSA
    device combinations make PortAudio segfault inside ``PyAudio.open()``,
    which cannot be caught or reported safely by Python.
    """
    if shutil.which("aplay") is None:
        raise RuntimeError("aplay is required for acoustic latency calibration")
    if shutil.which("arecord") is None:
        raise RuntimeError("arecord is required for acoustic latency calibration")

    phrase = "Calibration signal begins now."
    pcm = synthesize(session, tts_url, phrase, voice)
    source_wav = os.path.join(temp_dir, "latency-source.wav")
    pcm_to_wav(source_wav, pcm)
    source_words = whisper_words(transcribe(session, whisper_url, source_wav))
    source_match = next((item for item in source_words
                         if normalize_word(item["word"]) == "calibration"), None)
    if source_match is None:
        raise RuntimeError("Whisper returned no source words for latency calibration")
    source_onset = source_match["start"]
    measured = []

    for repetition in range(3):
        player = None
        recorder = None
        try:
            player = subprocess.Popen(
                ["aplay", "-q", "-f", "S16_LE", "-r", str(SAMPLE_RATE),
                 "-c", "1", "-t", "raw", "-"],
                stdin=subprocess.PIPE,
            )
            recorded_wav = os.path.join(temp_dir, f"latency-recorded-{repetition}.wav")
            pcm_duration = len(pcm) / (SAMPLE_RATE * 2)
            capture_seconds = max(3, int(pcm_duration + 1.9))
            capture_started = time.monotonic()
            recorder = subprocess.Popen(
                ["arecord", "-q", "-D", capture_device, "-f", "S16_LE",
                 "-r", "16000", "-c", "1", "-t", "wav", "-d",
                 str(capture_seconds), recorded_wav],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            time.sleep(0.35)
            if recorder.poll() is not None:
                detail = (recorder.stderr.read() if recorder.stderr else "").strip()
                raise RuntimeError(
                    f"could not open capture device {capture_device}: {detail}")
            play_write_at = time.monotonic()
            player.stdin.write(pcm)
            player.stdin.close()
            player.wait(timeout=30)
            _, record_error = recorder.communicate(timeout=capture_seconds + 5)
            if recorder.returncode != 0:
                raise RuntimeError(
                    f"capture device {capture_device} failed: {record_error.strip()}")
            captured_words = whisper_words(transcribe(session, whisper_url, recorded_wav))
            captured_match = next((item for item in captured_words
                                   if normalize_word(item["word"]) == "calibration"), None)
            if captured_match is None:
                continue
            playback_offset = play_write_at - capture_started
            latency = captured_match["start"] - playback_offset - source_onset
            if 0.0 <= latency <= 2.5:
                measured.append(latency * 1000.0)
        finally:
            if player is not None and player.poll() is None:
                player.kill()
            if recorder is not None and recorder.poll() is None:
                recorder.kill()
                recorder.wait(timeout=2)
    if not measured:
        raise RuntimeError("could not measure speaker latency; pass --output-latency-ms")
    latency_ms = float(statistics.median(measured))
    print(f"Acoustic output latency: {latency_ms:.1f}ms ({measured})")
    return latency_ms


def measure_lcd_write_lead() -> float:
    """Measure queue-to-I2C commit time; safe to call only in manual calibration."""
    from core.lcd_display import lcd_manager

    commits = []
    lcd_manager.set_commit_observer(commits.append)
    stream_id = lcd_manager.begin_stream_session()
    try:
        for number in range(5):
            lcd_manager.write("LCD calibration", str(number), stream_id=stream_id)
            lcd_manager._queue.join()
        delays = [(item["committed_at"] - item["queued_at"]) * 1000.0
                  for item in commits if item["stream_id"] == stream_id]
        return float(statistics.median(delays or [35.0]))
    finally:
        lcd_manager.end_stream_session(stream_id)
        lcd_manager.set_commit_observer(None)


def synthesize(session, url: str, text: str, voice: str) -> bytes:
    body = {"input": text, "response_format": "pcm"}
    if voice != "default":
        body["voice"] = voice
    response = session.post(f"{url}/v1/audio/speech", json=body, timeout=(3, 60))
    response.raise_for_status()
    return response.content


def transcribe(session, url: str, wav_path: str) -> dict:
    with open(wav_path, "rb") as audio:
        response = session.post(
            url,
            files={"file": ("calibration.wav", audio, "audio/wav")},
            data={
                "response_format": "verbose_json",
                "language": "auto",
                "no_timestamps": "false",
                "token_timestamps": "true",
                "temperature": "0",
            },
            timeout=(3, 90),
        )
    response.raise_for_status()
    return response.json()


def feature_row(word: str) -> list[float]:
    letters = sum(ch.isalnum() for ch in word)
    marks = sum(0x0900 <= ord(ch) <= 0x097F and not ch.isalnum() for ch in word)
    terminal = float(any(ch in word for ch in ".!?।"))
    comma = float(not terminal and any(ch in word for ch in ",;:"))
    return [1.0, float(letters), float(marks), comma, terminal]


def fit_model(rows: list[tuple[list[float], float]], initials: list[float],
              word_durations: dict[str, list[float]],
              initial_words: dict[str, list[float]]) -> dict:
    if len(rows) < 4:
        raise RuntimeError("not enough aligned words to fit timing model")
    x = np.asarray([row for row, _ in rows], dtype=float)
    y = np.asarray([target for _, target in rows], dtype=float)
    # Small dependency-free non-negative least-squares coordinate descent.
    # Clipping an unconstrained least-squares result after fitting badly
    # distorts cumulative word timing; solve under the real runtime constraint.
    coefficients = np.zeros(x.shape[1], dtype=float)
    coefficients[0] = max(40.0, float(np.median(y)) * 0.35)
    for _ in range(2000):
        previous = coefficients.copy()
        for column in range(x.shape[1]):
            vector = x[:, column]
            denominator = float(np.dot(vector, vector))
            if denominator <= 1e-9:
                continue
            residual = y - x @ coefficients + vector * coefficients[column]
            coefficients[column] = max(0.0, float(np.dot(vector, residual)) / denominator)
        if float(np.max(np.abs(coefficients - previous))) < 1e-7:
            break
    return {
        "initial_ms": round(max(0.0, statistics.median(initials or [0.0])), 3),
        "initial_word_ms": {
            word: round(float(statistics.median(values)), 3)
            for word, values in sorted(initial_words.items()) if values
        },
        "base_ms": round(float(coefficients[0]), 3),
        "char_ms": round(float(coefficients[1]), 3),
        "mark_ms": round(float(coefficients[2]), 3),
        "comma_pause_ms": round(float(coefficients[3]), 3),
        "terminal_pause_ms": round(float(coefficients[4]), 3),
        "word_ms": {
            word: round(float(statistics.median(values)), 3)
            for word, values in sorted(word_durations.items()) if values
        },
    }


def discover_voices(session, tts_url: str) -> list[str]:
    response = session.get(f"{tts_url}/v1/voices", timeout=(3, 10))
    response.raise_for_status()
    voices = [item.get("name") for item in response.json().get("voices", [])]
    return ["default"] + sorted(voice for voice in voices if voice)


def atomic_write_json(path: str, payload: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temporary, path)


def main() -> int:
    tts_cfg = get_tts_config()
    stt_cfg = get_stt_config()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tts-url", default=tts_cfg.get("local_url"))
    parser.add_argument("--whisper-url", default=stt_cfg.get("whisper_url"))
    parser.add_argument("--voices", nargs="*", help="default: every server voice")
    parser.add_argument("--output", default=os.path.join(
        ROOT, tts_cfg.get("display_sync", {}).get(
            "profile_path", "tools_and_config/lcd_sync_calibration.json")))
    parser.add_argument("--output-latency-ms", type=float,
                        help="skip audible speaker/microphone latency measurement")
    parser.add_argument(
        "--capture-device",
        help="ALSA microphone for calibration (default: first arecord device)",
    )
    parser.add_argument("--lcd-write-lead-ms", type=float,
                        help="skip physical LCD write-latency measurement")
    parser.add_argument("--tolerance-ms", type=float, default=150.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    session = requests.Session()
    tts_url = args.tts_url.rstrip("/")
    voices = args.voices or discover_voices(session, tts_url)
    samples = defaultdict(list)
    fallback_char_ms = defaultdict(lambda: defaultdict(list))

    with tempfile.TemporaryDirectory(prefix="kikifast-lcd-sync-") as temp_dir:
        output_latency_ms = args.output_latency_ms
        if output_latency_ms is None:
            capture_device = args.capture_device or discover_capture_device()
            print("Playing three short audible latency-calibration phrases...")
            print(f"Capturing with {capture_device}")
            output_latency_ms = measure_acoustic_latency(
                session, tts_url, args.whisper_url, voices[0],
                capture_device, temp_dir,
            )
        for voice in voices:
            print(f"Calibrating {voice}...")
            for language, phrases in CORPUS.items():
                for number, text in enumerate(phrases):
                    pcm = synthesize(session, tts_url, text, voice)
                    words = visible_words(_TAG_RE.sub("", text))
                    total_chars = max(1, sum(max(1, sum(ch.isalnum() for ch in word))
                                             for word in words))
                    pcm_duration_ms = len(pcm) / (SAMPLE_RATE * 2) * 1000.0
                    for script in {script_for_word(word) for word in words}:
                        fallback_char_ms[voice][script].append(
                            pcm_duration_ms / total_chars)
                    # whisper.cpp can occasionally reproduce the Hindi words
                    # while assigning collapsed or otherwise invalid token
                    # timestamps. Never treat that apparent text match as
                    # authoritative timing data. Hindi/mixed schedules use the
                    # duration of the known synthesized PCM instead.
                    if language != "en":
                        print(f"  using source-duration fallback for {language}: {text}")
                        continue
                    wav_path = os.path.join(temp_dir, f"{voice}-{language}-{number}.wav")
                    pcm_to_wav(wav_path, pcm)
                    result = transcribe(session, args.whisper_url, wav_path)
                    aligned = align_known_words(text, whisper_words(result))
                    known_count = len(visible_words(_TAG_RE.sub("", text)))
                    if len(aligned) != known_count:
                        print(f"  skipped non-authoritative Whisper text: {text}")
                        continue
                    samples[voice].append((text, aligned, pcm_duration_ms))

    profile = {
        "version": PROFILE_VERSION,
        "sample_rate": SAMPLE_RATE,
        "tts_url": tts_url,
        "sink_name": active_sink_name(),
        "output_latency_ms": output_latency_ms,
        "lcd_write_lead_ms": (args.lcd_write_lead_ms
                              if args.lcd_write_lead_ms is not None
                              else measure_lcd_write_lead()),
        "tolerance_ms": args.tolerance_ms,
        "profiles": {},
    }

    for voice in voices:
        by_script = {"latin": [], "devanagari": []}
        initials = {"latin": [], "devanagari": []}
        initial_words = {"latin": defaultdict(list), "devanagari": defaultdict(list)}
        word_durations = {"latin": defaultdict(list), "devanagari": defaultdict(list)}
        for _, aligned, pcm_duration_ms in samples[voice]:
            for pos, item in enumerate(aligned):
                script = script_for_word(item["word"])
                if item["index"] == 0:
                    initials[script].append(item["start"] * 1000.0)
                    initial_words[script][normalize_word(item["word"])].append(
                        item["start"] * 1000.0)
                if (pos + 1 < len(aligned)
                        and aligned[pos + 1]["index"] == item["index"] + 1
                        and aligned[pos + 1]["recognized_index"]
                        == item["recognized_index"] + 1):
                    onset_gap_ms = (
                        aligned[pos + 1]["start"] - item["start"]
                    ) * 1000.0
                else:
                    # A skipped/misrecognized neighbor cannot provide a valid
                    # onset interval for this known word.
                    continue
                if 0.0 <= onset_gap_ms <= pcm_duration_ms:
                    by_script[script].append((feature_row(item["word"]), onset_gap_ms))
                    word_durations[script][normalize_word(item["word"])].append(
                        onset_gap_ms)
        models = {}
        for script, rows in by_script.items():
            if len(rows) >= 4:
                models[script] = fit_model(
                    rows, initials[script], word_durations[script],
                    initial_words[script],
                )
            else:
                # Whisper text can be unreliable for Hindi. Preserve coverage
                # without pretending the transcript is ground truth: use only
                # deterministic source-PCM duration per known grapheme.
                char_ms = statistics.median(
                    fallback_char_ms[voice].get(script, [55.0]))
                models[script] = {
                    "initial_ms": 60.0,
                    "initial_word_ms": {},
                    "base_ms": 40.0,
                    "char_ms": round(float(char_ms), 3),
                    "mark_ms": round(float(char_ms) * 0.45, 3),
                    "comma_pause_ms": 90.0,
                    "terminal_pause_ms": 160.0,
                    "word_ms": {},
                }
        models["tags"] = {"duration_ms": 0.0}
        profile["profiles"][voice] = models

    # Validate absolute onset error on the same deterministic calibration
    # corpus. The command refuses to replace a known-good profile on failure.
    calibration = TimingCalibration(profile)
    errors = []
    error_details = []
    for voice, voice_samples in samples.items():
        for text, aligned, _pcm_duration_ms in voice_samples:
            schedule, reason = calibration.schedule(text, voice)
            if schedule is None:
                raise RuntimeError(reason)
            for item in aligned:
                cue = (schedule.cues[item["index"]]
                       if item["index"] < len(schedule.cues) else None)
                if cue is not None and normalize_word(cue.word) == normalize_word(item["word"]):
                    error = abs(cue.offset_s - item["start"]) * 1000.0
                    errors.append(error)
                    error_details.append((error, voice, item["word"], cue.offset_s,
                                          item["start"]))
    max_error = max(errors or [float("inf")])
    p95 = float(np.percentile(errors, 95)) if errors else float("inf")
    print(f"Validation: max={max_error:.1f}ms p95={p95:.1f}ms samples={len(errors)}")
    for error, voice, word, predicted, actual in sorted(error_details, reverse=True)[:5]:
        print(f"  {voice}/{word}: predicted={predicted:.3f}s actual={actual:.3f}s "
              f"error={error:.1f}ms")
    if max_error > args.tolerance_ms:
        print("FAILED: profile not written; add corpus samples or recalibrate coefficients")
        return 2
    if not args.dry_run:
        atomic_write_json(args.output, profile)
        print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

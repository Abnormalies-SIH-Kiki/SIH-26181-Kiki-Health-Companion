"""Emulate the ESP32 over Wi-Fi and measure speech-end to first PCM."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import statistics
import time
import wave

import numpy as np
from websockets.asyncio.client import connect

from kiki_gateway.protocol import AudioFrame, BinaryKind, encode_event


def load_mono_48k(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as source:
        channels, width, rate = source.getnchannels(), source.getsampwidth(), source.getframerate()
        if width != 2:
            raise ValueError("benchmark WAV must be 16-bit PCM")
        pcm = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1).astype(np.int16)
    if rate != 48000:
        positions = np.arange(round(pcm.size * 48000 / rate)) * rate / 48000
        pcm = np.interp(positions, np.arange(pcm.size), pcm).astype(np.int16)
    return pcm


async def wait_until_ready(ws) -> None:
    acknowledged = False
    while True:
        message = await asyncio.wait_for(ws.recv(), timeout=60)
        if not isinstance(message, str):
            continue
        event = json.loads(message)
        acknowledged = acknowledged or event.get("type") == "hello_ack"
        if acknowledged and event.get("type") == "state" and event.get("state") == "idle":
            return


async def one_turn(ws, pcm: np.ndarray, stream_id: int) -> dict:
    await ws.send(encode_event("wake", source="benchmark"))

    result = {}
    first_pcm_seen = asyncio.Event()
    playback_done = asyncio.Event()

    async def receive():
        async for message in ws:
            if not isinstance(message, str):
                continue
            event = json.loads(message)
            if event["type"] in {"stt_speculative", "transcript_final", "first_pcm"}:
                result[event["type"]] = {**event, "received_at": time.perf_counter()}
            if event["type"] == "first_pcm":
                first_pcm_seen.set()
            if event["type"] == "audio_end":
                playback_done.set()
                return

    receiver = asyncio.create_task(receive())
    sequence = 0
    silence = np.zeros(480, dtype="<i2").tobytes()
    for _ in range(50):
        await ws.send(AudioFrame(BinaryKind.MIC_PCM_S16_48K_MONO, 0, stream_id, sequence, time.time_ns() // 1000, silence).encode())
        sequence += 1
        await asyncio.sleep(0.01)
    started = time.perf_counter()
    for offset in range(0, pcm.size, 480):
        chunk = pcm[offset : offset + 480]
        if chunk.size < 480:
            chunk = np.pad(chunk, (0, 480 - chunk.size))
        await ws.send(AudioFrame(BinaryKind.MIC_PCM_S16_48K_MONO, 0, stream_id, sequence, time.time_ns() // 1000, chunk.astype("<i2").tobytes()).encode())
        sequence += 1
        await asyncio.sleep(0.01)
    speech_end = time.perf_counter()
    for _ in range(75):
        await ws.send(AudioFrame(BinaryKind.MIC_PCM_S16_48K_MONO, 0, stream_id, sequence, time.time_ns() // 1000, silence).encode())
        sequence += 1
        await asyncio.sleep(0.01)
        if first_pcm_seen.is_set():
            break
    await asyncio.wait_for(first_pcm_seen.wait(), timeout=30)
    await asyncio.wait_for(playback_done.wait(), timeout=30)
    await receiver
    await ws.send(encode_event("playback_drained"))
    first = result["first_pcm"]["received_at"]
    return {
        "clip_realtime_ms": round((speech_end - started) * 1000),
        "speech_end_to_first_pcm_ms": round((first - speech_end) * 1000),
        "gateway_endpoint_to_first_pcm_ms": result["first_pcm"].get("ttfw_ms"),
        "stt_ms": result.get("transcript_final", {}).get("stt_ms"),
        "speculative": "stt_speculative" in result,
        "text": result.get("transcript_final", {}).get("text"),
    }


async def main_async(args):
    pcm = load_mono_48k(args.wav)
    samples = []
    async with connect(args.uri, compression=None, max_size=2 * 1024 * 1024) as ws:
        await ws.send(encode_event("hello", token=args.token, device="benchmark"))
        await wait_until_ready(ws)
        for index in range(args.iterations):
            item = await one_turn(ws, pcm, index + 1)
            samples.append(item)
            print(json.dumps({"iteration": index + 1, **item}))
    latencies = [item["speech_end_to_first_pcm_ms"] for item in samples]
    print(json.dumps({"iterations": len(samples), "median_ms": statistics.median(latencies), "max_ms": max(latencies)}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", default="ws://vaibhav:8765")
    parser.add_argument("--token", default="")
    parser.add_argument("--wav", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=5)
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()

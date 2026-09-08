#!/usr/bin/env python3
"""Interactively tune Kiki's ESP32 speaker volume and digital TTS gain."""

from __future__ import annotations

import argparse
import json
import socket


DEFAULT_TEXT = (
    "[confirmation-en] Hi Vaibhav. This is Kiki testing my speaker volume "
    "and digital voice gain. How does this level sound?"
)


def request(host: str, port: int, payload: dict) -> dict:
    with socket.create_connection((host, port), timeout=5.0) as connection:
        connection.sendall((json.dumps(payload) + "\n").encode())
        response = connection.makefile("rb").readline()
    if not response:
        raise RuntimeError("gateway closed the calibration connection")
    result = json.loads(response)
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "unknown gateway error"))
    return result


def show(result: dict) -> None:
    print(
        f"Speaker: {result['volume']:3d}%   "
        f"Digital gain: {result['gain']:.2f}x   "
        f"Caption delay: {result.get('sync_offset_ms', 0):4d} ms   "
        f"ESP32 connected: {result['devices']}"
    )
    if result.get("message"):
        print(result["message"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live Kiki voice-volume calibration (changes last until gateway restart)."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    args = parser.parse_args()

    result = request(args.host, args.port, {"action": "status"})
    show(result)
    print(
        "\nCommands:\n"
        "  p or Enter   play the test phrase\n"
        "  v NUMBER     speaker/codec volume, 0-100\n"
        "  g NUMBER     digital gain, 0.0-6.0 (recommended 2.0-4.0)\n"
        "  v+ / v-      speaker volume up/down by 5\n"
        "  g+ / g-      digital gain up/down by 0.2\n"
        "  d NUMBER     caption delay in ms; raise it if the text leads the voice\n"
        "  d+ / d-      caption delay up/down by 40 ms\n"
        "  s TEXT       play custom Kiki text\n"
        "  q            quit (current settings remain active until restart)\n"
    )

    while True:
        try:
            command = input("audio> ").strip()
            lower = command.lower()
            if lower in {"q", "quit", "exit"}:
                break
            if lower in {"", "p", "play"}:
                result = request(
                    args.host, args.port, {"action": "speak", "text": args.text}
                )
            elif lower.startswith("s "):
                result = request(
                    args.host, args.port, {"action": "speak", "text": command[2:]}
                )
            elif lower.startswith("v "):
                result = request(
                    args.host, args.port,
                    {"action": "volume", "value": int(command.split(None, 1)[1])},
                )
            elif lower.startswith("g "):
                result = request(
                    args.host, args.port,
                    {"action": "gain", "value": float(command.split(None, 1)[1])},
                )
            elif lower.startswith("d "):
                result = request(
                    args.host, args.port,
                    {"action": "sync", "value": int(command.split(None, 1)[1])},
                )
            elif lower in {"d+", "d-"}:
                current = request(args.host, args.port, {"action": "status"})
                value = current["sync_offset_ms"] + (40 if lower == "d+" else -40)
                result = request(
                    args.host, args.port,
                    {"action": "sync", "value": max(-2000, min(5000, value))},
                )
            elif lower in {"v+", "v-", "g+", "g-"}:
                current = request(args.host, args.port, {"action": "status"})
                if lower[0] == "v":
                    value = current["volume"] + (5 if lower == "v+" else -5)
                    result = request(
                        args.host, args.port,
                        {"action": "volume", "value": max(0, min(100, value))},
                    )
                else:
                    value = current["gain"] + (0.2 if lower == "g+" else -0.2)
                    result = request(
                        args.host, args.port,
                        {"action": "gain", "value": max(0.0, min(6.0, value))},
                    )
            else:
                print("Unknown command. Use p, v NUMBER, g NUMBER, d NUMBER, s TEXT, or q.")
                continue
            show(result)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"Error: {exc}")


if __name__ == "__main__":
    main()

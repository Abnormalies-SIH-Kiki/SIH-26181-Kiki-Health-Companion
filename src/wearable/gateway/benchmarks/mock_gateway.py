"""Real VAD/STT/TTS gateway with deterministic LLM output for transport tests."""

from __future__ import annotations

import argparse
import asyncio

from websockets.asyncio.server import serve

from kiki_gateway.config import GatewayConfig
from kiki_gateway.session import DeviceSession


class FixedCore:
    async def prefill_partial(self, _text):
        return None

    async def stream_reply(self, _text, _abort):
        yield "sentence", "Hello. This is an end to end latency test."
        yield "done", "Hello. This is an end to end latency test."


async def main_async(port: int):
    base = GatewayConfig.from_env()
    config = GatewayConfig(**{**base.__dict__, "bind_port": port, "legacy_root": ""})

    async def handler(ws):
        session = DeviceSession(ws, config, core=FixedCore())
        try:
            await session.run()
        finally:
            await session.close()

    async with serve(handler, config.bind_host, port, compression=None, max_size=2 * 1024 * 1024):
        await asyncio.Future()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    asyncio.run(main_async(parser.parse_args().port))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""A bad network, on demand.

Sits between the board (or ``fake_device.py``) and the gateway and makes the link
as bad as a college AP or a phone hotspot, reproducibly and without root::

    python3 slowlink.py --listen 0.0.0.0:8766 --target 127.0.0.1:8765 \
        --up-kbps 2000 --down-kbps 2000 --rtt-ms 120 --jitter-ms 60 \
        --stall-pct 1.0 --outage-every 60 --outage-for 8

Why a userspace proxy rather than ``tc``: ``tc`` needs root, shapes a whole
interface (so it also throttles ssh, the tunnel and every other service on the
laptop), and cannot be reconfigured between test rows without tearing the
qdisc down. This shapes exactly one TCP flow, is driven entirely by argv, and
prints its own throughput so a run's shaping is evidence rather than an
assumption.

The model is store-and-forward, which is what a bottleneck link actually is:
bytes arrive into a backlog, drain at the configured rate, and only then spend
the propagation delay. Doing it the other way round (delay, then rate) makes a
1 Mbps link behave like a 1 Mbps link with no queue, and queueing delay is the
entire reason a saturated uplink hurts.

**Loss is modelled as a stall, deliberately.** This is a byte-stream proxy: it
cannot drop a byte without corrupting the stream, and it must not, because TCP
below it would never have delivered a corrupt stream either. What the endpoints
actually experience from packet loss is a retransmission pause, so
``--stall-pct`` inserts one RTO-sized pause with that probability per tick. That
is the observable the board reacts to.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import random
import time


TICK = 0.005  # 5 ms shaper granularity


def _hostport(value: str, default_host: str = "127.0.0.1") -> tuple[str, int]:
    if ":" not in value:
        return default_host, int(value)
    host, _, port = value.rpartition(":")
    return (host or default_host), int(port)


class Direction:
    """One half of the link: a backlog, a token bucket and a delivery queue."""

    def __init__(self, name: str, kbps: float, delay_s: float, jitter_s: float,
                 stall_pct: float, stall_s: float):
        self.name = name
        self.rate = (kbps * 1000.0) / 8.0 if kbps > 0 else 0.0  # bytes/s, 0 = unlimited
        self.delay_s = delay_s
        self.jitter_s = jitter_s
        self.stall_pct = stall_pct
        self.stall_s = stall_s
        self.backlog = bytearray()
        self.tokens = 0.0
        self.deliveries: asyncio.Queue = asyncio.Queue()
        self.eof = False
        # Delivery times must never go backwards: a TCP proxy that reorders is
        # not modelling a slow link, it is corrupting the stream.
        self.last_deliver = 0.0
        self.stalled_until = 0.0
        # Stats, sampled by the reporter.
        self.bytes_in = 0
        self.bytes_out = 0
        self.peak_backlog = 0
        self.stalls = 0

    def offer(self, data: bytes) -> None:
        self.backlog += data
        self.bytes_in += len(data)
        self.peak_backlog = max(self.peak_backlog, len(self.backlog))

    async def shape(self) -> None:
        """Drain the backlog at the configured rate, stamping a delivery time."""
        last = time.monotonic()
        # The bucket accrues from the real clock, not from tick counts, so an
        # overslept tick does not lose rate -- but only if the cap is generous
        # enough to hold what accrued meanwhile. 50 ms of allowance absorbs
        # ordinary scheduler jitter without making the shaping visibly bursty.
        burst = max(self.rate * 0.05, 4096.0) if self.rate else 0.0
        while True:
            await asyncio.sleep(TICK)
            now = time.monotonic()
            elapsed, last = now - last, now

            if self.stall_pct > 0 and now >= self.stalled_until:
                # Per-tick probability scaled so --stall-pct reads as "percent of
                # seconds that begin a stall", which is the number a human can
                # reason about.
                if random.random() < self.stall_pct / 100.0 * TICK:
                    self.stalled_until = now + self.stall_s
                    self.stalls += 1
            if now < self.stalled_until:
                if self.eof and not self.backlog:
                    await self.deliveries.put(None)
                    return
                continue

            if self.rate:
                self.tokens = min(self.tokens + self.rate * elapsed, burst)
                take = min(int(self.tokens), len(self.backlog))
            else:
                take = len(self.backlog)

            if take:
                if self.rate:
                    self.tokens -= take
                chunk = bytes(self.backlog[:take])
                del self.backlog[:take]
                jitter = random.uniform(-self.jitter_s, self.jitter_s) if self.jitter_s else 0.0
                at = max(now + max(0.0, self.delay_s + jitter), self.last_deliver)
                self.last_deliver = at
                await self.deliveries.put((at, chunk))
            elif self.eof and not self.backlog:
                await self.deliveries.put(None)
                return

    async def deliver(self, writer: asyncio.StreamWriter) -> None:
        while True:
            item = await self.deliveries.get()
            if item is None:
                with contextlib.suppress(Exception):
                    writer.write_eof()
                return
            at, chunk = item
            wait = at - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            writer.write(chunk)
            self.bytes_out += len(chunk)
            await writer.drain()


class SlowLink:
    def __init__(self, args):
        self.args = args
        self.target = _hostport(args.target)
        self.live: set[asyncio.Task] = set()
        self.writers: set[asyncio.StreamWriter] = set()
        self.outage = False
        self.directions: list[Direction] = []
        self.connections = 0
        self.refused = 0

    def _direction(self, name: str, kbps: float) -> Direction:
        d = Direction(
            name,
            kbps,
            self.args.rtt_ms / 2000.0,
            self.args.jitter_ms / 2000.0,
            self.args.stall_pct,
            self.args.stall_ms / 1000.0,
        )
        self.directions.append(d)
        return d

    async def handle(self, client_reader, client_writer) -> None:
        peer = client_writer.get_extra_info("peername")
        if self.outage:
            self.refused += 1
            client_writer.close()
            return
        self.connections += 1
        print(f"[slowlink] connect #{self.connections} from {peer}", flush=True)
        try:
            server_reader, server_writer = await asyncio.open_connection(*self.target)
        except OSError as exc:
            print(f"[slowlink] upstream refused: {exc}", flush=True)
            client_writer.close()
            return

        up = self._direction("up", self.args.up_kbps)
        down = self._direction("down", self.args.down_kbps)
        self.writers.update({client_writer, server_writer})

        async def pump(reader, direction):
            try:
                while True:
                    data = await reader.read(65536)
                    if not data:
                        break
                    direction.offer(data)
            except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
                pass
            finally:
                direction.eof = True

        # A peer closing does NOT mean the shaped backlog is discarded: on a real
        # slow link those bytes are already committed and still arrive. Only the
        # two deliver tasks are waited on, and each ends when its own direction
        # has drained and passed the EOF sentinel through -- so the last second
        # of a reply is not thrown away by whichever side hangs up first.
        pumps = [
            asyncio.create_task(pump(client_reader, up)),
            asyncio.create_task(pump(server_reader, down)),
        ]
        shapers = [asyncio.create_task(up.shape()), asyncio.create_task(down.shape())]
        delivers = [
            asyncio.create_task(up.deliver(server_writer)),
            asyncio.create_task(down.deliver(client_writer)),
        ]
        tasks = pumps + shapers + delivers
        try:
            done, pending = await asyncio.wait(delivers, return_when=asyncio.FIRST_COMPLETED)
            if pending:
                # One side is finished; give the other a bounded chance to drain
                # rather than waiting on a half-open connection forever.
                await asyncio.wait(pending, timeout=self.args.drain_timeout)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for w in (client_writer, server_writer):
                self.writers.discard(w)
                with contextlib.suppress(Exception):
                    w.close()
            for d in (up, down):
                with contextlib.suppress(ValueError):
                    self.directions.remove(d)
            print(f"[slowlink] closed {peer}", flush=True)

    async def outages(self) -> None:
        every, hold = self.args.outage_every, self.args.outage_for
        if every <= 0 or hold <= 0:
            return
        while True:
            await asyncio.sleep(every)
            self.outage = True
            print(f"[slowlink] === OUTAGE for {hold}s ===", flush=True)
            for w in list(self.writers):
                # abort(), not close(). A graceful close flushes what is still
                # buffered and sends a FIN, which both endpoints treat as an
                # orderly shutdown -- the emulator rode six of those without
                # recording a single dropped session. A link that has gone away
                # does not say goodbye: abort() drops the buffers and RSTs the
                # socket, which is what the far end actually has to survive.
                with contextlib.suppress(Exception):
                    w.transport.abort()
                with contextlib.suppress(Exception):
                    w.close()
            # Do NOT clear the set here. handle() adds a connection's writers
            # once, when it opens, and removes them in its own finally; wiping
            # the set meant every outage after the first had nothing to sever
            # and quietly did nothing at all.
            await asyncio.sleep(hold)
            self.outage = False
            print("[slowlink] === link back ===", flush=True)

    async def report(self) -> None:
        prev = {}
        while True:
            await asyncio.sleep(self.args.report_seconds)
            rows = []
            for d in list(self.directions):
                key = id(d)
                was_in, was_out = prev.get(key, (0, 0))
                rate = (d.bytes_out - was_out) * 8 / 1000.0 / self.args.report_seconds
                prev[key] = (d.bytes_in, d.bytes_out)
                rows.append(
                    f"{d.name} {rate:7.1f} kbps  backlog {len(d.backlog):6d}B"
                    f"  peak {d.peak_backlog:6d}B  stalls {d.stalls}"
                )
            if rows:
                print("[slowlink] " + " | ".join(rows), flush=True)

    async def run(self) -> None:
        host, port = _hostport(self.args.listen, "0.0.0.0")
        server = await asyncio.start_server(self.handle, host, port)
        print(
            f"[slowlink] {host}:{port} -> {self.target[0]}:{self.target[1]}  "
            f"up={self.args.up_kbps}kbps down={self.args.down_kbps}kbps "
            f"rtt={self.args.rtt_ms}ms jitter={self.args.jitter_ms}ms "
            f"stall={self.args.stall_pct}%/{self.args.stall_ms}ms "
            f"outage={self.args.outage_every}s/{self.args.outage_for}s",
            flush=True,
        )
        async with server:
            await asyncio.gather(server.serve_forever(), self.outages(), self.report())


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--listen", default="0.0.0.0:8766")
    p.add_argument("--target", default="127.0.0.1:8765")
    p.add_argument("--up-kbps", type=float, default=0, help="board->gateway, 0 = unlimited")
    p.add_argument("--down-kbps", type=float, default=0, help="gateway->board, 0 = unlimited")
    p.add_argument("--rtt-ms", type=float, default=0.0, help="total round trip, split evenly")
    p.add_argument("--jitter-ms", type=float, default=0.0, help="peak-to-peak RTT variation")
    p.add_argument("--stall-pct", type=float, default=0.0,
                   help="percent of seconds that begin a retransmission stall")
    p.add_argument("--stall-ms", type=float, default=400.0, help="length of one such stall")
    p.add_argument("--outage-every", type=float, default=0.0, help="seconds between outages")
    p.add_argument("--outage-for", type=float, default=0.0, help="seconds each outage lasts")
    p.add_argument("--report-seconds", type=float, default=5.0)
    p.add_argument("--drain-timeout", type=float, default=30.0,
                   help="grace for the second direction to finish after the first")
    p.add_argument("--seed", type=int, default=None, help="make a run reproducible")
    args = p.parse_args()
    if args.seed is not None:
        random.seed(args.seed)
    try:
        asyncio.run(SlowLink(args).run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

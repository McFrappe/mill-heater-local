"""Regression tests for MillClient link supervision against a simulated HF-LPT120.

The simulated module behaves like the real one:
- answers 0x50 (APPLY) with a frame carrying the target temperature,
- enforces AT+TCPTO: closes the socket when it has received nothing FROM the
  client for `tcpto` seconds (traffic from the MCU does not reset the timer),
- optionally pushes unsolicited 0xc9-like traffic ("chatty" MCU).

Run: python3 tests/test_keepalive.py
"""
import asyncio
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "custom_components/mill_local"))
import protocol as p  # noqa: E402


def reply_0x50(target: int = 21) -> bytes:
    b = bytearray([0x5A, 0x00, 0x10, 0x01, 0x00, p.CMD_APPLY, 0x00, target, 0x00] + [0] * 5)
    b += bytes([sum(b[1:14]) & 0xFF, 0x5B])
    return bytes(b)


async def scenario(*, answers: str, chatty: bool, tcpto: float, seconds: float):
    """answers: 'all' | 'first' (dies after the connect handshake)."""
    async def module(reader, writer):
        last_rx = time.monotonic()
        replied = 0

        async def push():  # MCU traffic that does NOT reset TCPTO
            while chatty:
                await asyncio.sleep(0.2)
                writer.write(reply_0x50())
                await writer.drain()

        pusher = asyncio.ensure_future(push())
        try:
            while True:
                left = tcpto - (time.monotonic() - last_rx)
                try:
                    data = await asyncio.wait_for(reader.read(4096), max(left, 0.01))
                except asyncio.TimeoutError:
                    return  # AT+TCPTO expired -> module drops the client
                if not data:
                    return
                last_rx = time.monotonic()
                frames, _ = p.split_frames(data)
                if any(f[5] == p.CMD_APPLY for f in frames) and (answers == 'all' or replied == 0):
                    replied += 1
                    writer.write(reply_0x50())
                    await writer.drain()
        finally:
            pusher.cancel()
            writer.close()

    srv = await asyncio.start_server(module, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    lost = []
    client = p.MillClient("127.0.0.1", lambda s: s is None and lost.append(time.monotonic()), port=port)
    client.KEEPALIVE, client.IDLE_TIMEOUT = 0.3, 1.0
    task = asyncio.ensure_future(client.run())
    await asyncio.sleep(seconds)
    task.cancel()
    srv.close()
    return len(lost)


async def main() -> None:
    cases = [
        # (name, kwargs, expect_lost)
        ("quiet MCU, TCPTO 1s", dict(answers='all', chatty=False, tcpto=1.0, seconds=4.0), False),
        ("chatty MCU, TCPTO 1s", dict(answers='all', chatty=True, tcpto=1.0, seconds=4.0), False),
        ("module dies after connect", dict(answers='first', chatty=False, tcpto=99, seconds=3.0), True),
    ]
    ok = True
    for name, kw, expect_lost in cases:
        n = await scenario(**kw)
        passed = (n > 0) == expect_lost
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'}  {name}: lost={n}")
    sys.exit(0 if ok else 1)


asyncio.run(main())

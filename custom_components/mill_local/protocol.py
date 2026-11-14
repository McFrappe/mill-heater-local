"""Mill Gen 2 heater MCU protocol over the HF-LPT120 TCP bridge (port 8899).

Pure functions plus a small asyncio client. No Home Assistant imports, so
scripts/mill.py reuses this file directly.

Frame: 5a 00 <len> <seq> 00 <cmd> <a1> 00 <a2> ... <sum(bytes[1:len-2])> 5b
Commands sent are 16 bytes (len 0x10); the MCU also sends 17-byte 0xc9 status.
"""
from __future__ import annotations

import asyncio
import time

PORT = 8899

# Opcodes. 0x47/0x46 and the 0xc9 field map follow JanOveSaltvedt/mill-mod-matter
# (src/mill.rs); 0x44/0x48/0x50 come from the Mill-app capture in
# metrafonic/MillHeat-ESPHome.
CMD_MODE44 = 0x44  # NOT power. On this glass heater: 01 -> status[6]=04, target 8 C
                   # (frost/away?); 00 -> status[6]=00, previous target back.
CMD_SET_TEMP = 0x46
CMD_POWER = 0x47  # heat on (01) / off (00). Confirmed: off -> LCD "OF", status[10]=00
CMD_FAN = 0x48
CMD_APPLY = 0x50  # commit; reply carries target temperature
CMD_STATUS = 0xC9

MIN_TEMP = 5
MAX_TEMP = 35


def frame(seq: int, cmd: int, a1: int = 0, a2: int = 0) -> bytes:
    b = bytearray([0x5A, 0x00, 0x10, seq & 0xFF, 0x00, cmd, a1, 0x00, a2] + [0] * 5)
    b += bytes([sum(b[1:14]) & 0xFF, 0x5B])
    return bytes(b)


def split_frames(buf: bytes) -> tuple[list[bytes], bytes]:
    """Return (complete valid frames, unconsumed tail)."""
    frames, i = [], 0
    while i + 3 <= len(buf):
        if buf[i] != 0x5A:
            i += 1
            continue
        n = buf[i + 2]  # total length incl. 5a and 5b
        if n < 5:
            i += 1
            continue
        if i + n > len(buf):
            break
        f = buf[i:i + n]
        if f[-1] == 0x5B and (sum(f[1:-2]) & 0xFF) == f[-2]:
            frames.append(bytes(f))
            i += n
        else:
            i += 1
    return frames, buf[i:]


def parse_status(frames: list[bytes]) -> dict | None:
    """Decode target/room temperature from an 0x50 reply or 0xc9 status."""
    st: dict | None = None
    for f in frames:
        # 0xc9 indices (ours, incl. leading 0x5a). Confirmed on this heater by
        # button-press capture 2026-11-14 (logs/listen.log):
        #   [7]  target C, 5-35
        #   [8]  room C, whole degrees (0 = no reading yet)
        #   [12] heating demand (1 when target > room, with hysteresis)
        #   [10] heat on 01 / off 00 (follows 0x47; LCD shows "OF" when 00)
        # Unknown: [6] reacts to 0x44 (00/01/04)
        # The MCU pushes 0xc9 on every change (buttons, room temp) and sends
        # an empty 0xcb heartbeat about every 2 min. The 0x50 reply carries
        # only the target ([8] there is not room temperature).
        if f[5] == CMD_APPLY and f[7]:
            st = {**(st or {}), "target_c": f[7]}
        elif f[5] == CMD_STATUS and len(f) >= 15:
            st = {
                **(st or {}),
                "mode44": f[6],
                "target_c": f[7],
                "heat_on": {0: False, 1: True}.get(f[10]),
                "heating": bool(f[12]),
            }
            if f[8]:
                st["room_c"] = f[8]
    return st


class MillError(Exception):
    """Heater unreachable or command not possible."""


async def get_mac(host: str, timeout: float = 3.0) -> str:
    """Module MAC via the HF config port: UDP 48899 'HF-A11ASSISTHREAD'
    -> b'<ip>,<MAC>,<model>'. Read-only; no command mode is entered."""
    loop = asyncio.get_running_loop()
    reply: asyncio.Future = loop.create_future()

    class _Proto(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):
            if not reply.done():
                reply.set_result(data)

    transport, _ = await loop.create_datagram_endpoint(_Proto, remote_addr=(host, 48899))
    try:
        for _ in range(3):
            transport.sendto(b"HF-A11ASSISTHREAD")
            try:
                data = await asyncio.wait_for(asyncio.shield(reply), timeout)
                break
            except asyncio.TimeoutError:
                continue
        else:
            raise MillError(f"no reply from {host}:48899")
    finally:
        transport.close()
    parts = data.decode(errors="replace").split(",")
    if len(parts) < 2 or len(parts[1]) != 12:
        raise MillError(f"unexpected 48899 reply: {data!r}")
    return parts[1].lower()


async def probe(host: str, port: int = PORT, timeout: float = 5.0) -> dict:
    """One-shot check (config flow): connect, send 0x50, return decoded reply."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError) as err:
        raise MillError(f"cannot connect to {host}:{port}: {err}") from err
    try:
        writer.write(frame(int(time.time()), CMD_APPLY))
        await writer.drain()
        buf, end = b"", time.monotonic() + 3.0
        while time.monotonic() < end:
            try:
                chunk = await asyncio.wait_for(reader.read(4096), end - time.monotonic())
            except asyncio.TimeoutError:
                break
            if not chunk:
                break
            frames, buf = split_frames(buf + chunk)
            st = parse_status(frames)  # echoes decode to nothing
            if st:
                return st
    except OSError as err:
        raise MillError(str(err)) from err
    finally:
        writer.close()
    raise MillError("no reply from heater")


class MillClient:
    """Persistent push client.

    Keeps one TCP connection to the bridge. The MCU pushes a 0xc9 status on
    every change (buttons, room temperature, our commands); no heartbeat reaches
    this connection. The client sends a periodic keepalive (see KEEPALIVE).
    Every decoded update is merged into `state` and passed
    to `on_update(state)`; `on_update(None)` signals a lost connection.
    Reconnects with backoff. Run `run()` as a background task; cancel to stop.
    """

    # The HF-LPT120 closes the TCP server socket after AT+TCPTO seconds
    # (default 300) without receiving data FROM the client - traffic from the
    # MCU does not reset it (manual 4.2.2.25; HA log 2026-11-14: drop every
    # 300 s even right after a status push). So we send 0x50 every KEEPALIVE
    # seconds regardless of RX; the MCU answers with its target.
    # IDLE_TIMEOUT: no byte received at all for this long = link is dead.
    KEEPALIVE = 60
    IDLE_TIMEOUT = 150

    def __init__(self, host: str, on_update, port: int = PORT) -> None:
        self.host, self.port = host, port
        self.on_update = on_update
        self.state: dict = {}
        self._seq = int(time.time()) & 0xFF
        self._writer: asyncio.StreamWriter | None = None
        self._got_target = asyncio.Event()

    @property
    def connected(self) -> bool:
        return self._writer is not None

    def _next(self, cmd: int, a1: int = 0, a2: int = 0) -> bytes:
        self._seq = (self._seq + 1) & 0xFF
        return frame(self._seq, cmd, a1, a2)

    async def _send(self, *frames: bytes) -> None:
        if self._writer is None:
            raise MillError("not connected to heater")
        try:
            for f in frames:
                self._writer.write(f)
                await self._writer.drain()
                await asyncio.sleep(0.2)  # MCU handles one frame at a time
        except OSError as err:
            raise MillError(str(err)) from err

    async def run(self) -> None:
        backoff = 5
        while True:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port), 10
                )
            except (OSError, asyncio.TimeoutError):
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            backoff = 5
            self._writer = writer
            self._got_target.clear()
            reading = asyncio.ensure_future(self._read(reader))
            keepalive: asyncio.Future | None = None
            try:
                await self._prime()
                keepalive = asyncio.ensure_future(self._keepalive())
                done, _ = await asyncio.wait(
                    {reading, keepalive}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    task.result()  # re-raise why the link ended
            except (OSError, MillError, asyncio.TimeoutError):
                pass
            finally:
                reading.cancel()
                if keepalive is not None:
                    keepalive.cancel()
                self._writer = None
                writer.close()
                self.on_update(None)
            await asyncio.sleep(backoff)

    async def _prime(self) -> None:
        """Get a full 0xc9 right after connecting.

        0x50 returns only the target; re-sending that same target (once per
        connect) makes the MCU push a complete 0xc9 with room temp and power.
        """
        await self._send(self._next(CMD_APPLY))
        await asyncio.wait_for(self._got_target.wait(), 5)
        target = self.state.get("target_c")
        if target and "room_c" not in self.state:
            await self._send(self._next(CMD_SET_TEMP, 1, target), self._next(CMD_APPLY))

    async def _keepalive(self) -> None:
        while True:
            await asyncio.sleep(self.KEEPALIVE)
            await self._send(self._next(CMD_APPLY))

    async def _read(self, reader: asyncio.StreamReader) -> None:
        buf = b""
        while True:
            chunk = await asyncio.wait_for(reader.read(4096), self.IDLE_TIMEOUT)
            if not chunk:
                raise MillError("connection closed by heater")
            frames, buf = split_frames(buf + chunk)
            st = parse_status(frames)  # echoes of our own frames decode to nothing
            if st:
                self.state = {**self.state, **st}
                if "target_c" in st:
                    self._got_target.set()
                self.on_update(dict(self.state))

    async def set_power(self, on: bool) -> None:
        await self._send(self._next(CMD_POWER, 1 if on else 0))

    async def set_temperature(self, celsius: int) -> None:
        if not MIN_TEMP <= celsius <= MAX_TEMP:
            raise ValueError(f"target must be {MIN_TEMP}-{MAX_TEMP} C")
        await self._send(self._next(CMD_SET_TEMP, 1, celsius), self._next(CMD_APPLY))

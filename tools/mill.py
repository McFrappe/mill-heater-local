#!/usr/bin/env python3
"""Control a Mill Gen 2 heater whose HF-LPT120 runs stock High-Flying firmware.

The module bridges TCP 8899 <-> the heater MCU UART (9600 8N1). Frames are
16 bytes: 5a 00 10 <seq> 00 <cmd> <a1> 00 <a2> 00*5 <sum(bytes[1:14])> 5b.
See docs/PROTOCOL.md.

Usage:
  mill.py status              read target/room temperature, power, heating
  mill.py set 21              set target temperature (5-35 C)
  mill.py power on|off        cmd 0x47, heating on/off (LCD shows "OF" when off)
  mill.py mode44 on|off       cmd 0x44 (ESPHome/Mill-app "power" frame). Here:
                              on -> 8 C mode (status[6]=04), off -> normal
  mill.py fan on|off          cmd 0x48
  mill.py raw 5a0010...5b     send a raw frame, print replies
Options: --host <heater-ip> [--port 8899]
"""
import argparse
import os
import socket
import sys
import time

# Frame building/parsing is shared with the Home Assistant integration.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "custom_components", "mill_local"))
from protocol import (  # noqa: E402
    CMD_APPLY, CMD_FAN, CMD_MODE44, CMD_POWER, CMD_SET_TEMP, MAX_TEMP, MIN_TEMP, frame, parse_status, split_frames,
)


class Mill:
    def __init__(self, host, port=8899, timeout=5):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.seq = int(time.time()) & 0xFF

    def close(self):
        self.sock.close()

    def send(self, data, wait=2.0):
        """Send one frame, return all valid frames received within `wait` s."""
        self.sock.sendall(data)
        self.sock.settimeout(0.3)
        buf, end = b"", time.time() + wait
        while time.time() < end:
            try:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
            except socket.timeout:
                pass
        return [f for f in split_frames(buf)[0] if f != data]  # drop the echo

    def cmd(self, cmd, a1=0, a2=0, wait=2.0):
        self.seq = (self.seq + 1) & 0xFF
        return self.send(frame(self.seq, cmd, a1, a2), wait)

    def status(self):
        # 0x50 (apply/commit) is answered with target + room temp and a 0xc9
        # status frame; it changes nothing on its own.
        return parse(self.cmd(CMD_APPLY))

    def set_temp(self, c):
        if not MIN_TEMP <= c <= MAX_TEMP:
            raise ValueError("target must be %d-%d C" % (MIN_TEMP, MAX_TEMP))
        self.cmd(CMD_SET_TEMP, 1, c)
        return parse(self.cmd(CMD_APPLY))

    def power(self, on):
        return parse(self.cmd(CMD_POWER, 1 if on else 0))

    def mode44(self, on):
        return parse(self.cmd(CMD_MODE44, 1 if on else 0))

    def fan(self, on):
        return parse(self.cmd(CMD_FAN, 1 if on else 0))


def parse(frames):
    """(decoded status, raw frames). Decoding lives in protocol.parse_status."""
    return parse_status(frames), frames


def main():
    p = argparse.ArgumentParser(description="Mill Gen 2 heater over TCP 8899")
    p.add_argument("--host", required=True, help="heater IP")
    p.add_argument("--port", type=int, default=8899)
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("status")
    s = sub.add_parser("set"); s.add_argument("temp", type=int)
    s = sub.add_parser("power"); s.add_argument("state", choices=["on", "off"])
    s = sub.add_parser("mode44"); s.add_argument("state", choices=["on", "off"])
    s = sub.add_parser("fan"); s.add_argument("state", choices=["on", "off"])
    s = sub.add_parser("raw"); s.add_argument("hex")
    a = p.parse_args()

    m = Mill(a.host, a.port)
    try:
        if a.action == "status":
            st, frames = m.status()
        elif a.action == "set":
            st, frames = m.set_temp(a.temp)
        elif a.action == "power":
            st, frames = m.power(a.state == "on")
        elif a.action == "mode44":
            st, frames = m.mode44(a.state == "on")
        elif a.action == "fan":
            st, frames = m.fan(a.state == "on")
        else:
            st, frames = parse(m.send(bytes.fromhex(a.hex.replace(" ", ""))))
        for f in frames:
            print("rx", f.hex(" "))
        print(st if st else "no status frame received")
    finally:
        m.close()


if __name__ == "__main__":
    main()

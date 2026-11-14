#!/usr/bin/env python3
"""Passive logger: hold TCP 8899 open and print every frame the heater MCU sends.

Sends nothing. Press buttons on the heater (+, -, gear, WiFi) while it runs and
note what you pressed; the timestamps line up with the frames.
Ctrl+C to stop. Also appends to ./mill_listen.log.

  python3 mill_listen.py --host <heater-ip>
"""
import argparse
import os
import socket
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "custom_components", "mill_local"))
from protocol import PORT, parse_status, split_frames  # noqa: E402

LOG = "mill_listen.log"  # appended in the current directory


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", required=True, help="heater IP")
    a = p.parse_args()
    s = socket.create_connection((a.host, PORT), timeout=5)
    s.settimeout(1)
    print("listening on %s:%d - press buttons on the heater, Ctrl+C to stop" % (a.host, PORT))
    buf = b""
    with open(LOG, "a") as log:
        log.write("--- %s start\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        try:
            while True:
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    print("connection closed by heater")
                    break
                frames, buf = split_frames(buf + chunk)
                for f in frames:
                    line = "%s  %s  %s" % (time.strftime("%H:%M:%S"), f.hex(" "),
                                           parse_status([f]) or "")
                    print(line, flush=True)
                    log.write(line + "\n")
                    log.flush()
        except KeyboardInterrupt:
            pass
    s.close()


if __name__ == "__main__":
    main()

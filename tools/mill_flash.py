#!/usr/bin/env python3
"""Flash stock High-Flying HSF firmware onto the HF-LPT120 in a Mill Gen 2 heater.

Serves the OTA image over HTTP and tells the module to fetch it with
AT+UPURL=<url>,<file> on the UDP 48899 config channel. The module downloads
the image, replies +ok (~10 s), verifies the CRC in the 128-byte
"HF-MC3000 Image" header, writes it and reboots by itself (~2 min).

  sudo python3 mill_flash.py <heater-ip> <LPT120_UPGRADE_*.bin> [--serve-ip <this-computer-ip>]

- Use the *_UPGRADE_* image (with header). The plain image is for XMODEM.
- Port 80 needs sudo on macOS/Linux. The URL in AT+UPURL is length-limited
  (~21 chars incl. "http://" and trailing "/"), so serve from a plain IPv4.
- Send AT+UPURL exactly once: every retry makes the module start another
  download (seen as repeated GETs).

See docs/FLASHING.md.
"""
import argparse
import functools
import http.server
import os
import socket
import threading
import time

HEADER_MAGIC = b"HF-MC3000 Image"


def log(msg):
    print("%s  %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log("HTTP %s %s" % (self.client_address[0], fmt % args))

    def copyfile(self, src, dst):
        # Log how many bytes the module actually read (did it take the whole file?).
        sent = 0
        try:
            while True:
                buf = src.read(16 * 1024)
                if not buf:
                    break
                dst.write(buf)
                sent += len(buf)
        except (BrokenPipeError, ConnectionResetError) as e:
            log("HTTP transfer aborted by module after %d bytes (%s)" % (sent, e.__class__.__name__))
            return
        log("HTTP sent %d bytes" % sent)


class Module:
    """UDP 48899 command channel: hello, '+ok' to enter AT mode, 'AT+...\\r'."""

    def __init__(self, host):
        self.addr = (host, 48899)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def handshake(self):
        self.sock.settimeout(3)
        self.sock.sendto(b"HF-A11ASSISTHREAD", self.addr)
        reply = self.sock.recvfrom(1024)[0].decode(errors="replace")
        self.sock.sendto(b"+ok", self.addr)
        return reply

    def _drain(self):
        # A stale reply (e.g. +ERR=-1 to "+ok" when already in AT mode) would
        # otherwise be read as the answer to the next command.
        self.sock.settimeout(0.5)
        try:
            while True:
                self.sock.recvfrom(4096)
        except socket.timeout:
            pass

    def at(self, cmd, wait=3, tries=3):
        for _ in range(tries):
            self._drain()
            self.sock.settimeout(wait)
            self.sock.sendto((cmd + "\r").encode(), self.addr)
            try:
                reply = self.sock.recvfrom(4096)[0].decode(errors="replace").strip()
                log("%s -> %s" % (cmd, reply))
                return reply
            except socket.timeout:
                try:
                    self.handshake()
                except socket.timeout:
                    pass
        log("%s -> <no reply>" % cmd)
        return ""

    def leave(self):
        self.sock.sendto(b"AT+Q\r", self.addr)


def local_ip_towards(host):
    """IP of the interface that routes to `host` (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 48899))
        return s.getsockname()[0]
    finally:
        s.close()


def tcp_open(host, port):
    try:
        socket.create_connection((host, port), timeout=2).close()
        return True
    except OSError:
        return False


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("heater", help="heater IP (module in STA mode on your LAN, or 10.10.100.254 on its AP)")
    p.add_argument("image", help="path to LPT120_UPGRADE_*.bin")
    p.add_argument("--serve-ip", help="IP of this computer as seen by the heater (default: auto)")
    a = p.parse_args()

    image = os.path.abspath(a.image)
    directory, name = os.path.split(image)
    with open(image, "rb") as f:
        has_header = f.read(len(HEADER_MAGIC)) == HEADER_MAGIC
    size = os.path.getsize(image)
    serve_ip = a.serve_ip or local_ip_towards(a.heater)
    log("serving %s (%d bytes, %s) from %s" % (name, size, "OTA image with header" if has_header else "NO header", serve_ip))
    if not has_header:
        raise SystemExit("Refusing: AT+UPURL needs the *_UPGRADE_* image (starts with 'HF-MC3000 Image').")
    url = "http://%s/" % serve_ip
    if len(url) > 21:
        log("warning: URL %r is %d chars; the module may truncate it (limit ~21)" % (url, len(url)))

    srv = http.server.ThreadingHTTPServer(("0.0.0.0", 80), functools.partial(Handler, directory=directory))
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    m = Module(a.heater)
    log("hello -> %s" % m.handshake())
    m.at("AT+VER")
    m.at("AT+APPVER")
    m.at("AT+UPURL=%s,%s" % (url, name), wait=30, tries=1)  # exactly once, see docstring

    log("waiting up to 4 min for download, flash and reboot")
    deadline, last = time.time() + 240, None
    time.sleep(20)
    while time.time() < deadline:
        try:
            m.handshake()
            ver, app = m.at("AT+VER"), m.at("AT+APPVER")
            last = (ver, app)
            if ver and "ZC_version" not in app:  # vendor app layer gone = stock image running
                break
        except socket.timeout:
            log("module not answering (flashing/rebooting)")
        time.sleep(15)
    m.leave()

    for port in (8899, 80):
        log("tcp %d %s" % (port, "OPEN" if tcp_open(a.heater, port) else "closed"))
    srv.shutdown()
    log("done. last reading: %s" % (last,))


if __name__ == "__main__":
    main()

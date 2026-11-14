#!/usr/bin/env python3
"""Flash stock High-Flying HSF firmware onto the HF-LPT120 in a Mill Gen 2 heater.

Serves the OTA image over HTTP and tells the module to fetch it with
AT+UPURL=<url>,<file> on the UDP 48899 config channel. The module downloads
the image, replies +ok (~10 s), checks the 128-byte "HF-MC3000 Image" header,
writes flash and reboots by itself (~2 min).

  python3 mill_flash.py --check <heater-ip> <LPT120_UPGRADE_*.bin>   # pre-flight only, changes nothing
  sudo python3 mill_flash.py <heater-ip> <LPT120_UPGRADE_*.bin>      # flash

<heater-ip> is 10.10.100.254 when this computer is joined to the heater's own
access point (mill-XXXX), or the heater's LAN address in station mode.

- Use the *_UPGRADE_* image (with header). The plain image is for XMODEM.
- Port 80 needs sudo on macOS/Linux. The URL in AT+UPURL is length-limited
  (~21 chars incl. "http://" and trailing "/"), so serve from a plain IPv4.
- AT+UPURL is sent exactly once: every resend makes the module start another
  download (seen as repeated GETs) and it never completes.

See docs/FLASHING.md.
"""
import argparse
import functools
import hashlib
import http.server
import os
import socket
import sys
import threading
import time

HEADER_MAGIC = b"HF-MC3000 Image"
KNOWN_IMAGES = {  # sha256 -> name, from the HSF V2.10-11 package in firmware/
    "eafed8d9a70a28ee732f579851b015b0d10a939c68ef972a8a7739270718eeeb": "LPT120_UPGRADE_HFV2.10_2MB_20181123_11.bin",
}
URL_LIMIT = 21
STOCK_TIMEOUT = 240  # s to wait for download + flash + reboot


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

    def __init__(self, host, port=48899):
        self.addr = (host, port)
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


def firmware_state(appver):
    if "ZC_version" in appver:
        return "mill"   # vendor (AbleCloud) app layer: flash needed
    if appver.startswith("+ERR"):
        return "stock"  # no app layer: stock HSF already running
    return "unknown"


def preflight(a, image, serve_ip, url):
    """Return True if flashing can start. Changes nothing on the module."""
    ok = True

    def check(passed, text, fatal=True):
        nonlocal ok
        log("%s  %s" % ("PASS" if passed else ("FAIL" if fatal else "WARN"), text))
        if not passed and fatal:
            ok = False

    with open(image, "rb") as f:
        data = f.read()
    digest = hashlib.sha256(data).hexdigest()
    check(data.startswith(HEADER_MAGIC), "image has the OTA header (HF-MC3000 Image)")
    check(digest in KNOWN_IMAGES, "image hash %s… is the known LPT120 V2.10-11 OTA image" % digest[:12], fatal=False)
    check(len(url) <= URL_LIMIT, "URL %r is %d chars (limit ~%d)" % (url, len(url), URL_LIMIT), fatal=False)
    try:
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", a.http_port))
        s.close()
        check(True, "HTTP port %d can be opened on this computer" % a.http_port)
    except OSError as e:
        check(False, "HTTP port %d can be opened (%s; use sudo or free the port)" % (a.http_port, e.strerror))

    m = Module(a.heater, a.at_port)
    try:
        hello = m.handshake()
    except socket.timeout:
        check(False, "module answers on UDP %d (is this computer on the heater's network?)" % a.at_port)
        return False
    check("HF-LPT120" in hello, "module identifies as HF-LPT120: %s" % hello)
    ver, appver = m.at("AT+VER"), m.at("AT+APPVER")
    m.leave()
    state = firmware_state(appver)
    check(bool(ver), "module reports its version")
    if state == "stock":
        check(a.force, "module still runs Mill firmware (it already runs stock HSF; --force to reflash)")
    else:
        check(state == "mill", "module runs Mill firmware (ZC_version in AT+APPVER)", fatal=False)
    log("pre-flight %s (serving from %s)" % ("OK" if ok else "FAILED", serve_ip))
    return ok


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("heater", help="heater IP: 10.10.100.254 on its AP, or its LAN IP in station mode")
    p.add_argument("image", help="path to LPT120_UPGRADE_*.bin")
    p.add_argument("--check", action="store_true", help="pre-flight checks only; do not flash")
    p.add_argument("--force", action="store_true", help="flash even if stock firmware already runs")
    p.add_argument("--serve-ip", help="IP of this computer as seen by the heater (default: auto)")
    p.add_argument("--http-port", type=int, default=80, help=argparse.SUPPRESS)  # tests only
    p.add_argument("--at-port", type=int, default=48899, help=argparse.SUPPRESS)  # tests only
    a = p.parse_args()

    image = os.path.abspath(a.image)
    directory, name = os.path.split(image)
    serve_ip = a.serve_ip or local_ip_towards(a.heater)
    host = serve_ip if a.http_port == 80 else "%s:%d" % (serve_ip, a.http_port)
    url = "http://%s/" % host

    if not preflight(a, image, serve_ip, url):
        sys.exit(1)
    if a.check:
        return

    srv = http.server.ThreadingHTTPServer(("0.0.0.0", a.http_port), functools.partial(Handler, directory=directory))
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    m = Module(a.heater, a.at_port)
    m.handshake()
    m.at("AT+UPURL=%s,%s" % (url, name), wait=30, tries=1)  # exactly once, see docstring

    log("waiting up to %d s for download, flash and reboot - do not power off" % STOCK_TIMEOUT)
    deadline, last, state = time.time() + STOCK_TIMEOUT, None, "unknown"
    time.sleep(min(20, STOCK_TIMEOUT / 10))
    while time.time() < deadline:
        try:
            m.handshake()
            ver, appver = m.at("AT+VER"), m.at("AT+APPVER")
            last, state = (ver, appver), firmware_state(appver)
            if ver and state == "stock":
                break
        except socket.timeout:
            log("module not answering (flashing/rebooting)")
        time.sleep(min(15, STOCK_TIMEOUT / 10))
    m.leave()
    srv.shutdown()

    for port in (8899, 80):
        log("tcp %d %s" % (port, "OPEN" if tcp_open(a.heater, port) else "closed"))
    log("result: %s, last reading %s" % ("STOCK FIRMWARE RUNNING" if state == "stock" else "NOT CONFIRMED", last))
    sys.exit(0 if state == "stock" else 2)


if __name__ == "__main__":
    main()

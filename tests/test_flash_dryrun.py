"""Dry run of tools/mill_flash.py against a simulated HF-LPT120 (no hardware).

The simulated module answers the UDP 48899 AT channel like the Mill firmware
(AT+APPVER -> ZC_version), and on AT+UPURL=<url>,<file> really downloads the
image over HTTP, checks the 128-byte "HF-MC3000 Image" header, replies +ok,
goes silent for a few seconds ("flashing") and then reports the stock build.

Checks: --check passes and changes nothing; a flash run downloads the whole
image exactly once and ends with "STOCK FIRMWARE RUNNING"; --check on the
flashed module refuses (already stock).

Run: python3 tests/test_flash_dryrun.py   (~15 s)
"""
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
FLASH = ROOT / "tools" / "mill_flash.py"


class FakeModule:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.stock = False
        self.offline_until = 0.0
        self.downloads = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _reply(self, text, addr):
        self.sock.sendto(text.encode(), addr)

    def _serve(self):
        while True:
            data, addr = self.sock.recvfrom(4096)
            if time.time() < self.offline_until:
                continue  # rebooting
            msg = data.decode(errors="replace").strip()
            if msg == "HF-A11ASSISTHREAD":
                self._reply("127.0.0.1,34EAE7000000,HF-LPT120", addr)
            elif msg in ("+ok", "AT+Q"):
                pass
            elif msg == "AT+VER":
                self._reply("+ok=2.0.10,11 (2018-11-22 1737)" if self.stock else "+ok=2.0.10,2 (2017-02-28 1012)", addr)
            elif msg == "AT+APPVER":
                self._reply("+ERR=-1" if self.stock else "+ok=1.1,ZC_version[1.0.8],Dec 14 2017-16:27:09", addr)
            elif msg.startswith("AT+UPURL="):
                url, name = msg[len("AT+UPURL="):].rsplit(",", 1)
                body = urllib.request.urlopen(url + name, timeout=5).read()
                self.downloads.append(len(body))
                if body.startswith(b"HF-MC3000 Image"):
                    self._reply("+ok", addr)
                    self.offline_until = time.time() + 3
                    self.stock = True
                else:
                    self._reply("+ERR=-4", addr)
            else:
                self._reply("+ERR=-1", addr)


def run(args):
    cmd = [sys.executable, str(FLASH), *args]
    src = FLASH.read_text().replace("STOCK_TIMEOUT = 240", "STOCK_TIMEOUT = 20")  # fast polling for the test
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as t:
        t.write(src)
    cmd[1] = t.name
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout + p.stderr


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main():
    with tempfile.TemporaryDirectory() as d:
        image = pathlib.Path(d) / "LPT120_UPGRADE_TEST.bin"
        image.write_bytes(b"HF-MC3000 Image" + bytes(113) + bytes(range(256)) * 64)
        mod = FakeModule()
        common = ["127.0.0.1", str(image), "--serve-ip", "127.0.0.1", "--at-port", str(mod.port)]
        results = []

        rc, out = run(["--check", *common, "--http-port", str(free_port())])
        results.append(("pre-flight passes, module untouched", rc == 0 and "pre-flight OK" in out and not mod.downloads and not mod.stock))

        rc, out = run([*common, "--http-port", str(free_port())])
        results.append(("flash: image downloaded exactly once", mod.downloads == [image.stat().st_size]))
        results.append(("flash: ends with stock firmware", rc == 0 and "STOCK FIRMWARE RUNNING" in out))

        rc, out = run(["--check", *common, "--http-port", str(free_port())])
        results.append(("pre-flight refuses an already flashed module", rc == 1 and "already runs stock HSF" in out))

    ok = True
    for name, passed in results:
        ok &= passed
        print("%s  %s" % ("PASS" if passed else "FAIL", name))
    if not ok:
        print(out)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

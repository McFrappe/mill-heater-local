"""Read-only probe of the HF-LPT120 config channel (UDP 48899).

  python3 mill_probe.py [heater-ip]     # default 10.10.100.254 (heater AP)

Prints firmware version, Wi-Fi mode/SSID, key (masked) and NETP.
"""
import socket
import sys

ADDR = (sys.argv[1] if len(sys.argv) > 1 else "10.10.100.254", 48899)

out = []
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(3)
try:
    s.sendto(b"HF-A11ASSISTHREAD", ADDR)
    out.append("hello -> %r" % s.recvfrom(1024)[0])
    s.sendto(b"+ok", ADDR)
    for c in [b"AT+VER\r", b"AT+APPVER\r", b"AT+WMODE\r", b"AT+WSSSID\r", b"AT+WSKEY\r", b"AT+NETP\r"]:
        # Discard stale replies (e.g. +ERR=-1 to "+ok" if already in command mode).
        s.settimeout(0.5)
        try:
            while True:
                s.recvfrom(1024)
        except socket.timeout:
            pass
        s.settimeout(3)
        s.sendto(c, ADDR)
        try:
            reply = s.recvfrom(1024)[0]
            if c.startswith(b"AT+WSKEY") and reply.count(b",") >= 2:
                reply = reply.rsplit(b",", 1)[0] + b",********"  # never print the password
            out.append("%s -> %r" % (c.strip().decode(), reply))
        except socket.timeout:
            out.append("%s -> no reply" % c.strip().decode())
    s.sendto(b"AT+Q\r", ADDR)  # leave command mode so the next run starts clean
except Exception as e:
    out.append("error: %r" % e)

print("\n".join(out))

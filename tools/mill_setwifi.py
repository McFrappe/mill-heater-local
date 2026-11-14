"""Write STA WiFi settings to a HF-LPT120 module (Mill Gen 2 heater) over UDP 48899.

  python3 mill_setwifi.py                    # Mac on the heater's own AP (10.10.100.254)
  python3 mill_setwifi.py <heater-ip>        # heater already on your LAN (stock firmware)
  python3 mill_setwifi.py <heater-ip> --web-login    # change web page login only

SSID and password are prompted at runtime; the password is never logged.
Hidden SSIDs: the SSID is set explicitly, so it is the same command.
If the module can't join, AT+MDCH=auto makes it fall back to its own AP
(mill-XXXX, 10.10.100.254) after a few minutes; rerun from there.
"""
import getpass
import os
import socket
import sys

LOG = "mill_setwifi.log"  # written in the current directory; passwords are masked
_hosts = [a for a in sys.argv[1:] if not a.startswith("--")]
ADDR = (_hosts[0] if _hosts else "10.10.100.254", 48899)

log_lines = []


def log(line):
    print(line)
    log_lines.append(line)


def save_log():
    with open(LOG, "w") as f:
        f.write("\n".join(log_lines) + "\n")


def drain(sock):
    """Discard stale replies so the next read matches the next command.

    If the module is already in command mode (e.g. a previous run did not exit),
    the "+ok" handshake itself returns +ERR=-1, which would otherwise be read as
    the reply to the following command.
    """
    old = sock.gettimeout()
    sock.settimeout(0.5)
    try:
        while True:
            sock.recvfrom(1024)
    except socket.timeout:
        pass
    finally:
        sock.settimeout(old)


def at(sock, cmd, secret=None):
    """Send one AT command; return the reply. Mask `secret` in the log."""
    reply = "<no reply>"
    for attempt in range(3):
        if attempt:
            # UDP is lossy and the module can drop out of command mode: re-handshake.
            sock.sendto(b"HF-A11ASSISTHREAD", ADDR)
            drain(sock)
            sock.sendto(b"+ok", ADDR)
        drain(sock)
        sock.sendto((cmd + "\r").encode(), ADDR)
        try:
            reply = sock.recvfrom(1024)[0].decode(errors="replace").strip()
            break
        except socket.timeout:
            continue
    shown_cmd, shown_reply = cmd, reply
    if secret:
        shown_cmd = shown_cmd.replace(secret, "********")
        shown_reply = shown_reply.replace(secret, "********")
    log("%s -> %s" % (shown_cmd, shown_reply))
    return reply


def connect():
    """Open the UDP 48899 config channel and enter command mode."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    for _ in range(3):  # UDP: a single hello is sometimes lost
        try:
            s.sendto(b"HF-A11ASSISTHREAD", ADDR)
            log("hello -> %s" % s.recvfrom(1024)[0].decode(errors="replace"))
            break
        except socket.timeout:
            continue
    else:
        log("hello -> <no reply>. Is this computer on the heater's network?")
        save_log()
        sys.exit(1)
    s.sendto(b"+ok", ADDR)
    return s


def set_web_login():
    """Change the module's web page login (default admin/admin) with AT+WEBU.

    Read back before rebooting; the password is masked in output and log.
    The web page is only one of three open interfaces: UDP 48899 and TCP 8899
    have no login at all, so restrict access with firewall rules too.
    """
    user = input("New web username: ").strip()
    pw = getpass.getpass("New web password (hidden): ")
    if pw != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match.")
    if not user or not pw or "," in user + pw:
        sys.exit("Username and password must be non-empty and contain no commas.")
    if len(user) > 15 or len(pw) > 15:
        print("Note: HF modules often limit these to 15 characters; read-back will show if it was cut.")

    s = connect()
    if not at(s, "AT+WEBU=%s,%s" % (user, pw), pw).startswith("+ok"):
        log("Aborted: module did not accept AT+WEBU. Login unchanged.")
        save_log()
        sys.exit(1)
    if at(s, "AT+WEBU", pw) != "+ok=%s,%s" % (user, pw):
        log("Read-back mismatch (value cut or rejected). Not rebooting; rerun with shorter values.")
        save_log()
        sys.exit(1)
    at(s, "AT+Z")  # reboot so the web server uses the new login
    log("Done. Web login changed; module rebooting (HA reconnects by itself).")
    save_log()


def main():
    if "--web-login" in sys.argv:
        set_web_login()
        return

    ssid = input("WiFi name (SSID): ").strip()
    password = getpass.getpass("WiFi password (hidden): ")

    if not (1 <= len(ssid) <= 32):
        sys.exit("SSID must be 1-32 characters.")
    if not (8 <= len(password) <= 63):
        sys.exit("WPA2 password must be 8-63 characters.")
    if "," in ssid or "," in password:
        sys.exit("Comma in SSID or password breaks the AT command format. Change it on the router first.")

    s = connect()

    steps = [
        ("AT+WSSSID=%s" % ssid, None),
        ("AT+WSKEY=WPA2PSK,AES,%s" % password, password),
    ]
    for cmd, secret in steps:
        if not at(s, cmd, secret).startswith("+ok"):
            log("Aborted: module did not accept the command. Nothing switched to STA.")
            save_log()
            sys.exit(1)

    # Read back before switching mode.
    if at(s, "AT+WSSSID") != "+ok=%s" % ssid:
        log("Aborted: SSID read-back mismatch.")
        save_log()
        sys.exit(1)
    if at(s, "AT+WSKEY", password) != "+ok=WPA2PSK,AES,%s" % password:
        log("Aborted: key read-back mismatch.")
        save_log()
        sys.exit(1)

    if not at(s, "AT+WMODE=STA").startswith("+ok"):
        log("Aborted: could not set STA mode.")
        save_log()
        sys.exit(1)

    at(s, "AT+Z")  # reboot module; it should now join the network
    log("Done. Module rebooting into STA mode. Heater WiFi network will disappear.")
    save_log()


if __name__ == "__main__":
    main()

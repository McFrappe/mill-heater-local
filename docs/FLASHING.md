# Flashing stock firmware onto the Mill Gen 2 Wi-Fi module

This replaces Mill's firmware on the heater's HF-LPT120 Wi-Fi module with
High-Flying's stock firmware, which exposes the heater MCU on TCP 8899. The
whole procedure runs on the heater's own access point: the heater does not
need to join your network until it has been flashed and verified. Read
[FINDINGS.md](FINDINGS.md) first.

> **Risk:** one-way over Wi-Fi. The Mill app/cloud will no longer work with
> the heater. A failed flash can leave the module unbootable; recovery then
> needs the serial bootloader (open the heater, UART0 230400 8N1, nReload,
> XMODEM). The heater itself keeps working from its buttons regardless: the
> MCU is separate.

## Overview

| Step | Network | Command |
|---|---|---|
| 0. Prepare the image | any | `unzip -j …`, `shasum -a 256` |
| 1. Heater into AP mode, join `mill-XXXX` | heater AP | – |
| 2. Pre-flight check | heater AP | `mill_flash.py --check 10.10.100.254 <image>` |
| 3. Flash | heater AP | `sudo mill_flash.py 10.10.100.254 <image>` |
| 4. Power-cycle, rejoin `mill-XXXX` | heater AP | – |
| 5. Verify firmware and control | heater AP | `mill_probe.py`, `mill.py status / set / power` |
| 6. Join your network | heater AP → LAN | `mill_setwifi.py` |
| 7. Lock down, add to Home Assistant | LAN | `mill_setwifi.py --web-login`, firewall |

## Requirements

- A computer with Python 3.9+ and Wi-Fi. Port 80 must be free, and `sudo` is
  needed to bind it.
- **VPN off** while you are connected to the heater. A VPN client (WireGuard
  on-demand and similar) routes the traffic away from the heater's network,
  and the tool then serves from the wrong address.
- This repository.

## 0. Prepare the firmware image

The package is in this repo, unmodified (see [`firmware/README.md`](../firmware/README.md)).
Extract only the OTA image. Plain `unzip` on macOS fails on the Chinese file
names in the package, and `-j` avoids that:

```bash
shasum -a 256 firmware/HF-LPB120_LPT120_LPT220_LPB125_SIP120_Frmware_V2.10-11.zip
# 01177e03ffe377fd78b28bb983de63dc04c4bd5c503e50fafffc92c171472884
unzip -j firmware/HF-LPB120_LPT120_LPT220_LPB125_SIP120_Frmware_V2.10-11.zip \
  "LPX120 V2.10-11/2.10-11/Firmware/LPT120_UPGRADE_HFV2.10_2MB_20181123_11.bin"
shasum -a 256 LPT120_UPGRADE_HFV2.10_2MB_20181123_11.bin
# eafed8d9a70a28ee732f579851b015b0d10a939c68ef972a8a7739270718eeeb
```

Use only **`LPT120_UPGRADE_…`**. It carries the 128-byte `HF-MC3000 Image`
header that OTA requires, and the tool refuses images without it. The
LPB120/LPB125/LPT120G/LPT220/SIP120 images are for other modules.

## 1. Put the heater in AP mode and join it

Start Wi-Fi pairing on the heater as described in Mill's manual (the Wi-Fi
indicator blinks). The module opens its own access point:

| | |
|---|---|
| SSID | `mill-XXXX` (last 4 hex digits of the module MAC) |
| Key | `123456789` |
| Module IP | `10.10.100.254` |
| Your IP | DHCP, `10.10.100.150`–`200` |

Join that network from your computer. You have no internet access while
connected. There is no captive portal; the interface is the UDP 48899 AT
channel.

## 2. Pre-flight check

Checks everything that can be checked without touching the module: image,
hash, URL, port, module identity and current firmware.

```bash
python3 tools/mill_flash.py --check 10.10.100.254 LPT120_UPGRADE_HFV2.10_2MB_20181123_11.bin
```

Expected:

```
PASS  image has the OTA header (HF-MC3000 Image)
PASS  image hash eafed8d9a70a… is the known LPT120 V2.10-11 OTA image
PASS  URL 'http://10.10.100.150/' is 21 chars (limit ~21)
PASS  HTTP port 80 can be opened on this computer      # run with sudo, or expect FAIL here
PASS  module identifies as HF-LPT120: 10.10.100.254,34EAE7xxxxxx,HF-LPT120
AT+VER -> +ok=2.0.10,2 (2017-02-28 1012)
AT+APPVER -> +ok=1.1,ZC_version[1.0.8],Dec 14 2017-16:27:09
PASS  module reports its version
PASS  module runs Mill firmware (ZC_version in AT+APPVER)
pre-flight OK (serving from 10.10.100.150)
```

Do not continue on a `FAIL`. A `WARN` on the URL length means your address
is longer than `10.10.100.1xx`. Check that you are on the heater AP and that
the VPN is off.

## 3. Flash

```bash
sudo python3 tools/mill_flash.py 10.10.100.254 LPT120_UPGRADE_HFV2.10_2MB_20181123_11.bin
```

The tool runs the pre-flight again, starts an HTTP server on port 80, sends
`AT+UPURL=http://10.10.100.150/,LPT120_UPGRADE_…bin` **once** and polls until
the module reports the stock build. Reference run (station mode; on the AP
the addresses are `10.10.100.254` / `10.10.100.150`):

```
23:11:16  HTTP 192.168.1.50 "GET /LPT120_UPGRADE_…bin HTTP/1.1" 200 -
23:11:21  HTTP sent 373840 bytes
23:11:26  AT+UPURL=http://192.168.1.10/,LPT120_UPGRADE_…bin -> +ok
23:11:26  waiting up to 240 s for download, flash and reboot - do not power off
...       module not answering (flashing/rebooting)
23:13:46  AT+VER -> +ok=2.0.10,11 (2018-11-22 1737)
23:13:46  AT+APPVER -> +ERR=-1
23:13:47  tcp 8899 OPEN
23:13:47  tcp 80 OPEN
23:13:47  result: STOCK FIRMWARE RUNNING, …
```

Rules:

- **Do not interrupt it, and never resend `AT+UPURL`.** Each resend starts a
  new download and the module never completes. `+ok` arrives only after the
  whole image is read (~10 s).
- Do not power-cycle the heater, or let the computer sleep, in the ~2 minutes
  after `+ok`.
- The module reboots when done, and the AP disappears briefly. Your computer
  may drop off and rejoin; the tool keeps polling.

> The reference flash was done with the module in station mode. The AP path
> uses the same mechanism (module → HTTP GET to the client at
> `10.10.100.150`). If the module never fetches the image (no `GET` line),
> use the station-mode alternative at the end of this document.

## 4. Power-cycle and rejoin

Unplug the heater for 10 s, plug it back in and wait about 1 minute, so the
MCU and the module start together. The module keeps its stored settings
across the upgrade, so the same `mill-XXXX` network comes back. Rejoin it.

## 5. Verify firmware and heater control

Firmware:

```bash
python3 tools/mill_probe.py
#   AT+VER    -> +ok=2.0.10,11 (2018-11-22 1737)
#   AT+APPVER -> +ERR=-1               (Mill app layer gone)
```

The UART bridge, and a temperature round trip you can see on the heater's
display:

```bash
python3 tools/mill.py --host 10.10.100.254 status
#   {'target_c': 21, 'mode44': 0, 'heat_on': True, 'heating': False, 'room_c': 21}
python3 tools/mill.py --host 10.10.100.254 set 18      # LCD changes to 18
python3 tools/mill.py --host 10.10.100.254 power off   # LCD shows "OF"
python3 tools/mill.py --host 10.10.100.254 power on    # LCD back to 18
python3 tools/mill.py --host 10.10.100.254 set 21      # restore
python3 tools/mill_listen.py --host 10.10.100.254      # press +/- on the heater, watch 0xc9 frames
```

Every command prints the echo, a `0xc9` status and the decoded state. See
[PROTOCOL.md](PROTOCOL.md) for the field map.

## 6. Join your network

```bash
python3 tools/mill_setwifi.py          # prompts for SSID and key; key never printed
```

Requirements for the network: **2.4 GHz, WPA2-PSK (AES)**, no WPA3 or
transition mode, PMF and 802.11r off, no `,` in SSID or key. A hidden SSID is
fine. The tool writes `AT+WSSSID` / `AT+WSKEY`, reads both back, switches to
`AT+WMODE=STA` and reboots. The `mill-XXXX` network disappears.

Reconnect your computer to your network (VPN back on if you use one). Find
the heater in your router's client list (hostname `HF-LPT120`, MAC
`34:EA:E7:…`) and give it a **DHCP reservation**. If the module cannot join,
`AT+MDCH=auto` brings its AP back after a few minutes; rerun this step from
the AP.

## 7. Lock down and add to Home Assistant

1. Change the web UI login (port 80, default `admin/admin`):
   `python3 tools/mill_setwifi.py <heater-ip> --web-login`.
2. Network isolation: see the README section
   [Network isolation](../README.md#network-isolation-recommended).
3. Add the integration: see the
   [README](../README.md#home-assistant-integration).

## Alternative: flash in station mode

If flashing on the AP does not work, do step 6 first (join your network), then
run steps 2 and 3 against the heater's LAN address from a computer on the same
network:

```bash
python3 tools/mill_flash.py --check <heater-ip> LPT120_UPGRADE_HFV2.10_2MB_20181123_11.bin
sudo python3 tools/mill_flash.py <heater-ip> LPT120_UPGRADE_HFV2.10_2MB_20181123_11.bin
```

The reference flash in [FINDINGS §5.4](FINDINGS.md#54-observed-behaviour--do-not-retry-atupurl)
was done this way. Keep the URL short (`http://` + IPv4 + `/` ≤ ~21 chars).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Pre-flight: module does not answer on UDP 48899 | Not on the heater's network, VPN on, or UDP lost. Check the SSID, disable the VPN, retry |
| Pre-flight: URL warning | VPN or a second interface picked a different source IP. Disable the VPN, or pass `--serve-ip 10.10.100.15x` |
| Pre-flight: HTTP port 80 FAIL | Run with `sudo`, or stop whatever listens on port 80 |
| `unzip` stops with "write error" on a PDF | macOS `unzip` and GBK file names. Use `unzip -j … <image path>` as in step 0 |
| Every AT reply is "off by one", `+ERR=-1` to the handshake | Module was left in command mode. The tools drain the socket; otherwise send `AT+Q` and retry |
| Repeated `GET` lines, no `+ok`, version unchanged | `AT+UPURL` was resent. Wait 2 min, power-cycle, run the tool once |
| No `GET` at all after `AT+UPURL` | The module cannot reach your HTTP server. Use the station-mode alternative |
| Version unchanged after `+ok` | Wrong image (no header) or a truncated URL. Check the hash and URL length |
| `mill-XXXX` not back after flashing | Wait 2–3 min, then power-cycle. Check with `mill_probe.py` on the heater AP |
| TCP 8899 drops every 300 s | `AT+TCPTO`. Clients must transmit periodically (the integration does) |

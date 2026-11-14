# Flashing stock firmware onto the Mill Gen 2 Wi-Fi module

This replaces Mill's firmware on the heater's HF-LPT120 Wi-Fi module with
High-Flying's stock firmware, which exposes the heater MCU on TCP 8899. Read
[FINDINGS.md](FINDINGS.md) first.

> **Risk:** one-way over Wi-Fi. The Mill app/cloud will no longer work with
> the heater. A failed flash can leave the module unbootable; recovery then
> needs the serial bootloader (open the heater, UART0 230400 8N1, nReload,
> XMODEM). The heater itself keeps working from its buttons regardless: the
> MCU is separate.

## Requirements

- A computer with Python 3.9+, on the same network as the heater. Port 80
  must be free, and `sudo` is needed to bind it.
- A **2.4 GHz WPA2-PSK (AES)** network: no WPA3 or transition mode, PMF off,
  no `,` in SSID or key. A hidden SSID is fine.
- The firmware package in [`firmware/`](../firmware/README.md) (see step 4). Do not use any other
  image.
- This repository (`tools/`).

## 1. Put the heater in pairing (AP) mode

Start Wi-Fi pairing on the heater as described in Mill's manual. The Wi-Fi
indicator blinks. The module then opens its own access point:

| | |
|---|---|
| SSID | `mill-XXXX` (last 4 hex digits of the module MAC) |
| Key | `123456789` |
| Module IP | `10.10.100.254` |
| Your IP | DHCP from `10.10.100.150-200` |

Join that network from your computer. There is no real captive portal; the
interface is the UDP 48899 AT channel.

## 2. Check the module

```bash
python3 tools/mill_probe.py            # defaults to 10.10.100.254
```

Expected (Mill firmware):

```
hello -> b'10.10.100.254,34EAE7xxxxxx,HF-LPT120'
AT+VER -> b'+ok=2.0.10,2 (2017-02-28 1012)\r\n\r\n'
AT+APPVER -> b'+ok=1.1,ZC_version[1.0.8],Dec 14 2017-16:27:09\r\n\r\n'
AT+WMODE -> b'+ok=AP\r\n\r\n'
AT+NETP -> b'+ok=TCP,Server,8899,10.10.100.254\r\n\r\n'
```

`ZC_version` in `AT+APPVER` means the Mill/AbleCloud app layer is running.

## 3. Join your network

```bash
python3 tools/mill_setwifi.py          # prompts for SSID and key; key never printed
```

The tool writes `AT+WSSSID` / `AT+WSKEY`, reads both back and verifies them,
then switches to `AT+WMODE=STA` and reboots (`AT+Z`). The heater's AP
disappears. Reconnect your computer to your normal network and find the
heater's IP in your router's client list (hostname `HF-LPT120`, MAC
`34:EA:E7:…`). Give it a **DHCP reservation** now; the integration and your
firewall rules refer to it.

If the module cannot join, `AT+MDCH=auto` brings its AP back after a few
minutes. Rerun from step 1.

## 4. Download and verify the firmware

The package is in this repo, unmodified: `firmware/HF-LPB120_LPT120_LPT220_LPB125_SIP120_Frmware_V2.10-11.zip`
(source: hi-flying.com, *HF-LPB120/LPT120/LPT220/LPB125/SIP120 Firmware V2.10-11*;
the site may only work over plain `http`).

```bash
shasum -a 256 HF-LPB120_LPT120_LPT220_LPB125_SIP120_Frmware_V2.10-11.zip
# 01177e03ffe377fd78b28bb983de63dc04c4bd5c503e50fafffc92c171472884
unzip HF-LPB120_LPT120_LPT220_LPB125_SIP120_Frmware_V2.10-11.zip
shasum -a 256 "LPX120 V2.10-11/2.10-11/Firmware/LPT120_UPGRADE_HFV2.10_2MB_20181123_11.bin"
# eafed8d9a70a28ee732f579851b015b0d10a939c68ef972a8a7739270718eeeb
```

Use the **`LPT120_UPGRADE_…`** file. It starts with the 128-byte
`HF-MC3000 Image` header that OTA requires. The tool refuses images without
it. Do not use the LPB120/LPB125/LPT220 images.

## 5. Flash

```bash
sudo python3 tools/mill_flash.py <heater-ip> LPT120_UPGRADE_HFV2.10_2MB_20181123_11.bin
```

The tool serves the image on port 80 of your computer and sends
`AT+UPURL=http://<your-ip>/,<file>` **once**, then polls. A good run looks
like this:

```
23:11:13  serving LPT120_UPGRADE_…bin (373840 bytes, OTA image with header) from 192.168.1.10
23:11:14  hello -> 192.168.1.50,34EAE7xxxxxx,HF-LPT120
23:11:14  AT+VER -> +ok=2.0.10,2 (2017-02-28 1012)
23:11:15  AT+APPVER -> +ok=1.1,ZC_version[1.0.8],Dec 14 2017-16:27:09
23:11:16  HTTP 192.168.1.50 "GET /LPT120_UPGRADE_…bin HTTP/1.1" 200 -
23:11:21  HTTP sent 373840 bytes
23:11:26  AT+UPURL=http://192.168.1.10/,LPT120_UPGRADE_…bin -> +ok
23:11:26  waiting up to 4 min for download, flash and reboot
...       module not answering (flashing/rebooting)
23:13:46  AT+VER -> +ok=2.0.10,11 (2018-11-22 1737)
23:13:46  AT+APPVER -> +ERR=-1
23:13:47  tcp 8899 OPEN
23:13:47  tcp 80 OPEN
```

Rules:

- **Never resend `AT+UPURL`** while a download is in progress. Each resend
  starts a new download, and the module never completes. The `+ok` arrives
  only after the whole image has been read (~10 s).
- Keep the computer awake, and do not power-cycle the heater during the ~2
  minutes after `+ok`.
- If your URL is longer than ~21 characters (`http://` + IP + `/`), the module
  may truncate it. Serve from a short IPv4.

## 6. Power-cycle and verify

The module reboots by itself. Power-cycling the heater afterwards is still
recommended, so the MCU and the module start cleanly together: unplug it for
10 s, plug it back in and wait about 1 minute.

```bash
python3 tools/mill_probe.py <heater-ip>
#   AT+VER    -> +ok=2.0.10,11 (2018-11-22 1737)
#   AT+APPVER -> +ERR=-1               (Mill app layer gone)
python3 tools/mill.py --host <heater-ip> status
#   {'target_c': 21, 'mode44': 0, 'heat_on': True, 'heating': False, 'room_c': 21}
python3 tools/mill_listen.py --host <heater-ip>   # press +/- on the heater, watch 0xc9 frames
```

Stored Wi-Fi settings survive the upgrade, so the heater rejoins the same
network.

## 7. Lock it down

Stock firmware exposes three unauthenticated or default-credential interfaces
(see [FINDINGS §8](FINDINGS.md#8-security-posture)):

1. Change the web UI login (port 80, default `admin/admin`):
   `python3 tools/mill_setwifi.py <heater-ip> --web-login`.
2. Move the heater to an IoT VLAN or SSID. Allow only Home Assistant → heater
   TCP 8899 (plus UDP 48899 if you want MAC-based setup), with return traffic.
   Block IoT → LAN and IoT → Internet.

## 8. Add it to Home Assistant

See the [README](../README.md#home-assistant-integration).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| No reply to `HF-A11ASSISTHREAD` | Not on the heater's network, or UDP blocked. Retry: UDP is lossy |
| Every AT reply is "off by one", `+ERR=-1` to the handshake | Module was left in command mode. Tools drain the socket; otherwise send `AT+Q` and retry |
| Repeated `GET` lines, no `+ok`, version unchanged | `AT+UPURL` was resent. Wait 2 min, power-cycle, run the tool again once |
| Version unchanged after `+ok` | Wrong image (no header) or a truncated URL. Check the hash and URL length |
| Heater gone from the network after flashing | Wait 2–3 min, then power-cycle. If its AP `mill-XXXX` reappears, go back to step 3 |
| TCP 8899 drops every 300 s | `AT+TCPTO`. Clients must transmit periodically (the integration does) |

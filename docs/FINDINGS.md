# Mill Gen 2 Wi-Fi heater – technical findings

Reverse-engineering notes for a Mill Gen 2 **glass** panel heater with Wi-Fi.
Goal: local control from Home Assistant without the (defunct) vendor cloud.

All addresses, MACs and SSIDs below are anonymised. Captures are verbatim
otherwise. Byte indices are 0-based and **include** the `0x5a` start byte.

---

## 1. Summary

| Item | Finding |
|---|---|
| Wi-Fi module | High-Flying **HF-LPT120** (OUI `34:EA:E7`), 2.4 GHz 802.11b/g/n |
| Module ↔ heater MCU | UART, **9600 8N1**, no flow control |
| Shipped firmware | HSF core **2.0.10-2** (2017-02-28) + AbleCloud "ZC" device SDK **1.0.8** app layer |
| Vendor cloud | AbleCloud. The app pairing flow fails; the agent never produced internet traffic on the test network |
| Open interfaces (shipped) | **UDP 48899** only (unauthenticated AT command channel). TCP 1–65535 closed |
| UART bridge (shipped) | Disabled in the vendor app layer, although `AT+NETP` reports `TCP,Server,8899` |
| Fix | OTA-flash stock High-Flying HSF **2.0.10-11** via `AT+UPURL`. The stock app is a transparent TCP 8899 ⇄ UART bridge |
| Result | Full local control: target 5–35 °C, heating on/off, room temperature, heating demand, all push-based |
| Cost | The Mill app/cloud path is gone for good (it did not work anyway). Firmware change is one-way over Wi-Fi |

---

## 2. Hardware and architecture

```
 ┌──────────────┐   UART 9600 8N1   ┌──────────────────────┐   2.4 GHz
 │ Heater MCU   │ ◄───────────────► │ HF-LPT120 Wi-Fi SoM  │ ◄──────── LAN
 │ (LCD, keys,  │   16/17-byte      │ HSF core + app layer │
 │  triac, NTC) │   0x5a..0x5b      └──────────────────────┘
 └──────────────┘   frames
```

The MCU owns everything heater-related (setpoint, element, display, buttons,
room sensor). The Wi-Fi module only relays frames; it never interprets them.
Identification from the module itself:

```
hello  -> 192.168.1.50,34EAE7xxxxxx,HF-LPT120
AT+VER    -> +ok=2.0.10,2 (2017-02-28 1012)
AT+APPVER -> +ok=1.1,ZC_version[1.0.8],Dec 14 2017-16:27:09
AT+UART   -> +ok=9600,8,1,None,NFC
```

`ZC_version` is the AbleCloud device SDK (Mill's former cloud provider), not
Zigbee. Routers that fingerprint by DHCP/OUI may label the device as another
product built on High-Flying modules (UniFi showed a GoodWe inverter).

---

## 3. Shipped (Mill) firmware behaviour

### 3.1 Provisioning state

Unprovisioned, the module runs as an access point and holds placeholder
station credentials:

```
AT+WMODE  -> +ok=AP
AT+WAP    -> +ok=11BGN,mill-XXXX,AUTO          # XXXX = last 4 hex of the MAC
AT+WAKEY  -> +ok=WPA2PSK,AES,123456789          # vendor default AP key
AT+LANN   -> +ok=10.10.100.254,255.255.255.0
AT+WADHCP -> +ok=on,150,200                     # clients get 10.10.100.150-200
AT+WSSSID -> +ok=111111                         # placeholder STA SSID
AT+WSKEY  -> +ok=WPA2PSK,AES,222222222          # placeholder STA key
```

The Mill app never delivered real credentials (the phone → module step of the
pairing flow timed out), so the placeholders stayed. Writing credentials
directly over the AT channel works (see `tools/mill_setwifi.py`).

### 3.2 Station-mode constraints

| Setting | Requirement |
|---|---|
| Band | 2.4 GHz only |
| Security | WPA2-PSK (AES). WPA3 and WPA2/WPA3 transition mode fail to associate |
| PMF / 802.11r | Off |
| Channel width | 20 MHz recommended, channel 1/6/11 |
| Hidden SSID | Works (SSID is set explicitly) |
| Characters | No `,` in SSID or key – it is the AT argument separator |

### 3.3 Exposed services

| Port | Shipped firmware | Notes |
|---|---|---|
| UDP 48899 | **open** | HF config channel, no authentication |
| TCP 80 | refused | Even though `AT+WEBU -> admin,admin` is set |
| TCP 8899 | refused | Even though `AT+NETP -> TCP,Server,8899,10.10.100.254` |
| TCP 1–65535 | all closed | Full scan in STA mode |

### 3.4 What was tried before replacing the firmware

| Attempt | Result | Conclusion |
|---|---|---|
| Socket B as TCP client → host:9000 (`AT+SOCKB=TCP,9000,<host>`) | Setting stored, never connected | Socket B not serviced by the vendor app |
| Socket B as UDP server | No datagrams in 35 s, frames sent to it not acted on | No network → UART path |
| `AT+TMODE`, `AT+UARTTE`, `AT+UDPLCPT` | `+ERR=-1` | Commands removed from the vendor build |
| `AT+RELD` (restore defaults) | Restores **Mill's** defaults (AP `mill-XXXX`, STA `111111`), app layer unchanged, 8899/80 still refused | The vendor saved its config as factory default. The bridge is disabled in firmware, not configuration |

Other vendor defaults seen: `AT+UPURL -> http://10.10.100.100/` (OTA path
retained), `AT+NTPSER -> 171.66.97.126`, `AT+MDCH -> auto` (falls back to AP
mode if the STA network cannot be joined).

---

## 4. The UDP 48899 AT command channel

Available in AP and STA mode, in both the vendor and the stock firmware. There
is no authentication; the "password" is a fixed string.

```
→ "HF-A11ASSISTHREAD"            (to <module-ip>:48899)
← "192.168.1.50,34EAE7xxxxxx,HF-LPT120"
→ "+ok"                          enter command mode (no reply)
→ "AT+VER\r"
← "+ok=2.0.10,11 (2018-11-22 1737)\r\n\r\n"
→ "AT+Q\r"                       leave command mode
```

- **Discovery:** broadcast `HF-A11ASSISTHREAD` to `<subnet>.255:48899`; every
  HF module answers with `<ip>,<mac>,<model>`.
- **Errors:** `+ERR=-1` = unknown/unsupported command; other negative codes are
  argument errors.
- **State gotcha:** the module stays in command mode until `AT+Q`. If a previous
  session did not leave it, the `+ok` handshake itself is answered with
  `+ERR=-1`, and every following reply is shifted by one. Drain the socket
  (read until timeout) before every command. All tools in `tools/` do this.
- **UDP is lossy:** retry the hello. On a missing reply, re-handshake before
  resending a command, except for `AT+UPURL` (see §5.4).
- `AT+H` lists the commands the running build supports.
- Most settings take effect after `AT+Z` (reboot).

Commands used in this project:

| Command | Purpose |
|---|---|
| `AT+VER`, `AT+APPVER`, `AT+BVER` | Core, app-layer and bootloader version |
| `AT+WMODE=STA\|AP` | Station or access-point mode |
| `AT+WSSSID=<ssid>`, `AT+WSKEY=WPA2PSK,AES,<key>` | Station credentials |
| `AT+WSLK`, `AT+WSLQ` | Link status and quality, e.g. `+ok=Good, 100%` |
| `AT+WANN` | STA IP configuration, e.g. `+ok=DHCP,192.168.1.50,255.255.255.0,192.168.1.1` |
| `AT+NETP` | Socket A: `TCP,Server,8899,<ip>` = the UART bridge |
| `AT+TCPTO[=<s>]` | Socket A TCP idle timeout, default 300 s (see §7) |
| `AT+SOCKB` | Socket B configuration |
| `AT+WEBU=<user>,<pass>` | Web UI login (default `admin,admin`) |
| `AT+UPURL=<url>,<file>` | Download and flash an OTA image (see §5) |
| `AT+RELD`, `AT+Z`, `AT+Q` | Restore defaults, reboot, leave command mode |

---

## 5. Replacing the firmware with stock High-Flying HSF

### 5.1 Package

Official High-Flying release *HF-LPB120/LPT120/LPT220/LPB125/SIP120 Firmware
V2.10-11* (HSF 2.0.10-11, built 2018-11-23), included unmodified in
[`firmware/`](../firmware/README.md). Verify the hashes:

| File | Size | SHA-256 |
|---|---|---|
| `HF-LPB120_LPT120_LPT220_LPB125_SIP120_Frmware_V2.10-11.zip` | 4 021 528 | `01177e03ffe377fd78b28bb983de63dc04c4bd5c503e50fafffc92c171472884` |
| `LPT120_UPGRADE_HFV2.10_2MB_20181123_11.bin` (OTA) | 373 840 | `eafed8d9a70a28ee732f579851b015b0d10a939c68ef972a8a7739270718eeeb` |
| `LPT120_HFV2.10_11_2MB_20181123.bin` (serial) | 373 712 | `c63e0b7883b0db2b220f10c9ced831e0024f314fcddfb14ab3ed6c5db37a7208` |

### 5.2 Image formats

The `*_UPGRADE_*` file is the plain image prefixed with a 128-byte header:

```
00000000  48 46 2d 4d 43 33 30 30 30 20 49 6d 61 67 65 ..  "HF-MC3000 Image"
          ... image length 0x05b3d0, CRC ...
00000080  <plain image, byte-identical to LPT120_HFV2.10_11_2MB_20181123.bin>
```

- `*_UPGRADE_*`: for OTA (`AT+UPURL`, `AT+OTA`, HFUpdate). The header lets the
  module validate the download before it writes flash.
- Plain image: for the serial bootloader (UART0 230400 8N1, hold nReload at
  power-up, press space, `S`, XMODEM). This is the recovery path if OTA ever
  bricks a module, but it requires opening the heater.

### 5.3 Upgrade over Wi-Fi with `AT+UPURL`

`AT+UPURL=<url ending in />,<filename>` makes the module fetch
`<url><filename>` over HTTP and, if a filename is given, start the upgrade
immediately. The vendor build kept this command (its default URL
`http://10.10.100.100/` hints at the vendor's own update path). The URL field
is short: the manual says "within 20 characters". A 21-character URL
(`http://192.168.1.123/`) worked, so serve from a plain IPv4 on port 80.

`tools/mill_flash.py` runs an HTTP server, issues the command once and polls
the module until the app layer reports the stock build.

### 5.4 Observed behaviour – do not retry `AT+UPURL`

**Run 1 – failed (retry loop):** the module replies only after the download
finishes (~10 s). The tool's 3 s timeout fired, it re-handshook and resent the
command, and each resend started a **new** download. The module never
finished and never rebooted:

```
23:05:37  HTTP 192.168.1.50 "GET /lpt120.bin HTTP/1.1" 200 -
23:05:49  HTTP 192.168.1.50 "GET /lpt120.bin HTTP/1.1" 200 -
23:06:00  HTTP 192.168.1.50 "GET /lpt120.bin HTTP/1.1" 200 -
23:06:10  AT+UPURL=http://192.168.1.10/,lpt120.bin -> <no reply>
...
23:10:15  last version reading: ('+ok=2.0.10,2 (2017-02-28 1012)', '+ok=1.1,ZC_version[1.0.8],...')
```

**Run 2 – success (single send, 30 s reply window):**

```
23:11:13  serving lpt120.bin (373840 bytes, UPGRADE (with header))
23:11:21  HTTP sent 373840 bytes
23:11:26  AT+UPURL=http://192.168.1.10/,lpt120.bin -> +ok
~23:13    module reboots by itself
23:13:46  hello 192.168.1.50,34EAE7xxxxxx,HF-LPT120
          AT+VER    -> +ok=2.0.10,11 (2018-11-22 1737)
          AT+APPVER -> +ERR=-1                 # vendor app layer gone
          AT+NETP   -> +ok=TCP,Server,8899,10.10.100.254
          tcp 8899 OPEN, tcp 80 OPEN
```

Settings stored by the vendor firmware (STA credentials, UART 9600 8N1, NETP)
survived the upgrade, so the module came back on the same network.

### 5.5 After the upgrade

| Port | Stock HSF 2.0.10-11 |
|---|---|
| UDP 48899 | AT channel (unchanged, no auth) |
| TCP 8899 | **Transparent UART bridge**, no auth |
| TCP 80 | Web UI `/index_en.html`, `/iweb.html` (Basic auth `admin/admin` until changed) – Wi-Fi settings and firmware upload |

There is no way to read the vendor firmware back out over Wi-Fi, so the
change cannot be undone without a serial dump taken beforehand.

---

## 6. Heater MCU protocol

The bridge gives byte-transparent access to the MCU UART. Full reference:
[`PROTOCOL.md`](PROTOCOL.md). In short:

- 16-byte commands `5a 00 10 <seq> 00 <cmd> <a1> 00 <a2> 00×5 <sum> 5b`, where
  `sum` is the sum of bytes 1–13, mod 256.
- `0x46` + `0x50` set the target temperature, `0x47` switches heating on/off,
  and `0x50` alone reads the target.
- The MCU echoes every command, pushes a 17-byte `0xc9` status on every change
  (setpoint, buttons, room temperature) and sends an all-zero `0xcb` frame
  after command responses.
- The `0xc9` layout of this glass heater differs from the panel-heater layout
  that existing ESPHome code assumes (§9).

---

## 7. Link supervision: `AT+TCPTO`

The stock module closes the TCP 8899 server connection after `AT+TCPTO`
seconds (**default 300**) **without data received from the client**. From the
HF-LPT120 manual, 4.2.2.25:

> Module begin to count time when TCP channel don't receive any data, clear
> time counter when TCP channel receive any data. If the time counter reaches
> the TCPTO, the tcp channel will be break.

Traffic from the MCU to the client does **not** reset this timer. A
push-only client that just listens gets disconnected every 300 s. Home
Assistant log before the fix:

```
12:04:38 ERROR [mill_local] ... connection to heater lost
12:09:44 ERROR [mill_local] ... connection to heater lost
12:14:50 ERROR [mill_local] ... connection to heater lost      # every 300 s + 5 s backoff
```

A first fix (keepalive only after 60 s of RX silence) still dropped. The MCU
had just pushed a status frame, so the client never transmitted:

```
16:04:56  climate.mill  off            # 0xc9 received
16:05:00  climate.mill  unavailable    # module closed the socket, 300 s after the client's last TX
```

**Fix in the integration:** send `0x50` every 60 s **regardless of RX**
(`MillClient._keepalive`). It is side-effect free and the MCU answers with
the target, which also proves the MCU side is alive. A separate RX watchdog
(150 s without any byte) detects a dead link. The alternative,
`AT+TCPTO=0` (no timeout), works too, but then a half-open connection is
never reaped on the module side, so the client-side keepalive is the better
fix. Regression tests in `tests/test_keepalive.py` simulate the module,
including TCPTO and a chatty MCU.

---

## 8. Security posture

| Interface | Authentication | Capability |
|---|---|---|
| UDP 48899 | none (fixed hello string) | Everything: Wi-Fi credentials, reboot, factory reset, **firmware flash** |
| TCP 8899 | none | Heater control (setpoint 5–35 °C, on/off) |
| TCP 80 | HTTP Basic, default `admin/admin` | Wi-Fi settings, firmware upload |

Anyone on the same L2/L3 segment can reflash or reconfigure the module. The
login change (`AT+WEBU`, `tools/mill_setwifi.py --web-login`) only covers
port 80. Recommended network isolation:

- Dedicated IoT VLAN and SSID (2.4 GHz, WPA2, client isolation on).
- Allow only the Home Assistant host → heater on TCP 8899 (and UDP 48899 for
  MAC lookup during setup), with return traffic allowed.
- Block IoT → LAN and IoT → Internet. The stock firmware has no cloud
  dependency. The vendor firmware tried NTP only.

---

## 9. Open questions

| Item | Status |
|---|---|
| `0x44` with `a1=01` | Sets `status[6]=04` and target 8 °C (frost/away?). `a1=00` restores the previous target. It is **not** power on this heater |
| `status[6]` | 00/01/04 observed. Reacts to `0x44`. Exact meaning unknown |
| `0xcb` frame | Always all-zero payload. Follows command responses and occasionally appears unsolicited. Purpose unknown |
| `0x48` (fan) | Accepted and echoed. No visible effect on the glass heater |
| Fractional temperatures | `0xc9` carries whole degrees only |

## 10. Related work

- [metrafonic/MillHeat-ESPHome](https://github.com/metrafonic/MillHeat-ESPHome): ESP8266 replacement firmware. Its send frames match the ones documented here byte-for-byte. Its `0xc9` receive map is for panel heaters and does not match this glass heater (it indexes without the `0x5a` byte and drops `0x0a` bytes).
- [JanOveSaltvedt/mill-mod-matter](https://github.com/JanOveSaltvedt/mill-mod-matter): source of the `0x47` power opcode and the `0xc9` field order.
- [ESPHome device page: Mill panel heater gen 2](https://devices.esphome.io/devices/Mill-Panelheater-gen2/)

# mill-heater-local

Local control of **Mill Gen 2 Wi-Fi heaters** (HF-LPT120 module) from Home
Assistant, with no cloud.

The heater's Wi-Fi module ships with Mill/AbleCloud firmware that disables
every local interface, and the cloud it depended on is gone. This project
documents the hardware and protocol, provides a guide and tooling to flash
the module with High-Flying's stock firmware (a transparent TCP ⇄ UART
bridge), and ships a **local-push Home Assistant integration** on top.

|---|---|
| Tested on | Mill Gen 2 glass panel heater, HF-LPT120, HSF 2.0.10-2 → 2.0.10-11 |
| Home Assistant | 2025.1+ (developed on 2026.11) |
| Control | Target 5–35 °C, heating on/off, room temperature, heating demand |
| Updates | Pushed by the heater MCU (no polling) |

> Not affiliated with Mill or High-Flying. Flashing is one-way over Wi-Fi and
> removes Mill app support. You do this at your own risk. Read
> [docs/FLASHING.md](docs/FLASHING.md) before you start.

## Documentation

| Document | Contents |
|---|---|
| [docs/FINDINGS.md](docs/FINDINGS.md) | Hardware, vendor firmware behaviour, open ports, the UDP 48899 AT interface, firmware images and OTA, `AT+TCPTO`, security, open questions |
| [docs/PROTOCOL.md](docs/PROTOCOL.md) | MCU UART frame format, opcodes, `0xc9` status map, annotated captures |
| [docs/FLASHING.md](docs/FLASHING.md) | Step-by-step: AP mode → Wi-Fi → verified OTA flash → power-cycle → verify → lock down |

## Home Assistant integration

`custom_components/mill_local` – domain `mill_local`, `iot_class: local_push`.

**Install:**

- **Home Assistant 2026.11+ Marketplace:** Settings → Marketplace → *Add from
  link* → `https://github.com/McFrappe/mill-heater-local` → Install → restart.
- **HACS:** Custom repositories → this URL, type *Integration* → Download →
  restart.
- **Manual:** copy `custom_components/mill_local` to
  `/config/custom_components/` and restart.

**Set up:**

- Heaters on the same subnet are **discovered via DHCP** (hostname
  `hf-lpt120*`, MAC `34:EA:E7*`). Discovery probes TCP 8899 first, so other
  HF-LPT120 products are ignored.
- Or *Add integration → Mill Gen 2 (local, HF-LPT120)* and enter the IP.
- The unique ID is the module MAC, read over UDP 48899. If the heater gets a
  new IP, re-adding it (or rediscovery) updates the existing entry and its
  title.
- Firewall: Home Assistant → heater **TCP 8899** (control) and **UDP 48899**
  (MAC lookup during setup), with return traffic allowed.

**Entity:** one `climate` entity per heater.

| Attribute | Source |
|---|---|
| `hvac_mode` heat/off | `0x47` power, `status[10]` |
| target temperature, 1 °C steps, 5–35 | `0x46` + `0x50`, `status[7]` |
| current temperature | `status[8]` (whole degrees) |
| `hvac_action` heating/idle/off | `status[12]` |

**Connection handling:** one persistent TCP connection. The client sends a
`0x50` keepalive every 60 s, because the module drops clients that do not
transmit for `AT+TCPTO` (300 s). The link counts as lost after 150 s without
data, and reconnects with backoff from 5 to 60 s.

## Tools

Python 3, standard library only. Frame code is shared with the integration
(`custom_components/mill_local/protocol.py`).

| Tool | Purpose |
|---|---|
| `tools/mill_probe.py [ip]` | Read-only: versions, Wi-Fi mode/SSID, NETP |
| `tools/mill_setwifi.py [ip] [--web-login]` | Write and verify STA credentials; change the web UI login |
| `tools/mill_flash.py <ip> <image>` | OTA-flash the stock firmware via `AT+UPURL` |
| `tools/mill.py --host <ip> status\|set N\|power on/off\|raw <hex>` | Control the heater over TCP 8899 |
| `tools/mill_listen.py --host <ip>` | Passive frame logger: watch `0xc9` while pressing buttons |

## Tests

```bash
python3 tests/test_keepalive.py
```

The tests simulate the HF-LPT120: `0x50` replies, `AT+TCPTO` enforcement and
an optional chatty MCU. They check that a quiet or chatty but healthy module
never counts as lost, and that a module dying after connect does.

## Credits

- [metrafonic/MillHeat-ESPHome](https://github.com/metrafonic/MillHeat-ESPHome): command frame captures.
- [JanOveSaltvedt/mill-mod-matter](https://github.com/JanOveSaltvedt/mill-mod-matter): `0x47` power opcode, `0xc9` field order.
- High-Flying HF-LPT120 user manual and *Module Upgrade and Debug* guide.

## License

MIT – see [LICENSE](LICENSE). Firmware images and vendor manuals are **not**
included; obtain them from High-Flying.

# Mill Gen 2 heater – MCU UART protocol

Transport: HF-LPT120 UART ⇄ heater MCU at **9600 8N1**, no flow control. With
stock HSF firmware the module bridges it transparently to **TCP 8899**, so
bytes written to the socket reach the MCU unchanged.

Byte indices are 0-based and **include** the `0x5a` start byte. (Some other
projects index from the byte after `0x5a`, i.e. their `i` is our `i+1`.)

## 1. Framing

```
offset  0    1    2    3    4    5    6    7    8    9   10   11   12   13   14   15  (16)
       5a   00  len  seq   00  cmd   a1   00   a2  ...payload/zero...       sum   5b
```

| Field | Value |
|---|---|
| Start / end | `0x5a` … `0x5b` |
| `len` (byte 2) | Total frame length including start and end: `0x10` (16) for commands, `0x11` (17) for `0xc9` status |
| `seq` (byte 3) | Sender sequence number. The MCU echoes it and does not validate it. MCU-originated frames use `0x00` |
| `cmd` (byte 5) | Opcode |
| `sum` (byte `len-2`) | `sum(bytes[1 .. len-3]) & 0xff` – holds for 16- and 17-byte frames |

Reference builder (identical to `custom_components/mill_local/protocol.py`):

```python
def frame(seq: int, cmd: int, a1: int = 0, a2: int = 0) -> bytes:
    b = bytearray([0x5A, 0x00, 0x10, seq & 0xFF, 0x00, cmd, a1, 0x00, a2] + [0] * 5)
    b += bytes([sum(b[1:14]) & 0xFF, 0x5B])
    return bytes(b)
```

Stream parsing: scan for `0x5a`, read `len` at +2, require `buf[i+len-1] == 0x5b`
and a valid checksum; otherwise advance one byte (`split_frames()`).

## 2. Commands (host → MCU)

| Opcode | Name | Args | Effect on this glass heater |
|---|---|---|---|
| `0x46` | SET_TEMP | `a1=01`, `a2`=°C (5–35) | Stages a new target. Follow with `0x50` |
| `0x50` | APPLY | – | Commits staged changes. Replies with the target. Side-effect free when nothing is staged → used as a read and as a keepalive |
| `0x47` | POWER | `a1=01` on / `00` off | Heating on/off. Off: LCD shows `OF`, `status[10]=00` |
| `0x44` | MODE44 | `a1=01` / `00` | **Not power here.** `01` → `status[6]=04`, target 8 °C. `00` → previous target restored |
| `0x48` | FAN | `a1=01` / `00` | Accepted. No visible effect on the glass heater |

Send one frame at a time with ~200 ms spacing; the MCU processes frames
serially.

### Captured command frames

```
SET 14 °C   5a 00 10 1b 00 46 01 00 0e 00 00 00 00 00 80 5b
APPLY       5a 00 10 1c 00 50 00 00 00 00 00 00 00 00 7c 5b
SET 5 °C    5a 00 10 1d 00 46 01 00 05 00 00 00 00 00 79 5b
MODE44 off  5a 00 10 1f 00 44 00 00 00 00 00 00 00 00 73 5b
MODE44 on   5a 00 10 20 00 44 01 00 00 00 00 00 00 00 75 5b
FAN off     5a 00 10 26 00 48 00 00 00 00 00 00 00 00 7e 5b
FAN on      5a 00 10 27 00 48 01 00 00 00 00 00 00 00 80 5b
```

Checksum check for SET 14 °C: `00+10+1b+00+46+01+00+0e = 0x80`.

## 3. Responses (MCU → host)

Every command produces, in order:

1. an **echo** of the command, byte-identical;
2. for state-changing commands, a **`0xc9` status** frame (17 bytes);
3. an all-zero **`0xcb`** frame: `5a 00 10 00 00 cb 00 00 00 00 00 00 00 00 db 5b`.

### 3.1 `0x50` reply (16 bytes)

```
5a 00 10 43 00 50 00 16 16 01 00 00 00 00 d0 5b
                     ^^ target (0x16 = 22 °C)
```

Only byte 7 (target) is reliable. Byte 8 is **not** the room temperature here.

### 3.2 `0xc9` status (17 bytes)

```
idx   0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15 16
     5a 00 11 00 00 c9 MM TT RR 00 PP 00 HH 00 00 ss 5b
```

| Byte | Meaning | Confidence |
|---|---|---|
| 6 `MM` | Mode: `00` normal, `04` after `0x44 01`, `01` after `0x44 00` from mode 04 | Observed, meaning unknown |
| 7 `TT` | **Target °C** (5–35), matches the LCD | Confirmed (button capture: `-` held stops at `05`, `+` stops at `23` = 35) |
| 8 `RR` | **Room °C**, whole degrees; `00` = no reading yet | Confirmed (changed 22 → 20 with no input) |
| 10 `PP` | **Heating enabled**: `01` on / `00` off (follows `0x47`) | Confirmed |
| 12 `HH` | **Heating demand**: `01` when target > room (with hysteresis) | Confirmed (target 23 / room 22 → `01`; target 20 → `00`) |

The MCU pushes `0xc9` unprompted on every change: setpoint (panel buttons or
network), room temperature and power. The gear button is display-only and
sends nothing.

### 3.3 Annotated session (after flashing)

```
→ 5a 00 10 40 00 44 00 00 00 00 00 00 00 00 94 5b    MODE44 off
← 5a 00 10 40 00 44 00 00 00 00 00 00 00 00 94 5b    echo
← 5a 00 11 00 00 c9 01 16 16 00 01 00 00 00 00 08 5b status: mode 01, target 22, room 22, on
← 5a 00 10 00 00 cb 00 00 00 00 00 00 00 00 db 5b    cb
→ 5a 00 10 41 00 44 01 00 00 00 00 00 00 00 96 5b    MODE44 on
← 5a 00 11 00 00 c9 04 08 16 00 01 00 00 00 00 fd 5b status: mode 04, target 8 (LCD "08")
→ 5a 00 10 42 00 46 01 00 16 00 00 00 00 00 af 5b    SET 22
← 5a 00 11 00 00 c9 00 16 16 00 01 00 00 00 00 07 5b status: mode 00, target 22
→ 5a 00 10 43 00 50 00 00 00 00 00 00 00 00 a3 5b    APPLY
← 5a 00 10 43 00 50 00 16 16 01 00 00 00 00 d0 5b    reply: target 22
← 5a 00 11 00 00 c9 00 16 15 00 01 00 01 00 00 07 5b status: room 21, heating demand 1
```

Button capture (passive listener, `+` held):

```
00:10:36  5a 00 11 00 00 c9 04 08 16 00 01 00 00 00 00 fd 5b   target 8
00:10:41  5a 00 11 00 00 c9 04 09 16 00 01 00 00 00 00 fe 5b   target 9
...
00:12:46  5a 00 11 00 00 c9 04 23 16 00 01 00 01 00 00 19 5b   target 35, demand 1
```

## 4. Client sequence used by the integration

1. Connect to TCP 8899.
2. Send `0x50`. The reply gives the target.
3. If no `0xc9` has been seen yet, re-send the same target (`0x46` + `0x50`).
   This makes the MCU push a full `0xc9` with room temperature, power and
   demand. Side effect: leaves mode `04` (`status[6]` → `00`).
4. Keep the socket open and decode every pushed frame.
5. Send `0x50` every 60 s, regardless of received traffic, to keep the module's
   `AT+TCPTO` timer from expiring (see [FINDINGS §7](FINDINGS.md#7-link-supervision-attcpto)).
6. Treat 150 s without any received byte, or a socket close, as a lost link,
   and reconnect with backoff (5 → 60 s).

Avoid polling with `0x46` to force status pushes: it rewrites the setpoint
(EEPROM writes, and races with the panel buttons).

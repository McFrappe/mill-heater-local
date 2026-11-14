# High-Flying HSF firmware V2.10-11

`HF-LPB120_LPT120_LPT220_LPB125_SIP120_Frmware_V2.10-11.zip` is the
**unmodified** package from Shanghai High-Flying Electronics Technology, as
downloaded from hi-flying.com. It is included here so the flashing guide keeps
working if the vendor site goes away. © High-Flying. The package contains no
license terms; all rights remain with High-Flying.

| | |
|---|---|
| SHA-256 | `01177e03ffe377fd78b28bb983de63dc04c4bd5c503e50fafffc92c171472884` |
| Size | 4 021 528 bytes |
| Version | HSF 2.0.10-11, images built 2018-11-23 |
| Source | http://www.hi-flying.com/firmware-1/download-item-hf-lpb120-lpt120-lpt220-lpb125-sip120-frmware-v2-10-11 |

## Contents (`LPX120 V2.10-11/`)

| Path | Use |
|---|---|
| `2.10-11/Firmware/LPT120_UPGRADE_HFV2.10_2MB_20181123_11.bin` | **The image flashed in this project.** OTA image (128-byte `HF-MC3000 Image` header). SHA-256 `eafed8d9a70a28ee732f579851b015b0d10a939c68ef972a8a7739270718eeeb` |
| `2.10-11/Firmware/LPT120_HFV2.10_11_2MB_20181123.bin` | Same image without header, for the serial bootloader (XMODEM). SHA-256 `c63e0b7883b0db2b220f10c9ced831e0024f314fcddfb14ab3ed6c5db37a7208` |
| `2.10-11/Firmware/LPB120_*`, `LPB125_*`, `LPT120G_*`, `LPT220_*`, `SIP120_*` | Other modules. **Do not flash these on the Mill HF-LPT120** |
| `2.10-11/webpage.html` | Web UI page for the stock firmware |
| `Release Notes LPX120 v2.0.10-11.txt` | Change log (mostly Chinese) |
| `HF-LPB120_Module_Upgrade and Debug_V1.5.pdf` | Upgrade methods: serial bootloader, HFUpdate/OTA, web page |
| `HF-LPB120…V1.5.pdf` | Module manual (Chinese) |

Extract and verify:

```bash
unzip HF-LPB120_LPT120_LPT220_LPB125_SIP120_Frmware_V2.10-11.zip
shasum -a 256 "LPX120 V2.10-11/2.10-11/Firmware/LPT120_UPGRADE_HFV2.10_2MB_20181123_11.bin"
```

Then follow [docs/FLASHING.md](../docs/FLASHING.md).

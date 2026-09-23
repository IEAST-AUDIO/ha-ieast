# iEAST Audio for Home Assistant

Custom Home Assistant integration for iEAST multi-room audio devices
(eAMP / ePlay / eDante / i50 / i50B / M30 / M50 / AMP80 / OLIO series).

Local control via the device HTTP API and TCP 8899 (MCU passthrough). No cloud
dependency. Model-aware DSP mounting: PEQ / MaxxAudio entities are only created
when the device reports DSP support (probed via `PEQC` / `DPST` / `PKI`).

## Features

- Media player per device: play/pause/stop/seek, volume, mute, source selection
  (WiFi / Bluetooth / AUX / Optical / USB), EQ presets as sound modes,
  shuffle/repeat, metadata
- Multi-room: standard `media_player.join` / `unjoin`, party mode, group volume
- Intercom: `announce` (snapshot → TTS → restore) and live `page` broadcasting
  via master AUX
- Stereo pair (L/R via multiroom channel assignment)
- DSP (BP10 family): PEQ band editing, DPU parameter groups, sound scheme
  library (SCH), production parameter pack export/import (PK)
- Alarms, sleep timer, presets, 12V trigger, do-not-disturb, LED control
- Diagnostics download and a full `scan_device` developer probe action

## Installation

### HACS (recommended)

Add this repository as a custom repository (category: Integration), then
install "iEAST Audio".

### Manual

Copy `custom_components/ieast` into `config/custom_components/` and restart
Home Assistant.

## Configuration

Settings → Devices & Services → Add Integration → **iEAST Audio** → enter the
device IP. Devices are also auto-discovered via mDNS (`_linkplay._tcp`).

## Documentation

See the `examples/` directory for ready-to-use dashboard and automation
samples (whole-home multiroom panel, developer workbench, console scripts)
and `tools/` for the standalone device probe and protocol tests.

## License

MIT — see [LICENSE](LICENSE).

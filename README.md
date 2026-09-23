# iEAST Audio for Home Assistant

Custom Home Assistant integration for iEAST multi-room audio devices
(eAMP / ePlay / eDante / i50 / i50B / M30 / M50 / AMP80 / OLIO series).

Local control via the device HTTP API and TCP 8899. No cloud dependency.

## Features

- Media player per device: play/pause/stop/seek, volume, mute, source selection
  (WiFi / Bluetooth / AUX / Optical / USB), EQ presets as sound modes,
  shuffle/repeat, metadata
- Multi-room: standard `media_player.join` / `unjoin`, party mode, group volume
- Intercom: `announce` (snapshot → TTS → restore) and live `page` broadcasting
  via master AUX
- Stereo pair (L/R via multiroom channel assignment)
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

## Roadmap

This first release focuses on reliable core controls (playback, multiroom
grouping, intercom and daily-use automation). Device-specific advanced features
will be introduced in future updates once the product line is officially on
the market.

## License

MIT — see [LICENSE](LICENSE).

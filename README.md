# SteelSeries Battery Reader for NVDA

SteelSeries Battery Reader is an NVDA global plugin that announces the battery level of a supported SteelSeries headset.

## Usage

Press `NVDA+Shift+A` to hear the current battery level.

## Package layout

- `steelSeriesBattery/`: add-on source folder
- `steelSeriesBattery/globalPlugins/bin/headsetcontrol.exe`: bundled helper used for direct battery reads on Windows
- `test_arctis.py` and `test_enum.py`: local hardware probing scripts used during development

## Building

Package the contents of `steelSeriesBattery/` into `steelSeriesBattery.nvda-addon`.

## Notes

This add-on was tested with a SteelSeries Arctis Nova 5.

The add-on includes `headsetcontrol.exe` from the HeadsetControl project. See `steelSeriesBattery/globalPlugins/bin/HEADSETCONTROL-NOTICE.txt` and `steelSeriesBattery/globalPlugins/bin/HEADSETCONTROL-LICENSE.txt` for attribution and license details.

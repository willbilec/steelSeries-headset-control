# SteelSeries Headset Control for NVDA

SteelSeries Headset Control is an NVDA global plugin that reports battery level and provides EQ, sidetone, mic volume, and auto-off controls for compatible SteelSeries headsets (tested with Arctis Nova 5).

## Usage

### Battery
Press `NVDA+Shift+A` to hear the current battery level.

### EQ Control Layer
Press `NVDA+Shift+Q` to enter the EQ layer. Then press a single key:

| Key | Action |
|-----|--------|
| **S** | Announce current EQ preset and curve |
| **A** | Battery check |
| **1** | Flat preset (no EQ) |
| **2** | Bass preset (boosted low end) |
| **3** | Focus preset (clarity for footsteps/gaming) |
| **4** | Smiley preset (V-shaped curve) |
| **5** | Heavy Bass preset (aggressive bass boost) |
| **C** | Open custom 10-band EQ dialog (sliders) |
| **T** | Cycle sidetone level (Off → Low → Medium → High → Max) |
| **M** | Cycle mic volume (Low → Medium → High → Max) |
| **I** | Cycle auto-off timer (Never → 10min → 30min → 60min → 90min) |
| **Escape** | Cancel / exit layer |

Any other key also cancels the layer. The layer auto-cancels after 5 seconds of inactivity.

### Custom EQ (Direct)
Press `NVDA+Shift+Ctrl+E` to apply the custom 10-band EQ from the config file without entering the layer.

### Custom EQ Dialog
Press **C** in the EQ layer to open a dialog with 10 sliders. Each slider controls one frequency band (32 Hz to 16 kHz). Use Tab to move between sliders and arrow keys to adjust. Press Enter or click **Apply** to set the EQ.

### Config File
Settings are saved to `steelSeriesBattery_eq.json` in your NVDA user configuration folder:

```json
{
  "preset": 1,
  "custom_eq": [3.5, 5.5, 4.0, 1.0, -1.5, -1.5, -1.0, -1.0, -1.0, -1.0]
}
```

- `preset`: 0=Flat, 1=Bass, 2=Focus, 3=Smiley, 4=Heavy Bass, -1=Custom (use values from `custom_eq`)
- `custom_eq`: 10 values, -10 to +10 dB, 0.5 dB steps. Frequencies: 32, 64, 125, 250, 500, 1k, 2k, 4k, 8k, 16k Hz

### Auto-Apply on Startup
The add-on automatically applies the last-used EQ (preset or custom) when NVDA starts.

## Package layout

- `steelSeriesHeadsetControl/`: add-on source folder
- `steelSeriesHeadsetControl/globalPlugins/bin/headsetcontrol.exe`: bundled helper for direct headset communication
- `test_arctis.py` and `test_enum.py`: local hardware probing scripts

## Building

Package the contents of `steelSeriesHeadsetControl/` into `steelSeriesHeadsetControl.nvda-addon`:

```bash
cd steelSeriesHeadsetControl && zip -r ../steelSeriesHeadsetControl.nvda-addon * -x "__pycache__/*" "*.pyc"
```

## Notes

This add-on was tested with a SteelSeries Arctis Nova 5.

The add-on includes `headsetcontrol.exe` from the HeadsetControl project. See `steelSeriesHeadsetControl/globalPlugins/bin/HEADSETCONTROL-NOTICE.txt` and `HEADSETCONTROL-LICENSE.txt` for attribution and license details.

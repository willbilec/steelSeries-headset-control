# SteelSeries Headset Control Add-on for NVDA
# Copyright 2026, Antigravity
#
# Gestures:
#   NVDA+Shift+A       — Announce battery level
#   NVDA+Shift+Q       — Enter EQ layer (then press a sub-key):
#       S              — Status (announce current EQ)
#       A              — Battery check
#       1/2/3/4/5      — Presets: Flat / Bass / Focus / Smiley / Heavy Bass
#       C              — Open custom 10-band EQ dialog (sliders)
#       T              — Cycle sidetone: Off → Low → Medium → High → Max
#       M              — Cycle mic volume: Low → Medium → High → Max
#       I              — Cycle auto-off: Never → 10min → 30min → 60min → 90min
#       Escape         — Cancel / exit layer
#       (any other key cancels the layer)
#   NVDA+Shift+Ctrl+E  — Apply custom EQ from config (direct, no layer)
#
# On startup: auto-applies saved EQ preset/custom curve.
#
# Config file: steelSeriesBattery_eq.json in NVDA user config folder
#   {"preset": 1, "custom_eq": [3.5, 5.5, 4.0, 1.0, -1.5, -1.5, -1.0, -1.0, -1.0, -1.0]}
#   preset: 0=Flat, 1=Bass, 2=Focus, 3=Smiley, 4=Heavy Bass, -1=custom
#   custom_eq: 10 values between -10 and +10 dB (0.5 dB steps)

import globalPluginHandler
import ui
import core
import addonHandler
import threading
import ctypes
from ctypes import wintypes
import subprocess
import json
import os
import ssl
import urllib.parse
import urllib.request
import time
import wx

addonHandler.initTranslation()

FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

TARGET_VENDOR_ID = 0x1038
TARGET_PRODUCT_IDS = (0x2232, 0x2253)
TARGET_INTERFACE_NUMBER = 3
TARGET_USAGE_PAGE = 0xFFC0
TARGET_USAGE = 0x0001
CORE_PROPS_PATH = r"C:\ProgramData\SteelSeries\GG\coreProps.json"

# ── EQ constants ──────────────────────────────────────────────
EQ_CONFIG_FILENAME = "steelSeriesBattery_eq.json"
PRESET_NAMES = ["Flat", "Bass", "Focus", "Smiley", "Heavy Bass"]
PRESET_VALUES = {
    "flat":       [ 0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0,  0.0],
    "bass":       [ 3.5,  5.5,  4.0,  1.0, -1.5, -1.5, -1.0, -1.0, -1.0, -1.0],
    "focus":      [-5.0, -3.5, -1.0, -3.5, -2.5,  4.0,  6.0, -3.5,  0.0,  0.0],
    "smiley":     [ 3.0,  3.5,  1.5, -1.5, -4.0, -4.0, -2.5,  1.5,  3.0,  4.0],
    "heavy bass": [ 7.0,  9.0,  7.0,  4.0,  0.0, -2.0, -2.0, -2.0, -1.0,  0.0],
}
EQ_BAND_FREQS = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
EQ_LAYER_TIMEOUT_MS = 5000  # auto-cancel layer after 5s idle

class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE)
    ]

# CTypes setup
kernel32 = ctypes.windll.kernel32
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.GetLastError.restype = wintypes.DWORD

def write_overlapped(handle, data):
    hevent = kernel32.CreateEventW(None, True, False, None)
    ov = OVERLAPPED()
    ov.hEvent = hevent
    buf = (ctypes.c_ubyte * len(data))(*data)

    res = kernel32.WriteFile(handle, ctypes.byref(buf), len(data), None, ctypes.byref(ov))
    if not res:
        err = kernel32.GetLastError()
        if err == 997:  # ERROR_IO_PENDING
            if kernel32.WaitForSingleObject(hevent, 100) == 0:
                written = wintypes.DWORD()
                kernel32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(written), False)
            else:
                kernel32.CancelIo(handle)
                written = wintypes.DWORD()
                kernel32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(written), True)

    kernel32.CloseHandle(hevent)
    return buf, ov

def read_overlapped(handle):
    hevent = kernel32.CreateEventW(None, True, False, None)
    ov = OVERLAPPED()
    ov.hEvent = hevent
    buf = (ctypes.c_ubyte * 129)()

    res = kernel32.ReadFile(handle, ctypes.byref(buf), 129, None, ctypes.byref(ov))
    success = False

    if not res:
        err = kernel32.GetLastError()
        if err == 997:
            if kernel32.WaitForSingleObject(hevent, 1000) == 0:
                read_bytes = wintypes.DWORD()
                kernel32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(read_bytes), False)
                success = True
            else:
                kernel32.CancelIo(handle)
                read_bytes = wintypes.DWORD()
                kernel32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(read_bytes), True)
    else:
        read_bytes = wintypes.DWORD()
        kernel32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(read_bytes), False)
        success = True

    kernel32.CloseHandle(hevent)
    return (list(buf) if success else None), buf, ov

def announce(msg):
    core.callLater(0, ui.message, msg)

class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    scriptCategory = "SteelSeries Headset Control"

    def __init__(self):
        super(GlobalPlugin, self).__init__()
        self._check_thread = None
        self._cached_path = None
        self._cache_file = None

        # ── EQ state ──
        self._eq_layer_active = False
        self._eq_layer_timer = None
        self._eq_config_path = None
        self._active_preset = 0     # 0-4 = preset index, -1 = custom
        self._custom_eq = [0.0] * 10
        self._sidetone_level = 0    # 0-128, 0 = off
        self._mic_volume = 64       # 0-128
        self._inactive_time = 0     # 0-90 minutes, 0 = never

        # ── Build layer gesture map (bare key names — after last colon) ──
        # NVDA can send "kb:a", "kb(laptop):a", etc. We match on the key name only.
        self._eq_layer_map = {
            "s":          self.script_eqLayer_status,
            "a":          self.script_eqLayer_battery,
            "1":          self.script_eqLayer_flat,
            "2":          self.script_eqLayer_bass,
            "3":          self.script_eqLayer_focus,
            "4":          self.script_eqLayer_smiley,
            "5":          self.script_eqLayer_heavybass,
            "c":          self.script_eqLayer_custom,
            "t":          self.script_eqLayer_sidetone,
            "m":          self.script_eqLayer_micvolume,
            "i":          self.script_eqLayer_inactivetime,
            "escape":     self.script_eqLayer_cancel,
            # Also match entry gesture so it doesn't accidentally cancel the layer
            "nvda+shift+q": self.script_eqLayer_refresh,
        }

        try:
            import globalVars
            self._cache_file = os.path.join(globalVars.appArgs.configPath, "steelSeriesBattery_path.txt")
            if os.path.exists(self._cache_file):
                with open(self._cache_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        self._cached_path = content

            self._eq_config_path = os.path.join(globalVars.appArgs.configPath, EQ_CONFIG_FILENAME)
            self._load_eq_config()
        except Exception:
            pass

        # ── Auto-apply saved EQ on startup (delayed daemon thread) ──
        try:
            t = threading.Thread(target=self._startup_apply_eq, daemon=True)
            t.start()
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    #  Gesture routing — layered EQ commands
    # ═══════════════════════════════════════════════════════════

    def getScript(self, gesture):
        """Intercept all gestures when EQ layer is active."""
        if not self._eq_layer_active:
            return globalPluginHandler.GlobalPlugin.getScript(self, gesture)

        # NVDA sends identifiers like "kb:a", "kb(laptop):a", "kb:nvda+shift+q", etc.
        # Extract the bare key name after the last colon and match against our map.
        for gid in getattr(gesture, 'normalizedIdentifiers', []):
            key = gid.rsplit(':', 1)[-1] if ':' in gid else gid
            script = self._eq_layer_map.get(key)
            if script is not None:
                return script

        # Unrecognized — cancel the layer
        self._cancel_eq_layer()
        announce("EQ layer cancelled.")
        return None

    def _enter_eq_layer(self):
        """Activate the EQ command layer."""
        self._eq_layer_active = True
        self._reset_layer_timer()
        announce(
            "EQ layer. "
            "1 Flat, 2 Bass, 3 Focus, 4 Smiley, 5 Heavy Bass, "
            "C custom, T sidetone, M mic vol, I inactive, S status, A battery, Escape cancel."
        )

    def _cancel_eq_layer(self):
        """Deactivate the EQ command layer."""
        self._eq_layer_active = False
        if self._eq_layer_timer:
            self._eq_layer_timer.cancel()
            self._eq_layer_timer = None

    def _reset_layer_timer(self):
        """Reset the auto-cancel timer for the EQ layer."""
        if self._eq_layer_timer:
            self._eq_layer_timer.cancel()
        self._eq_layer_timer = threading.Timer(EQ_LAYER_TIMEOUT_MS / 1000.0, self._on_layer_timeout)
        self._eq_layer_timer.daemon = True
        self._eq_layer_timer.start()

    def _on_layer_timeout(self):
        """Called when the EQ layer times out."""
        if self._eq_layer_active:
            self._cancel_eq_layer()
            announce("EQ layer timed out.")

    # ═══════════════════════════════════════════════════════════
    #  EQ config persistence
    # ═══════════════════════════════════════════════════════════

    def _load_eq_config(self):
        """Load saved EQ + hardware config from JSON file."""
        if not self._eq_config_path or not os.path.exists(self._eq_config_path):
            return
        try:
            with open(self._eq_config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            preset = cfg.get("preset", 0)
            if isinstance(preset, int) and -1 <= preset <= 4:
                self._active_preset = preset
            custom = cfg.get("custom_eq")
            if isinstance(custom, list) and len(custom) == 10:
                self._custom_eq = [float(v) for v in custom]
            # --- Hardware settings: persist across NVDA restarts ---
            sidetone = cfg.get("sidetone")
            if isinstance(sidetone, int) and 0 <= sidetone <= 128:
                self._sidetone_level = sidetone
            mic = cfg.get("mic_volume")
            if isinstance(mic, int) and 0 <= mic <= 128:
                self._mic_volume = mic
            inactive = cfg.get("inactive_time")
            if isinstance(inactive, int) and 0 <= inactive <= 90:
                self._inactive_time = inactive
        except Exception:
            pass

    def _save_eq_config(self):
        """Save current EQ + hardware config to JSON file."""
        if not self._eq_config_path:
            return
        try:
            cfg = {
                "preset": self._active_preset,
                "custom_eq": self._custom_eq,
                "sidetone": self._sidetone_level,
                "mic_volume": self._mic_volume,
                "inactive_time": self._inactive_time,
            }
            with open(self._eq_config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    #  HeadsetControl helpers
    # ═══════════════════════════════════════════════════════════

    def _get_headsetcontrol_path(self):
        plugin_dir = os.path.dirname(__file__)
        bundled_path = os.path.join(plugin_dir, "bin", "headsetcontrol.exe")
        if os.path.exists(bundled_path):
            return bundled_path
        return None

    def _run_headsetcontrol(self, args, timeout=8):
        """Run headsetcontrol and return parsed JSON, or None on failure."""
        exe = self._get_headsetcontrol_path()
        if not exe:
            return None

        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        try:
            output = subprocess.check_output(
                [exe] + args,
                text=True,
                startupinfo=startupinfo,
                stderr=subprocess.STDOUT,
                timeout=timeout
            )
            return json.loads(output)
        except Exception:
            return None

    # ═══════════════════════════════════════════════════════════
    #  Custom EQ Dialog
    # ═══════════════════════════════════════════════════════════

    def _get_active_eq_values(self):
        """Return the 10 current EQ values (from active preset or custom)."""
        if self._active_preset >= 0:
            name = PRESET_NAMES[self._active_preset]
            return list(PRESET_VALUES[name.lower()])
        return list(self._custom_eq)

    def _show_custom_eq_dialog(self):
        """Open a wx dialog with 10 text fields for EQ adjustment.
        Up/Down arrow keys adjust values by ±0.5 dB for slider-like feel."""

        current_values = self._get_active_eq_values()

        class EqCustomDialog(wx.Dialog):
            def __init__(self, parent, values):
                super().__init__(parent, title="Custom 10-Band EQ",
                                 style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
                panel = wx.Panel(self)
                main_sizer = wx.BoxSizer(wx.VERTICAL)

                label = wx.StaticText(panel, label="Adjust each band (-10 to +10 dB). "
                    "Type a value or use Up/Down arrows (±0.5 dB):")
                main_sizer.Add(label, 0, wx.ALL, 10)

                self._fields = []
                freqs = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]

                for i, freq in enumerate(freqs):
                    if freq >= 1000:
                        freq_label = "{} kHz".format(freq // 1000)
                    else:
                        freq_label = "{} Hz".format(freq)

                    row_sizer = wx.BoxSizer(wx.HORIZONTAL)
                    lbl = wx.StaticText(panel, label=freq_label, size=(70, -1))
                    row_sizer.Add(lbl, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)

                    field = wx.TextCtrl(panel, value="{:.1f}".format(values[i]),
                                        size=(55, -1), style=wx.TE_PROCESS_ENTER)
                    field.SetName(freq_label)
                    # Bind arrow keys for slider-like behavior
                    field.Bind(wx.EVT_KEY_DOWN, self._on_field_key)
                    row_sizer.Add(field, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 15)

                    main_sizer.Add(row_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 4)
                    self._fields.append(field)

                # Focus first field after dialog is fully shown
                if self._fields:
                    wx.CallAfter(self._fields[0].SetFocus)

                btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
                ok_btn = wx.Button(panel, wx.ID_OK, "Apply")
                cancel_btn = wx.Button(panel, wx.ID_CANCEL, "Cancel")
                ok_btn.SetDefault()
                btn_sizer.Add(ok_btn, 0, wx.ALL, 5)
                btn_sizer.Add(cancel_btn, 0, wx.ALL, 5)
                main_sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 10)

                panel.SetSizer(main_sizer)
                main_sizer.Fit(self)

            def _on_field_key(self, event):
                key = event.GetKeyCode()
                field = event.GetEventObject()
                if key in (wx.WXK_UP, wx.WXK_DOWN):
                    try:
                        val = float(field.GetValue())
                    except ValueError:
                        val = 0.0
                    if key == wx.WXK_UP:
                        val += 0.5
                    else:
                        val -= 0.5
                    val = max(-10.0, min(10.0, val))
                    field.SetValue("{:.1f}".format(val))
                    field.SetSelection(-1, -1)  # select all for easy retyping
                else:
                    event.Skip()

            def get_values(self):
                vals = []
                for field in self._fields:
                    try:
                        v = float(field.GetValue())
                        vals.append(max(-10.0, min(10.0, v)))
                    except ValueError:
                        vals.append(0.0)
                return vals

        def _run_dialog():
            import gui
            dlg = EqCustomDialog(gui.mainFrame, current_values)
            dlg.CenterOnScreen()
            dlg.Raise()
            dlg.ShowModal()
            if dlg.GetReturnCode() == wx.ID_OK:
                values = dlg.get_values()
                dlg.Destroy()
                t = threading.Thread(target=lambda: self._apply_custom_eq(values))
                t.start()
            else:
                dlg.Destroy()
                announce("Cancelled.")

        self._cancel_eq_layer()
        wx.CallLater(100, _run_dialog)

    # ═══════════════════════════════════════════════════════════
    #  EQ actions
    # ═══════════════════════════════════════════════════════════

    def _apply_eq_preset(self, idx):
        """Apply a built-in EQ preset (0-4). Presets 0-3 use hardware presets;
        preset 4 (Heavy Bass) sends the values as a custom graphic EQ."""
        if idx < 0 or idx > 4:
            return False

        preset_name = PRESET_NAMES[idx]
        values = PRESET_VALUES[preset_name.lower()]

        if idx <= 3:
            # Hardware preset
            result = self._run_headsetcontrol(["-p", str(idx), "-o", "json"])
        else:
            # Heavy Bass — send as custom graphic EQ
            eq_str = " ".join(str(v) for v in values)
            result = self._run_headsetcontrol(["-e", eq_str, "-o", "json"])

        if result is None:
            announce("HeadsetControl not available.")
            return False
        actions = result.get("actions", [])
        ok = any(a.get("status") == "success" for a in actions)
        if ok:
            self._active_preset = idx
            self._save_eq_config()
            bands_desc = self._describe_eq(values)
            announce("{} preset. {}".format(preset_name, bands_desc))
        else:
            announce("Failed to apply {} preset.".format(preset_name))
        return ok

    def _apply_custom_eq(self, values):
        """Apply a custom 10-band EQ curve. Values are clamped to -10..+10 in 0.5 dB steps."""
        if len(values) != 10:
            announce("Custom EQ must have exactly 10 values.")
            return False
        clamped = [max(-10.0, min(10.0, round(float(v) * 2) / 2.0)) for v in values]
        eq_str = " ".join(str(v) for v in clamped)
        result = self._run_headsetcontrol(["-e", eq_str, "-o", "json"])
        if result is None:
            announce("HeadsetControl not available.")
            return False
        actions = result.get("actions", [])
        ok = any(a.get("status") == "success" for a in actions)
        if ok:
            self._active_preset = -1
            self._custom_eq = clamped
            self._save_eq_config()
            bands_desc = self._describe_eq(clamped)
            announce("Custom EQ applied. {}".format(bands_desc))
        else:
            announce("Failed to apply custom EQ.")
        return ok

    def _describe_eq(self, values):
        """Summarize EQ curve in words."""
        if not values or len(values) < 10:
            return ""
        bass = sum(values[0:4]) / 4.0    # 32-250 Hz
        mids = sum(values[4:7]) / 3.0     # 500-2000 Hz
        treble = sum(values[7:10]) / 3.0  # 4000-16000 Hz

        def _label(avg):
            if avg >= 4:
                return "boosted"
            elif avg >= 1.5:
                return "slightly boosted"
            elif avg <= -4:
                return "cut"
            elif avg <= -1.5:
                return "slightly cut"
            return "flat"

        parts = []
        if abs(bass) >= 1.0:
            parts.append("bass {}".format(_label(bass)))
        if abs(mids) >= 1.0:
            parts.append("mids {}".format(_label(mids)))
        if abs(treble) >= 1.0:
            parts.append("treble {}".format(_label(treble)))

        if not parts:
            return "All bands flat."
        return ", ".join(parts) + "."

    def _announce_current_eq(self):
        """Speak the current EQ state."""
        if self._active_preset >= 0:
            name = PRESET_NAMES[self._active_preset]
            values = PRESET_VALUES[name.lower()]
            bands_desc = self._describe_eq(values)
            announce("EQ: {} preset. {}".format(name, bands_desc))
        else:
            bands_desc = self._describe_eq(self._custom_eq)
            announce("EQ: Custom. {}".format(bands_desc))

    def _startup_apply_eq(self):
        """Apply saved EQ + hardware config after NVDA is fully loaded.

        Retries until the headset is responsive (bounded wait) instead of
        sleeping a fixed interval. Each push is verified via the JSON
        "status" field; failures are announced so silent firmware mismatches
        are visible to the user.
        """
        exe = self._get_headsetcontrol_path()
        if not exe:
            return

        # Wait up to ~15s for the headset to be ready. This is needed because
        # the SteelSeries GG service may still be holding the HID interface
        # when NVDA finishes initializing, and headsetcontrol will silently
        # fail if called too early.
        deadline = time.time() + 15
        while time.time() < deadline:
            probe = self._run_headsetcontrol(["-b"], timeout=3)
            if probe is not None:
                devices = probe.get("devices", [])
                if any(d.get("status") == "success" for d in devices):
                    break
            time.sleep(1)
        else:
            announce("Headset not ready at startup; EQ settings not applied.")
            return

        announcements = []

        def _push(label, args):
            """Try to push a single command. Returns True on success."""
            try:
                result = self._run_headsetcontrol(args)
                if result and any(a.get("status") == "success" for a in result.get("actions", [])):
                    announcements.append("{} restored.".format(label))
                    return True
                announcements.append("Failed to restore {}.".format(label))
            except Exception:
                announcements.append("Failed to restore {}.".format(label))
            return False

        # 1) EQ
        if self._active_preset >= 0 and self._active_preset <= 3:
            name = PRESET_NAMES[self._active_preset]
            _push("EQ: {} preset".format(name), ["-p", str(self._active_preset)])
        elif self._active_preset == 4:
            values = PRESET_VALUES["heavy bass"]
            eq_str = " ".join(str(v) for v in values)
            _push("EQ: Heavy Bass preset", ["-e", eq_str])
        else:
            eq_str = " ".join(str(v) for v in self._custom_eq)
            _push("EQ: Custom", ["-e", eq_str])

        # 2) Sidetone
        _push("Sidetone: {}".format(self._sidetone_level), ["-s", str(self._sidetone_level)])

        # 3) Microphone volume
        _push("Mic volume: {}".format(self._mic_volume), ["--microphone-volume", str(self._mic_volume)])

        # 4) Inactive time
        _push("Auto-off: {} min".format(self._inactive_time), ["-i", str(self._inactive_time)])

        for msg in announcements:
            announce(msg)

    # ═══════════════════════════════════════════════════════════
    #  Battery (existing functionality — unchanged)
    # ═══════════════════════════════════════════════════════════

    def _save_path(self, path):
        self._cached_path = path
        if self._cache_file:
            try:
                with open(self._cache_file, "w", encoding="utf-8") as f:
                    f.write(path)
            except Exception:
                pass

    def _clear_cached_path(self):
        self._cached_path = None
        if self._cache_file:
            try:
                import os as _os
                if _os.path.exists(self._cache_file):
                    _os.remove(self._cache_file)
            except Exception:
                pass

    def _matches_target_interface(self, dev):
        is_dict = isinstance(dev, dict)

        def get_value(*names, default=None):
            if is_dict:
                for name in names:
                    if name in dev:
                        return dev[name]
                return default
            for name in names:
                if hasattr(dev, name):
                    return getattr(dev, name)
            return default

        vid = get_value("vendor_id", "vendorID", default=0)
        pid = get_value("product_id", "productID", default=0)
        if vid != TARGET_VENDOR_ID or pid not in TARGET_PRODUCT_IDS:
            return False

        usage_page = get_value("usage_page", "usagePage", default=0)
        usage = get_value("usage", "usage_id", "usageID", default=0)
        interface_number = get_value("interface_number", "interfaceNumber", default=None)

        if usage_page == TARGET_USAGE_PAGE and usage == TARGET_USAGE:
            return True

        if interface_number == TARGET_INTERFACE_NUMBER:
            return True

        path = get_value("path", default=None)
        if isinstance(path, bytes):
            path = path.decode("utf-8", errors="ignore")
        if isinstance(path, str) and "mi_{:02x}".format(TARGET_INTERFACE_NUMBER) in path.lower():
            return True

        return False

    def _get_device_paths(self):
        paths = []
        try:
            import hwIo.hid
            for dev in hwIo.hid.enumerate():
                try:
                    if not self._matches_target_interface(dev):
                        continue

                    path = dev.get("path") if isinstance(dev, dict) else getattr(dev, "path", None)
                    if path:
                        if isinstance(path, bytes):
                            path = path.decode("utf-8", errors="ignore")
                        paths.append(path)
                except Exception:
                    continue
        except Exception:
            pass

        if not paths and self._cached_path:
            paths.append(self._cached_path)

        if not paths:
            try:
                cmd = ['powershell', '-NoProfile', '-Command',
                       r"Get-WmiObject Win32_PnPEntity | Where-Object { $_.PNPDeviceID -match 'VID_1038&PID_2232' -or $_.PNPDeviceID -match 'VID_1038&PID_2253' } | Select-Object -ExpandProperty PNPDeviceID"]
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

                out = subprocess.check_output(cmd, text=True, startupinfo=startupinfo)
                for line in out.splitlines():
                    line = line.strip()
                    if line.startswith('HID\\\\'):
                        path = '\\\\\\\\?\\\\' + line.replace('\\\\', '#') + '#{4d1e55b2-f16f-11cf-88cb-001111000030}'
                        paths.append(path)
            except Exception:
                pass

        deduped = []
        seen = set()
        for path in paths:
            if path and path not in seen:
                deduped.append(path)
                seen.add(path)
        return deduped

    def _http_get_json(self, url, timeout=4):
        context = ssl._create_unverified_context()
        request = urllib.request.Request(url)
        with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _get_live_battery_from_headsetcontrol(self):
        exe_path = self._get_headsetcontrol_path()
        if not exe_path:
            return None

        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        try:
            output = subprocess.check_output(
                [exe_path, "-b", "-o", "json"],
                text=True,
                startupinfo=startupinfo,
                stderr=subprocess.STDOUT,
                timeout=5
            )
            payload = json.loads(output)
        except Exception:
            return None

        for device in payload.get("devices", []):
            if device.get("status") != "success":
                continue

            product = str(device.get("product", ""))
            name = str(device.get("device", ""))
            if "Nova 5" not in product and "Nova (5/5X)" not in name:
                continue

            battery = device.get("battery", {})
            level = battery.get("level")
            if level is None:
                continue

            return {
                "percent": int(level),
                "charging": battery.get("status") == "BATTERY_CHARGING",
            }

        return None

    def _read_core_props(self):
        if not os.path.exists(CORE_PROPS_PATH):
            return None

        try:
            with open(CORE_PROPS_PATH, "r", encoding="utf-8") as f:
                props = json.load(f)
        except Exception:
            return None

        engine = props.get("encryptedAddress", "")
        gg = props.get("ggEncryptedAddress", "")
        if not engine or not gg:
            return None
        return props

    def _get_live_battery_from_gg(self):
        props = self._read_core_props()
        if not props:
            return None

        try:
            active_loadout_url = "https://{}/v1/loadouts/active".format(props["ggEncryptedAddress"])
            active_loadout = self._http_get_json(active_loadout_url).get("activeLoadout", {})
            loadout_id = active_loadout.get("id")
            if not loadout_id:
                return None

            device_name = "arctis_nova_5_tx"
            status_url = "https://{}/v2/device/config/loadout?device={}&loadoutId={}".format(
                props["encryptedAddress"],
                urllib.parse.quote(device_name, safe=""),
                urllib.parse.quote(str(loadout_id), safe="")
            )
            payload = self._http_get_json(status_url).get("loadoutConfiguration", {})
            statuses = payload.get("statuses", {})
            battery = statuses.get("batteryLevels", {})
            wireless = statuses.get("wirelessConnection", {})
            if not battery.get("statusKnown"):
                return None

            percent = battery.get("percent")
            if percent is None:
                return None

            return {
                "percent": int(percent),
                "charging": bool(battery.get("charging")),
                "wireless": wireless,
            }
        except Exception:
            return None

    def _queryBatteryAsync(self):
        headsetcontrol_path = self._get_headsetcontrol_path()
        if headsetcontrol_path:
            live_status = self._get_live_battery_from_headsetcontrol()
            if live_status is not None:
                if live_status["charging"]:
                    announce("{}% and charging".format(live_status["percent"]))
                else:
                    announce("{}%".format(live_status["percent"]))
            else:
                announce("HeadsetControl could not read the battery.")
            return

        live_status = self._get_live_battery_from_gg()
        if live_status is not None:
            if live_status["charging"]:
                announce("{}% and charging".format(live_status["percent"]))
            else:
                announce("{}%".format(live_status["percent"]))
            return

        paths = self._get_device_paths()

        if not paths:
            announce("Base station not found.")
            return

        success = False
        access_denied = False

        for path in paths:
            handle = kernel32.CreateFileW(
                path,
                GENERIC_READ | GENERIC_WRITE,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None,
                OPEN_EXISTING,
                FILE_FLAG_OVERLAPPED,
                None
            )
            if handle == INVALID_HANDLE_VALUE:
                err = kernel32.GetLastError()
                if err in (5, 32):
                    access_denied = True
                continue

            try:
                protect_gc = []

                request = bytearray(64)
                request[0] = 0x00
                request[1] = 0xB0
                b, ov = write_overlapped(handle, request)
                protect_gc.append((b, ov))

                bytes_list, db, dov = read_overlapped(handle)
                protect_gc.append((db, dov))

                if bytes_list and len(bytes_list) >= 16:
                    has_report_id = (bytes_list[0] == 0x00)
                    offset = 1 if has_report_id else 0

                    if len(bytes_list) > offset + 4:
                        offline_status = bytes_list[offset + 1]
                        battery_level = bytes_list[offset + 3]
                        charging_status = bytes_list[offset + 4]

                        if offline_status == 0x02:
                            announce("Offline")
                            self._save_path(path)
                            success = True
                            break
                        elif offline_status == 0x04 or battery_level > 100:
                            pass
                        else:
                            is_charging = (charging_status == 0x01)
                            if is_charging:
                                announce("{}% and charging".format(battery_level))
                            else:
                                announce("{}%".format(battery_level))
                            self._save_path(path)
                            success = True
                            break
            except Exception:
                pass
            finally:
                kernel32.CloseHandle(handle)

        if not success:
            if access_denied:
                self._clear_cached_path()
                announce("Access blocked by another app, likely SteelSeries GG.")
            else:
                self._clear_cached_path()
                announce("No reply received.")

    # ═══════════════════════════════════════════════════════════
    #  Top-level scripts (gesture entry points)
    # ═══════════════════════════════════════════════════════════

    def script_announceBattery(self, gesture):
        if self._check_thread and self._check_thread.is_alive():
            ui.message("Please wait.")
            return

        self._check_thread = threading.Thread(target=self._queryBatteryAsync)
        self._check_thread.start()

    def script_enterEqLayer(self, gesture):
        """Enter the EQ command layer."""
        self._enter_eq_layer()

    def script_applyCustomEq(self, gesture):
        """Apply custom 10-band EQ from config file (direct shortcut, no layer)."""
        if not self._eq_config_path or not os.path.exists(self._eq_config_path):
            announce(
                "No custom EQ config found. "
                "Create {} with a 'custom_eq' array of 10 values (-10 to +10 dB).".format(
                    EQ_CONFIG_FILENAME
                )
            )
            return

        try:
            with open(self._eq_config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            custom = cfg.get("custom_eq")
            if not isinstance(custom, list) or len(custom) != 10:
                announce("Invalid custom_eq in config — must be an array of 10 numbers.")
                return
            self._apply_custom_eq(custom)
        except Exception as e:
            announce("Error reading EQ config: {}".format(e))

    # ═══════════════════════════════════════════════════════════
    #  EQ layer sub-commands (called from getScript routing)
    # ═══════════════════════════════════════════════════════════

    def script_eqLayer_status(self, gesture):
        self._reset_layer_timer()
        self._announce_current_eq()

    def script_eqLayer_flat(self, gesture):
        self._cancel_eq_layer()
        self._apply_eq_preset(0)

    def script_eqLayer_bass(self, gesture):
        self._cancel_eq_layer()
        self._apply_eq_preset(1)

    def script_eqLayer_focus(self, gesture):
        self._cancel_eq_layer()
        self._apply_eq_preset(2)

    def script_eqLayer_smiley(self, gesture):
        self._cancel_eq_layer()
        self._apply_eq_preset(3)

    def script_eqLayer_heavybass(self, gesture):
        self._cancel_eq_layer()
        self._apply_eq_preset(4)

    def script_eqLayer_battery(self, gesture):
        self._cancel_eq_layer()
        if self._check_thread and self._check_thread.is_alive():
            ui.message("Please wait.")
            return
        self._check_thread = threading.Thread(target=self._queryBatteryAsync)
        self._check_thread.start()

    def script_eqLayer_custom(self, gesture):
        """Open the custom EQ dialog with 10 sliders."""
        self._show_custom_eq_dialog()

    def script_eqLayer_sidetone(self, gesture):
        """Cycle sidetone: Off → Low → Medium → High → Max → Off."""
        SIDETONE_LEVELS = [0, 32, 64, 96, 128]
        SIDETONE_NAMES = ["Off", "Low (32)", "Medium (64)", "High (96)", "Max (128)"]
        try:
            idx = SIDETONE_LEVELS.index(self._sidetone_level)
        except ValueError:
            idx = 0
        next_idx = (idx + 1) % len(SIDETONE_LEVELS)
        new_level = SIDETONE_LEVELS[next_idx]
        name = SIDETONE_NAMES[next_idx]

        exe = self._get_headsetcontrol_path()
        if not exe:
            self._reset_layer_timer()
            announce("HeadsetControl not available.")
            return

        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        try:
            subprocess.check_output([exe, "-s", str(new_level)],
                                    text=True, startupinfo=startupinfo,
                                    stderr=subprocess.STDOUT, timeout=5)
            self._sidetone_level = new_level
            self._save_eq_config()
            self._reset_layer_timer()
            announce("Sidetone: {}".format(name))
        except Exception:
            self._reset_layer_timer()
            announce("Failed to set sidetone.")

    def script_eqLayer_cancel(self, gesture):
        self._cancel_eq_layer()
        announce("Cancelled.")

    def script_eqLayer_micvolume(self, gesture):
        """Cycle mic volume: 32 → 64 → 96 → 128 → 32."""
        LEVELS = [32, 64, 96, 128]
        NAMES = ["Low (32)", "Medium (64)", "High (96)", "Max (128)"]
        try:
            idx = LEVELS.index(self._mic_volume)
        except ValueError:
            idx = 0
        next_idx = (idx + 1) % len(LEVELS)
        new_level = LEVELS[next_idx]
        name = NAMES[next_idx]

        exe = self._get_headsetcontrol_path()
        if not exe:
            self._reset_layer_timer()
            announce("HeadsetControl not available.")
            return

        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        try:
            subprocess.check_output([exe, "--microphone-volume", str(new_level)],
                                    text=True, startupinfo=startupinfo,
                                    stderr=subprocess.STDOUT, timeout=5)
            self._mic_volume = new_level
            self._save_eq_config()
            self._reset_layer_timer()
            announce("Mic volume: {}".format(name))
        except Exception:
            self._reset_layer_timer()
            announce("Failed to set mic volume.")

    def script_eqLayer_inactivetime(self, gesture):
        """Cycle inactive time: Never → 10min → 30min → 60min → 90min."""
        TIMES = [0, 10, 30, 60, 90]
        NAMES = ["Never", "10 min", "30 min", "60 min", "90 min"]
        try:
            idx = TIMES.index(self._inactive_time)
        except ValueError:
            idx = 0
        next_idx = (idx + 1) % len(TIMES)
        new_time = TIMES[next_idx]
        name = NAMES[next_idx]

        exe = self._get_headsetcontrol_path()
        if not exe:
            self._reset_layer_timer()
            announce("HeadsetControl not available.")
            return

        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        try:
            subprocess.check_output([exe, "-i", str(new_time)],
                                    text=True, startupinfo=startupinfo,
                                    stderr=subprocess.STDOUT, timeout=5)
            self._inactive_time = new_time
            self._save_eq_config()
            self._reset_layer_timer()
            announce("Auto-off: {}".format(name))
        except Exception:
            self._reset_layer_timer()
            announce("Failed to set inactive time.")

    def script_eqLayer_refresh(self, gesture):
        """Re-entering the layer via NVDA+Shift+Q — just reset the timer."""
        self._reset_layer_timer()

    # ═══════════════════════════════════════════════════════════
    #  Gesture bindings
    # ═══════════════════════════════════════════════════════════

    __gestures = {
        "kb:nvda+shift+a":          "announceBattery",
        "kb:nvda+shift+q":          "enterEqLayer",
        "kb:nvda+shift+control+e":  "applyCustomEq",
    }

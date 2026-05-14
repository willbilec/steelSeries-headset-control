# SteelSeries Battery Add-on for NVDA
# Copyright 2026, Antigravity

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
        if err == 997: # ERROR_IO_PENDING
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
    scriptCategory = "SteelSeries Battery"
    
    def __init__(self):
        super(GlobalPlugin, self).__init__()
        self._check_thread = None
        self._cached_path = None
        self._cache_file = None
        try:
            import globalVars
            self._cache_file = os.path.join(globalVars.appArgs.configPath, "steelSeriesBattery_path.txt")
            if os.path.exists(self._cache_file):
                with open(self._cache_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        self._cached_path = content
        except Exception:
            pass

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
                import os
                if os.path.exists(self._cache_file):
                    os.remove(self._cache_file)
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
                    if line.startswith('HID\\'):
                        path = '\\\\?\\' + line.replace('\\', '#') + '#{4d1e55b2-f16f-11cf-88cb-001111000030}'
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

    def _get_headsetcontrol_path(self):
        plugin_dir = os.path.dirname(__file__)
        bundled_path = os.path.join(plugin_dir, "bin", "headsetcontrol.exe")
        if os.path.exists(bundled_path):
            return bundled_path
        return None

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

    def script_announceBattery(self, gesture):
        if self._check_thread and self._check_thread.is_alive():
            ui.message("Please wait.")
            return

        self._check_thread = threading.Thread(target=self._queryBatteryAsync)
        self._check_thread.start()

    script_announceBattery.__doc__ = "Announces the battery level of your supported SteelSeries headset."
    
    __gestures = {
        "kb:nvda+shift+a": "announceBattery",
    }

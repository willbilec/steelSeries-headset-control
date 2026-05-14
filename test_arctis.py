import ctypes
from ctypes import wintypes
import time

# Windows API Constants
DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8)
    ]

class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.POINTER(ctypes.c_ulong))
    ]

class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.DWORD),
        ("VendorID", wintypes.USHORT),
        ("ProductID", wintypes.USHORT),
        ("VersionNumber", wintypes.USHORT)
    ]

setupapi = ctypes.windll.setupapi
hid = ctypes.windll.hid
kernel32 = ctypes.windll.kernel32

def enumerate_arctis_nova():
    # 1. Get HID GUID
    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))
    
    # 2. Get Device Info Set for HID interfaces
    hDevInfo = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    
    if hDevInfo == wintypes.HANDLE(-1).value:
        return []
        
    devices = []
    interface_data = SP_DEVICE_INTERFACE_DATA()
    interface_data.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
    
    i = 0
    while setupapi.SetupDiEnumDeviceInterfaces(hDevInfo, None, ctypes.byref(guid), i, ctypes.byref(interface_data)):
        i += 1
        req_size = wintypes.DWORD(0)
        
        # Get required size for detail data
        setupapi.SetupDiGetDeviceInterfaceDetailW(hDevInfo, ctypes.byref(interface_data), None, 0, ctypes.byref(req_size), None)
        
        # Allocate detail data
        class SP_DEVICE_INTERFACE_DETAIL_DATA(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("DevicePath", wintypes.WCHAR * (req_size.value - ctypes.sizeof(wintypes.DWORD)))
            ]
        
        detail_data = SP_DEVICE_INTERFACE_DETAIL_DATA()
        
        # For 64-bit windows, cbSize should be 8, for 32-bit it's 5 (or dynamically based on ctypes.sizeof(VOID*))
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            detail_data.cbSize = 8
        else:
            detail_data.cbSize = 5
            
        if setupapi.SetupDiGetDeviceInterfaceDetailW(hDevInfo, ctypes.byref(interface_data), ctypes.byref(detail_data), req_size, None, None):
            path = detail_data.DevicePath
            
            # Open handle to query attributes
            handle = kernel32.CreateFileW(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
            if handle != -1:
                attributes = HIDD_ATTRIBUTES()
                attributes.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
                if hid.HidD_GetAttributes(handle, ctypes.byref(attributes)):
                    if attributes.VendorID == 0x1038 and attributes.ProductID in (0x2232, 0x2253):
                        devices.append(path)
                kernel32.CloseHandle(handle)
                
    setupapi.SetupDiDestroyDeviceInfoList(hDevInfo)
    return devices

print(enumerate_arctis_nova())

import subprocess

def test():
    cmd = ['powershell', '-NoProfile', '-Command', 
           r"Get-WmiObject Win32_PnPEntity | Where-Object { $_.PNPDeviceID -match 'VID_1038&PID_2232' -or $_.PNPDeviceID -match 'VID_1038&PID_2253' } | Select-Object -ExpandProperty PNPDeviceID"]
    
    out = subprocess.check_output(cmd, text=True)
    paths = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith('HID\\'):
            path = '\\\\?\\' + line.replace('\\', '#') + '#{4d1e55b2-f16f-11cf-88cb-001111000030}'
            paths.append(path)
    return paths

print(test())

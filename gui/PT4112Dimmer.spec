# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: single-file, windowless GUI.
#   python -m PyInstaller --noconfirm --clean PT4112Dimmer.spec
# Works on Windows (primary target) and also macOS/Linux for local testing.
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []

for pkg in ("bleak", "serial"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

if sys.platform == "win32":
    # bleak's WinRT backend is split across many winrt-* distributions that
    # live in the `winrt` namespace package; static analysis misses them.
    hiddenimports += [
        "winrt.system",
        "winrt.windows.foundation",
        "winrt.windows.foundation.collections",
        "winrt.windows.storage.streams",
        "winrt.windows.devices.enumeration",
        "winrt.windows.devices.radios",
        "winrt.windows.devices.bluetooth",
        "winrt.windows.devices.bluetooth.advertisement",
        "winrt.windows.devices.bluetooth.genericattributeprofile",
    ]
    try:
        hiddenimports += collect_submodules("winrt")
    except Exception:
        pass

a = Analysis(
    ["dimmer_gui.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "numpy", "PIL", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PT4112Dimmer",
    debug=False,
    strip=False,
    upx=False,
    console=False,          # no console window; set True to see tracebacks
    disable_windowed_traceback=False,
    icon=None,
)

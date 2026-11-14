"""autostart.py - optional "Start with Windows" (per-user Run registry key)."""
from __future__ import annotations

import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "Y2KWidgets"


def supported() -> bool:
    return sys.platform == "win32"


def command() -> str:
    if getattr(sys, "frozen", False):                 # the packaged .exe
        return f'"{sys.executable}"'
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")   # no console window
    if os.path.exists(pyw):
        exe = pyw
    return f'"{exe}" "{os.path.abspath(sys.argv[0])}"'


def is_enabled() -> bool:
    if not supported():
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except OSError:
        return False


def set_enabled(flag: bool) -> bool:
    if not supported():
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            if flag:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False

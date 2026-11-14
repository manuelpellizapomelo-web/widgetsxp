"""hotkey.py - global hotkeys (Ctrl+Alt+<key>) via the Windows RegisterHotKey API.

No keyboard hooks, no polling, no CPU use while idle. On other systems, or if
a key combination is already taken, it simply does nothing for that key.
"""
from __future__ import annotations

import sys

PREFIX = "Ctrl+Alt+"


def install_hotkeys(app, mapping: dict):
    """mapping = {"W": callback, "G": callback}.

    Returns a cleanup function (with a .registered set) or None if nothing
    could be registered."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        from PySide6.QtCore import QAbstractNativeEventFilter, QTimer

        user32 = ctypes.windll.user32
        MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
        WM_HOTKEY = 0x0312
        ids = {}
        for key, cb in mapping.items():
            hid = 0x5900 + ord(key.upper())
            if user32.RegisterHotKey(None, hid, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT,
                                     ord(key.upper())):
                ids[hid] = (key.upper(), cb)
        if not ids:
            return None

        class _Filter(QAbstractNativeEventFilter):
            def nativeEventFilter(self, event_type, message):
                try:
                    if bytes(event_type) == b"windows_generic_MSG":
                        msg = wintypes.MSG.from_address(int(message))
                        if msg.message == WM_HOTKEY and msg.wParam in ids:
                            QTimer.singleShot(0, ids[msg.wParam][1])
                except Exception:
                    pass
                return False, 0

        flt = _Filter()
        app.installNativeEventFilter(flt)

        def cleanup():
            try:
                for hid in ids:
                    user32.UnregisterHotKey(None, hid)
                app.removeNativeEventFilter(flt)
            except Exception:
                pass

        cleanup.filter = flt                      # keep the filter object alive
        cleanup.registered = {k for k, _ in ids.values()}
        return cleanup
    except Exception:
        return None

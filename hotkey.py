"""hotkey.py - global hotkey (Ctrl+Alt+W) to hide/show all widgets.

Uses the standard Windows RegisterHotKey API: no keyboard hooks, no polling,
no CPU use while idle. On other systems (or if the key is taken) it simply
does nothing.
"""
from __future__ import annotations

import sys

HOTKEY_LABEL = "Ctrl+Alt+W"


def install_hotkey(app, callback):
    """Returns a cleanup function if the hotkey was registered, else None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        from PySide6.QtCore import QAbstractNativeEventFilter, QTimer

        user32 = ctypes.windll.user32
        MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
        WM_HOTKEY, HOTKEY_ID = 0x0312, 0x5932
        if not user32.RegisterHotKey(None, HOTKEY_ID,
                                     MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("W")):
            return None

        class _Filter(QAbstractNativeEventFilter):
            def nativeEventFilter(self, event_type, message):
                try:
                    if bytes(event_type) == b"windows_generic_MSG":
                        msg = wintypes.MSG.from_address(int(message))
                        if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                            QTimer.singleShot(0, callback)
                except Exception:
                    pass
                return False, 0

        flt = _Filter()
        app.installNativeEventFilter(flt)

        def cleanup():
            try:
                user32.UnregisterHotKey(None, HOTKEY_ID)
                app.removeNativeEventFilter(flt)
            except Exception:
                pass

        cleanup.filter = flt          # keep the filter object alive
        return cleanup
    except Exception:
        return None

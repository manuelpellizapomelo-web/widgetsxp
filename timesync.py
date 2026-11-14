"""timesync.py - asks an internet time server (NTP) what time it really is.

Only the offset between the PC clock and the real time is stored; it never
changes the Windows clock. The request runs in a background thread so the
widgets never freeze, and if there is no internet the PC clock is used.
"""
from __future__ import annotations

import socket
import struct
import threading
import time

from PySide6.QtCore import QObject, Signal

SERVERS = ("time.cloudflare.com", "time.google.com", "pool.ntp.org", "time.windows.com")
NTP_TO_UNIX = 2208988800          # seconds between 1900-01-01 and 1970-01-01


def query_offset(timeout: float = 3.0) -> float | None:
    """Seconds to ADD to the PC clock to get the real time, or None if offline."""
    packet = b"\x1b" + 47 * b"\0"
    for host in SERVERS:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(timeout)
                t0 = time.time()
                s.sendto(packet, (host, 123))
                data, _ = s.recvfrom(512)
                t3 = time.time()
            if len(data) < 48:
                continue
            secs, frac = struct.unpack("!II", data[40:48])
            if secs == 0:
                continue
            server_time = secs - NTP_TO_UNIX + frac / 2 ** 32
            # assume the reply took half of the round trip to get back
            return server_time + (t3 - t0) / 2 - t3
        except (OSError, struct.error):
            continue
    return None


class TimeSync(QObject):
    """Runs query_offset() off the UI thread and reports back on the UI thread."""
    _raw = Signal(object)

    def __init__(self, callback, parent=None) -> None:
        super().__init__(parent)
        self._callback = callback
        self._busy = False
        self._raw.connect(self._done)

    def start(self) -> None:
        if self._busy:
            return
        self._busy = True
        threading.Thread(target=lambda: self._raw.emit(query_offset()),
                         daemon=True).start()

    def _done(self, offset) -> None:
        self._busy = False
        self._callback(offset)

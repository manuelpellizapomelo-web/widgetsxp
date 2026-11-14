"""widgets.py - Windows XP / Silver style desktop widgets (PySide6).

Performance design (so it can sit next to a game):
  * Normal opaque windows with a shape mask (no per-pixel transparency).
  * The window frame and everything static is painted ONCE into a cached
    pixmap; each tick only the moving parts are drawn on top.
  * No timers inside widgets: the Manager runs ONE shared 1-second ticker,
    which is stopped completely while the widgets are hidden.
"""
from __future__ import annotations

import ast
import calendar
import datetime as dt
import math
import operator
import os
import re
import socket
import threading
import time
from collections import deque

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (QColor, QFont, QKeySequence, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
                           QRadialGradient, QRegion, QShortcut)
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QFileDialog, QMessageBox, QSlider, QPushButton, QSizeGrip, QTextEdit,
                               QVBoxLayout, QWidget)

TB = 26          # title bar height
FB = 3           # frame thickness (left / right / bottom)
RADIUS = 8       # rounded top corners
BIG = 16777215   # Qt "no maximum size"

UI_FONTS = ["Tahoma", "Verdana", "DejaVu Sans", "Sans Serif"]
TITLE_FONTS = ["Trebuchet MS", "Tahoma", "Verdana", "DejaVu Sans"]
MONO_FONTS = ["Lucida Console", "Consolas", "DejaVu Sans Mono", "Monospace"]


def qc(hex_color: str, alpha: int = 255) -> QColor:
    c = QColor(hex_color)
    c.setAlpha(alpha)
    return c


def font(pt: float, bold: bool = False, kind: str = "ui") -> QFont:
    f = QFont()
    f.setFamilies({"ui": UI_FONTS, "title": TITLE_FONTS, "mono": MONO_FONTS}[kind])
    f.setPointSizeF(pt)
    f.setBold(bold)
    return f


def on_color(bg_hex: str) -> QColor:
    """Black or white, whichever is readable on top of bg_hex."""
    c = QColor(bg_hex)
    lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return QColor("black") if lum > 150 else QColor("white")


def draw_text(p, rect, text, fnt, color, flags=Qt.AlignCenter, shadow=None):
    p.setFont(fnt)
    if shadow is not None:
        p.setPen(shadow)
        p.drawText(rect.translated(1, 1), flags, text)
    p.setPen(color)
    p.drawText(rect, flags, text)


def sunken_box(p, rect, t):
    p.setPen(QPen(qc(t.field_border), 1))
    p.setBrush(QColor(t.field))
    p.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))


def draw_blocks(p, rect, pct, t):
    """XP-style segmented progress bar."""
    p.setPen(QPen(qc(t.field_border), 1))
    p.setBrush(QColor(t.field))
    p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 2, 2)
    inner = rect.adjusted(3, 3, -3, -3)
    bw, gap = 7.0, 2.0
    n = int((inner.width() + gap) // (bw + gap))
    filled = max(0, min(n, round(n * pct / 100.0)))
    p.setPen(Qt.NoPen)
    p.setBrush(qc(t.bar))
    for i in range(filled):
        p.drawRoundedRect(QRectF(inner.left() + i * (bw + gap), inner.top(),
                                 bw, inner.height()), 1, 1)


def button_qss(t) -> str:
    return (
        "QPushButton { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
        f"stop:0 {t.button_top}, stop:1 {t.button_bottom}); "
        f"border: 1px solid {t.button_border}; border-radius: 3px; "
        f"padding: 3px 6px; color: {t.text}; }}"
        "QPushButton:hover { border: 1px solid #e8a030; }"
        f"QPushButton:pressed {{ background: {t.button_bottom}; }}")


def parse_duration(text: str):
    """'25' = 25 min, '1:30' = 1 min 30 s, '1:00:00' = 1 h.  Returns seconds or None."""
    text = text.strip()
    if not re.fullmatch(r"\d{1,3}(:\d{1,2}){0,2}", text):
        return None
    parts = [int(x) for x in text.split(":")]
    if len(parts) == 1:
        secs = parts[0] * 60
    elif len(parts) == 2:
        if parts[1] > 59:
            return None
        secs = parts[0] * 60 + parts[1]
    else:
        if parts[1] > 59 or parts[2] > 59:
            return None
        secs = parts[0] * 3600 + parts[1] * 60 + parts[2]
    return secs if 1 <= secs <= 359999 else None


def fmt_rate(bps: float) -> str:
    if bps >= 1024 * 1024:
        return f"{bps / (1024 * 1024):.1f} MB/s"
    return f"{bps / 1024:.0f} KB/s"


class XPWindow(QWidget):
    """Frameless XP-style window. Base class for every widget."""
    kind = "xp"
    title = "Widget"
    SIZE = QSize(240, 280)
    MIN = QSize(120, TB + 20)
    resizable = False
    tick_every = 0          # seconds between on_tick() calls (0 = never)

    def __init__(self, manager):
        super().__init__(None)
        self.manager = manager
        self.collapsed = False
        self._cache = None
        self._cache_key = None
        self._drag = None
        self.setWindowFlags(manager.window_flags())
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)  # never steal focus
        self.setWindowOpacity(manager.opacity)
        if self.resizable:
            self.setMinimumSize(self.MIN)
            self.resize(self.SIZE)
        else:
            self.setFixedSize(self.SIZE)
        self._expanded = QSize(self.size())
        self._apply_mask()

    # ------------------------------------------------------------ geometry
    def body_rect(self) -> QRectF:
        return QRectF(FB, TB, self.width() - 2 * FB, self.height() - TB - FB)

    def close_rect(self) -> QRectF:
        return QRectF(self.width() - 27, 3, 22, 20)

    def collapse_rect(self) -> QRectF:
        return QRectF(self.width() - 51, 3, 22, 20)

    def _apply_mask(self) -> None:
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, self.width(), self.height() + RADIUS * 2),
                            RADIUS, RADIUS)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._apply_mask()
        self._cache = None

    # ------------------------------------------------------------ collapse
    def _children(self):
        return [c for c in self.children() if isinstance(c, QWidget)]

    def set_collapsed(self, flag: bool) -> None:
        flag = bool(flag)
        if flag == self.collapsed:
            return
        if flag:
            self._expanded = QSize(self.size())
            self.collapsed = True
            for c in self._children():
                c.hide()
            self.setMinimumSize(self.width(), TB)
            self.setMaximumSize(self.width(), TB)
        else:
            self.collapsed = False
            if self.resizable:
                self.setMinimumSize(self.MIN)
                self.setMaximumSize(BIG, BIG)
                self.resize(self._expanded)
            else:
                self.setFixedSize(self._expanded)
            for c in self._children():
                c.show()
        self._cache = None
        self.update()
        self.manager.save()

    # ------------------------------------------------------------ state
    def to_state(self) -> dict:
        size = self._expanded if self.collapsed else self.size()
        return {"type": self.kind, "x": self.x(), "y": self.y(),
                "w": size.width(), "h": size.height(),
                "collapsed": self.collapsed}

    def restore(self, s: dict) -> None:
        w, h = s.get("w"), s.get("h")
        if self.resizable and isinstance(w, int) and isinstance(h, int):
            self.resize(max(self.MIN.width(), min(w, 1400)),
                        max(self.MIN.height(), min(h, 1400)))
        if s.get("collapsed"):
            self.set_collapsed(True)

    def apply_theme(self) -> None:
        self._cache = None
        self.update()

    def refresh(self) -> None:
        self.update()

    def on_tick(self, n: int) -> None:
        pass

    def on_close(self) -> None:
        """Called when the user closes this widget (stop audio, etc.)."""

    def keep_alive(self) -> bool:
        """True while the widget must keep ticking even when everything is hidden
        (a running timer has to be able to ring)."""
        return False

    def extra_menu(self, menu) -> None:
        """Widgets can add their own entries to the right-click menu."""

    # ------------------------------------------------------------ painting
    def _title_button(self, p, r, top, bottom, glyph) -> None:
        t = self.manager.theme
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, qc(top))
        g.setColorAt(1, qc(bottom))
        p.setPen(QPen(QColor(255, 255, 255, 235) if glyph == "x"
                      else qc(t.title_text, 150), 1.2))
        p.setBrush(g)
        p.drawRoundedRect(r, 3.5, 3.5)
        cx, cy = r.center().x(), r.center().y()
        if glyph == "x":
            p.setPen(QPen(QColor("white"), 2.2, Qt.SolidLine, Qt.RoundCap))
            d = 4.5
            p.drawLine(QPointF(cx - d, cy - d), QPointF(cx + d, cy + d))
            p.drawLine(QPointF(cx - d, cy + d), QPointF(cx + d, cy - d))
        else:
            p.setPen(QPen(qc(t.title_text), 2.4, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(cx - 4.5, r.bottom() - 6), QPointF(cx + 4.5, r.bottom() - 6))

    def paint_frame(self, p: QPainter) -> None:
        t = self.manager.theme
        w, h = self.width(), self.height()
        p.fillRect(QRectF(0, 0, w, h), QColor(t.frame))
        g = QLinearGradient(0, 0, 0, TB)
        g.setColorAt(0.0, qc(t.title_top))
        g.setColorAt(0.22, qc(t.title_mid))
        g.setColorAt(1.0, qc(t.title_bottom))
        p.fillRect(QRectF(0, 0, w, TB), g)
        p.setPen(QColor(255, 255, 255, 110))
        p.drawLine(QPointF(RADIUS, 1.5), QPointF(w - RADIUS, 1.5))
        if not self.collapsed:
            p.fillRect(self.body_rect(), QColor(t.body))
        rect = QRectF(9, 0, max(10, w - 9 - 56), TB)
        draw_text(p, rect, self.title, font(9.5, True, "title"), qc(t.title_text),
                  Qt.AlignVCenter | Qt.AlignLeft, qc(t.title_shadow, 200))
        self._title_button(p, self.collapse_rect(), t.title_top, t.title_bottom, "-")
        self._title_button(p, self.close_rect(), t.close_top, t.close_bottom, "x")

    def paint_static(self, p: QPainter, b: QRectF) -> None:
        """Cached drawing (never changes between ticks)."""

    def paint_dynamic(self, p: QPainter, b: QRectF) -> None:
        """Drawn on top of the cache at every repaint."""

    def paint_overlay(self, p: QPainter) -> None:
        """Drawn on top, even when collapsed."""

    def _build_cache(self, dpr: float, key) -> None:
        pm = QPixmap(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
        pm.setDevicePixelRatio(dpr)
        p = QPainter(pm)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.paint_frame(p)
        if not self.collapsed:
            self.paint_static(p, self.body_rect())
        p.end()
        self._cache, self._cache_key = pm, key

    def paintEvent(self, _e) -> None:
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr, self.manager.theme.name, self.collapsed)
        if self._cache is None or self._cache_key != key:
            self._build_cache(dpr, key)
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        p.drawPixmap(0, 0, self._cache)
        self.paint_overlay(p)
        if not self.collapsed:
            self.paint_dynamic(p, self.body_rect())

    # ------------------------------------------------------- interaction
    def contextMenuEvent(self, e) -> None:
        self.manager.make_menu(self).exec(e.globalPos())

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            pos = e.position()
            if self.close_rect().contains(pos):
                self.manager.remove(self)
                return
            if self.collapse_rect().contains(pos):
                self.set_collapsed(not self.collapsed)
                return
            if not self.manager.locked:
                self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseDoubleClickEvent(self, e) -> None:
        pos = e.position()
        if (e.button() == Qt.LeftButton and pos.y() < TB
                and not self.close_rect().contains(pos)
                and not self.collapse_rect().contains(pos)):
            self.set_collapsed(not self.collapsed)

    def mouseMoveEvent(self, e) -> None:
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(self.manager.snap_pos(
                self, e.globalPosition().toPoint() - self._drag))

    def mouseReleaseEvent(self, e) -> None:
        if self._drag is not None:
            self._drag = None
            self.manager.save()


# ======================================================================
class ClockWidget(XPWindow):
    kind = "clock"
    title = "Clock"
    SIZE = QSize(230, 284)
    tick_every = 1
    R = 84.0

    def _center(self) -> QPointF:
        return QPointF(self.width() / 2, TB + 14 + self.R)

    def paint_static(self, p, b) -> None:
        t = self.manager.theme
        c, R = self._center(), self.R
        bez = QLinearGradient(c.x(), c.y() - R, c.x(), c.y() + R)
        bez.setColorAt(0.0, QColor(t.frame).lighter(200))
        bez.setColorAt(1.0, qc(t.frame))
        p.setPen(QPen(qc(t.frame), 1.5))
        p.setBrush(bez)
        p.drawEllipse(c, R, R)
        face = QRadialGradient(QPointF(c.x(), c.y() - R * 0.3), R)
        face.setColorAt(0.0, qc(t.face))
        face.setColorAt(1.0, QColor(t.body))
        p.setPen(QPen(qc(t.field_border), 1))
        p.setBrush(face)
        fr = R - 7
        p.drawEllipse(c, fr, fr)
        for i in range(60):
            major = i % 5 == 0
            p.save()
            p.translate(c)
            p.rotate(i * 6)
            p.setPen(QPen(qc(t.text, 230 if major else 120), 2.2 if major else 1))
            p.drawLine(QPointF(0, -fr + 4), QPointF(0, -fr + (13 if major else 8)))
            p.restore()
        for n, (dx, dy) in {12: (0, -1), 3: (1, 0), 6: (0, 1), 9: (-1, 0)}.items():
            cx, cy = c.x() + dx * (fr - 25), c.y() + dy * (fr - 25)
            draw_text(p, QRectF(cx - 12, cy - 9, 24, 18), str(n),
                      font(10, True), qc(t.accent))

    def paint_dynamic(self, p, b) -> None:
        t = self.manager.theme
        c, R = self._center(), self.R
        fr = R - 7
        now = dt.datetime.now()

        def hand(angle, length, width, color):
            p.save()
            p.translate(c)
            p.rotate(angle)
            poly = QPolygonF([QPointF(-width, 0), QPointF(0, -length),
                              QPointF(width, 0), QPointF(0, length * 0.16)])
            p.setBrush(color)
            p.setPen(QPen(QColor(255, 255, 255, 200), 1))
            p.drawPolygon(poly)
            p.restore()

        hand(((now.hour % 12) + now.minute / 60.0) * 30, fr * 0.5, 4.0, qc(t.accent))
        hand((now.minute + now.second / 60.0) * 6, fr * 0.76, 3.0, qc(t.accent))
        p.save()
        p.translate(c)
        p.rotate(now.second * 6)
        p.setPen(QPen(qc(t.second), 1.5))
        p.drawLine(QPointF(0, 16), QPointF(0, -fr * 0.85))
        p.restore()
        p.setPen(QPen(QColor(255, 255, 255, 220), 1))
        p.setBrush(qc(t.accent))
        p.drawEllipse(c, 4.5, 4.5)

        box = QRectF(b.left() + 14, c.y() + R + 10, b.width() - 28, 30)
        sunken_box(p, box, t)
        if self.manager.clock24:
            txt = now.strftime("%H:%M:%S")
        else:
            txt = now.strftime("%I:%M:%S %p").lstrip("0")
        draw_text(p, box, txt, font(14, True, "mono"), qc(t.text))
        draw_text(p, QRectF(b.left(), box.bottom() + 4, b.width(), 18),
                  now.strftime("%a, %d %b %Y"), font(9), qc(t.text))

    def on_tick(self, n: int) -> None:
        self.update()


# ======================================================================
class CalendarWidget(XPWindow):
    kind = "calendar"
    title = "Calendar"
    SIZE = QSize(236, 262)
    tick_every = 30         # only needed to notice that midnight has passed

    def __init__(self, manager):
        super().__init__(manager)
        self._today = dt.date.today()
        self.view = (self._today.year, self._today.month)

    def _header_rects(self):
        b = self.body_rect()
        y = b.top() + 6
        return (QRectF(b.left() + 4, y, 26, 22),          # previous
                QRectF(b.right() - 30, y, 26, 22),         # next
                QRectF(b.left() + 34, y, b.width() - 68, 22))  # title

    def _shift(self, delta: int) -> None:
        y, m = self.view
        m += delta
        if m < 1:
            y, m = y - 1, 12
        elif m > 12:
            y, m = y + 1, 1
        self.view = (max(1, min(y, 9998)), m)
        self.update()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and e.position().y() > TB:
            prev_r, next_r, title_r = self._header_rects()
            pos = e.position()
            if prev_r.contains(pos):
                self._shift(-1)
                return
            if next_r.contains(pos):
                self._shift(1)
                return
            if title_r.contains(pos):
                self.view = (self._today.year, self._today.month)
                self.update()
                return
        super().mousePressEvent(e)

    def paint_dynamic(self, p, b) -> None:
        t = self.manager.theme
        year, month = self.view
        prev_r, next_r, title_r = self._header_rects()
        p.setPen(Qt.NoPen)
        p.setBrush(qc(t.accent))
        for r, sign in ((prev_r, -1), (next_r, 1)):
            cx, cy = r.center().x(), r.center().y()
            p.drawPolygon(QPolygonF([QPointF(cx - sign * 4, cy - 6),
                                     QPointF(cx - sign * 4, cy + 6),
                                     QPointF(cx + sign * 4, cy)]))
        draw_text(p, title_r, f"{calendar.month_name[month]} {year}",
                  font(10.5, True), qc(t.text))

        first = 0 if self.manager.week_monday else 6
        names = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
        col_w = (b.width() - 16) / 7.0
        x0, y0 = b.left() + 8, title_r.bottom() + 6
        p.setPen(QPen(qc(t.field_border), 1))
        p.drawLine(QPointF(x0, y0 + 19), QPointF(x0 + 7 * col_w, y0 + 19))
        for i in range(7):
            draw_text(p, QRectF(x0 + i * col_w, y0, col_w, 18),
                      names[(first + i) % 7], font(8.5, True), qc(t.accent))
        weeks = calendar.Calendar(first).monthdayscalendar(year, month)
        today = self._today
        for r, week in enumerate(weeks):
            for c, day in enumerate(week):
                if day == 0:
                    continue
                cell = QRectF(x0 + c * col_w, y0 + 24 + r * 24, col_w, 22)
                is_today = (year, month, day) == (today.year, today.month, today.day)
                if is_today:
                    p.setPen(Qt.NoPen)
                    p.setBrush(qc(t.accent))
                    p.drawRoundedRect(cell.adjusted(2, 1, -2, -1), 3, 3)
                    draw_text(p, cell, str(day), font(9.5, True), on_color(t.accent))
                else:
                    sunday = calendar.weekday(year, month, day) == 6
                    draw_text(p, cell, str(day), font(9.5),
                              qc(t.second if sunday else t.text))

    def on_tick(self, n: int) -> None:
        today = dt.date.today()
        if today != self._today:
            self._today = today
            self.view = (today.year, today.month)
            self.update()


# ======================================================================
class MonitorWidget(XPWindow):
    kind = "monitor"
    title = "System Monitor"
    SIZE = QSize(250, 254)
    tick_every = 2

    def __init__(self, manager):
        super().__init__(manager)
        self.cpu = 0.0
        self.mem_pct = 0.0
        self.mem_used = 0
        self.mem_total = 0
        self.disk_pct = 0.0
        self.rx = 0.0
        self.tx = 0.0
        self.hist = deque([0.0] * 60, maxlen=60)
        self._last_net = None
        self._boot = None
        self._psutil = None
        try:
            import psutil
            self._psutil = psutil
            psutil.cpu_percent(None)        # prime (first call always returns 0)
            self._boot = psutil.boot_time()
            self._sample_disk()
        except Exception:
            pass

    def _sample_disk(self) -> None:
        try:
            self.disk_pct = float(self._psutil.disk_usage(os.path.abspath(os.sep)).percent)
        except Exception:
            pass

    def sample(self, n: int = 0) -> None:
        ps = self._psutil
        if ps is None:
            return
        self.cpu = float(ps.cpu_percent(None))
        self.hist.append(self.cpu)
        vm = ps.virtual_memory()
        self.mem_pct = float(vm.percent)
        self.mem_total = vm.total
        self.mem_used = vm.total - vm.available
        if n % 16 == 0:
            self._sample_disk()          # the disk changes slowly: every ~16 s
        try:
            io = ps.net_io_counters()
            now = time.monotonic()
            if io is not None:
                if self._last_net is not None:
                    dt_s = max(0.001, now - self._last_net[0])
                    self.rx = max(0.0, (io.bytes_recv - self._last_net[1]) / dt_s)
                    self.tx = max(0.0, (io.bytes_sent - self._last_net[2]) / dt_s)
                self._last_net = (now, io.bytes_recv, io.bytes_sent)
        except Exception:
            pass

    def on_tick(self, n: int) -> None:
        self.sample(n)
        self.update()

    def refresh(self) -> None:
        self._last_net = None        # avoid a huge fake spike after being hidden
        self.sample(0)
        self.update()

    def paint_dynamic(self, p, b) -> None:
        t = self.manager.theme
        if self._psutil is None:
            draw_text(p, b, "psutil is not installed", font(9), qc(t.text))
            return
        left, width = b.left() + 10, b.width() - 20
        y = b.top() + 10
        for label, pct in (("CPU", self.cpu), ("RAM", self.mem_pct),
                           ("DISK", self.disk_pct)):
            draw_text(p, QRectF(left, y, 40, 18), label, font(8.5, True), qc(t.text),
                      Qt.AlignVCenter | Qt.AlignLeft)
            draw_blocks(p, QRectF(left + 42, y, width - 42 - 40, 18), pct, t)
            draw_text(p, QRectF(left + width - 38, y, 38, 18), f"{pct:.0f}%", font(9),
                      qc(t.text), Qt.AlignVCenter | Qt.AlignRight)
            y += 25
        gb = 1024 ** 3
        draw_text(p, QRectF(left, y - 3, width, 15),
                  f"RAM {self.mem_used / gb:.1f} / {self.mem_total / gb:.1f} GB",
                  font(8.5), qc(t.text, 190))
        y += 18

        box = QRectF(left, y, width, 54)
        sunken_box(p, box, t)
        inner = box.adjusted(2, 2, -2, -2)
        p.setPen(QPen(qc(t.field_border, 90), 1))
        for frac in (0.25, 0.5, 0.75):
            yy = inner.bottom() - inner.height() * frac
            p.drawLine(QPointF(inner.left(), yy), QPointF(inner.right(), yy))
        pts = [QPointF(inner.left() + i * inner.width() / (len(self.hist) - 1),
                       inner.bottom() - inner.height() * min(v, 100.0) / 100.0)
               for i, v in enumerate(self.hist)]
        area = QPolygonF([QPointF(inner.left(), inner.bottom())] + pts +
                         [QPointF(inner.right(), inner.bottom())])
        p.setPen(Qt.NoPen)
        p.setBrush(qc(t.accent, 60))
        p.drawPolygon(area)
        p.setPen(QPen(qc(t.accent), 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPolygonF(pts))
        y += 58
        draw_text(p, QRectF(left, y, width, 16),
                  f"Down {fmt_rate(self.rx)}    Up {fmt_rate(self.tx)}",
                  font(9), qc(t.text))
        if self._boot:
            up = int(time.time() - self._boot)
            d, rem = divmod(up, 86400)
            h, rem = divmod(rem, 3600)
            txt = (f"{d}d " if d else "") + f"{h}h {rem // 60}m"
            draw_text(p, QRectF(left, y + 16, width, 15), f"Uptime {txt}",
                      font(8.5), qc(t.text, 190))


# ======================================================================
class TimerWidget(XPWindow):
    kind = "timer"
    title = "Timer"
    SIZE = QSize(240, 178)
    tick_every = 1
    DISPLAY_H = 56

    def __init__(self, manager):
        super().__init__(manager)
        self.mode = "countdown"
        self.target = 300
        self.running = False
        self._t0 = 0.0
        self._before = 0.0
        self._flash = 0
        self._beeps = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(FB + 8, TB + 10 + self.DISPLAY_H + 8, FB + 8, FB + 8)
        lay.setSpacing(6)
        row1 = QHBoxLayout()
        for m in (1, 5, 10, 15):
            btn = QPushButton(f"{m} min")
            btn.clicked.connect(lambda _c=False, m=m: self.set_target(m * 60))
            row1.addWidget(btn)
        row2 = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.reset_btn = QPushButton("Reset")
        self.mode_btn = QPushButton("Stopwatch")
        self.start_btn.clicked.connect(self.toggle)
        self.reset_btn.clicked.connect(self.reset)
        self.mode_btn.clicked.connect(self.toggle_mode)
        for btn in (self.start_btn, self.reset_btn, self.mode_btn):
            row2.addWidget(btn)
        lay.addLayout(row1)
        lay.addLayout(row2)
        lay.addStretch(1)

    # ---- logic (uses monotonic time, so it never drifts)
    def elapsed(self) -> float:
        return self._before + ((time.monotonic() - self._t0) if self.running else 0.0)

    def value(self) -> float:
        if self.mode == "countdown":
            return max(0.0, self.target - self.elapsed())
        return self.elapsed()

    def set_target(self, seconds: int) -> None:
        self.mode = "countdown"
        self.mode_btn.setText("Stopwatch")
        self.target = int(seconds)
        self.reset()
        self.manager.save()

    def toggle(self) -> None:
        if self.running:
            self._before = self.elapsed()
            self.running = False
            self.start_btn.setText("Start")
        else:
            if self.mode == "countdown" and self.value() <= 0:
                self._before = 0.0
            self._t0 = time.monotonic()
            self.running = True
            self._flash = 0
            self.start_btn.setText("Pause")
        self.update()

    def reset(self) -> None:
        self.running = False
        self._before = 0.0
        self._flash = 0
        self._beeps = 0
        self.start_btn.setText("Start")
        self.update()

    def keep_alive(self) -> bool:
        return (self.running and self.mode == "countdown") or self._beeps > 0

    def extra_menu(self, menu) -> None:
        act = menu.addAction("Set time\u2026")
        act.triggered.connect(self._ask_time)

    def _ask_time(self) -> None:
        text, ok = QInputDialog.getText(
            self, "Timer", "Duration: minutes (25), min:sec (1:30) or h:min:sec (1:00:00)")
        if not ok:
            return
        secs = parse_duration(text)
        if secs is None:
            QMessageBox.warning(self, "Timer", "That duration is not valid.")
        else:
            self.set_target(secs)

    def toggle_mode(self) -> None:
        self.mode = "stopwatch" if self.mode == "countdown" else "countdown"
        self.mode_btn.setText("Countdown" if self.mode == "stopwatch" else "Stopwatch")
        self.reset()
        self.manager.save()

    def on_tick(self, n: int) -> None:
        finished = False
        if self.running and self.mode == "countdown" and self.elapsed() >= self.target:
            self.running = False
            self._before = float(self.target)
            self.start_btn.setText("Start")
            self._flash = 8
            self._beeps = 4
            finished = True
            self.manager.notify("Timer", "Time is up!")
        if self._beeps > 0:
            self._beeps -= 1
            QApplication.beep()
        if self._flash > 0 and not finished:
            self._flash -= 1
        if self.running or self._flash > 0 or finished:
            self.update()

    # ---- look
    def apply_theme(self) -> None:
        qss = button_qss(self.manager.theme)
        for btn in self.findChildren(QPushButton):
            btn.setStyleSheet(qss)
            btn.setFont(font(8.5))
        super().apply_theme()

    def paint_overlay(self, p) -> None:
        if self._flash % 2 == 1:
            p.fillRect(QRectF(0, 0, self.width(), TB), QColor(255, 60, 40, 150))

    def paint_dynamic(self, p, b) -> None:
        t = self.manager.theme
        box = QRectF(b.left() + 8, TB + 10, b.width() - 16, self.DISPLAY_H)
        sunken_box(p, box, t)
        secs = int(self.value() + (0.999 if self.mode == "countdown" else 0))
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        txt = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
        draw_text(p, box.adjusted(0, 6, 0, 0), txt, font(24, True, "mono"), qc(t.text))
        draw_text(p, QRectF(box.left() + 6, box.top() + 2, 120, 14),
                  "COUNTDOWN" if self.mode == "countdown" else "STOPWATCH",
                  font(7, True), qc(t.accent), Qt.AlignVCenter | Qt.AlignLeft)

    def to_state(self) -> dict:
        s = super().to_state()
        s.update({"mode": self.mode, "target": self.target})
        return s

    def restore(self, s: dict) -> None:
        if s.get("mode") == "stopwatch":
            self.mode = "stopwatch"
            self.mode_btn.setText("Countdown")
        t = s.get("target")
        if isinstance(t, int) and 1 <= t <= 359999:
            self.target = t
        super().restore(s)


# ======================================================================
class NoteWidget(XPWindow):
    kind = "note"
    title = "Notes"
    SIZE = QSize(240, 200)
    MIN = QSize(170, 100)
    resizable = True

    def __init__(self, manager):
        super().__init__(manager)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(FB + 6, TB + 6, FB + 6, FB + 6)
        self.edit = QTextEdit()
        self.edit.setFont(font(10, False, "mono"))
        self.edit.setPlaceholderText("Type something\u2026")
        lay.addWidget(self.edit)
        self.grip = QSizeGrip(self)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self.manager.save)
        self.edit.textChanged.connect(self._save_timer.start)

    def apply_theme(self) -> None:
        t = self.manager.theme
        self.edit.setStyleSheet(
            f"QTextEdit {{ background: {t.field}; border: 1px solid {t.field_border}; "
            f"color: {t.text}; selection-background-color: {t.accent}; "
            "selection-color: white; }")
        super().apply_theme()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.grip.move(self.width() - FB - self.grip.width() - 2,
                       self.height() - FB - self.grip.height() - 2)
        self.grip.raise_()

    def to_state(self) -> dict:
        s = super().to_state()
        s["text"] = self.edit.toPlainText()
        return s

    def restore(self, s: dict) -> None:
        text = s.get("text")
        if isinstance(text, str):
            self.edit.setPlainText(text)
        super().restore(s)


# ======================================================================
class LatencyWidget(XPWindow):
    """TCP connect latency to a host YOU choose (no host = no network use)."""
    kind = "latency"
    title = "Latency"
    SIZE = QSize(250, 206)
    tick_every = 2
    HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")

    def __init__(self, manager):
        super().__init__(manager)
        self.host = ""
        self.port = 443
        self.hist = deque(maxlen=60)       # ms, or None for a timeout
        self._gen = 0
        self._pending = None               # (generation, ms or -1)
        self._busy = False

    @staticmethod
    def parse_target(text: str):
        host, _, port = text.strip().partition(":")
        if not host or not LatencyWidget.HOST_RE.match(host):
            return None
        if port:
            if not port.isdigit() or not 1 <= int(port) <= 65535:
                return None
            return host, int(port)
        return host, 443

    def set_target(self, text: str) -> bool:
        parsed = self.parse_target(text)
        if parsed is None:
            return False
        self.host, self.port = parsed
        self._gen += 1
        self.hist.clear()
        self.update()
        self.manager.save()
        return True

    def _probe(self, gen: int, host: str, port: int) -> None:
        t0 = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=1.5):
                pass
            self._pending = (gen, (time.perf_counter() - t0) * 1000.0)
        except OSError:
            self._pending = (gen, -1.0)
        finally:
            self._busy = False

    def on_tick(self, n: int) -> None:
        res, self._pending = self._pending, None
        if res is not None and res[0] == self._gen:
            self.hist.append(None if res[1] < 0 else res[1])
        if self.host and not self._busy:
            self._busy = True
            threading.Thread(target=self._probe, daemon=True,
                             args=(self._gen, self.host, self.port)).start()
        self.update()

    def stats(self):
        vals = [v for v in self.hist if v is not None]
        last = self.hist[-1] if self.hist else None
        avg = sum(vals) / len(vals) if vals else None
        jit = (sum(abs(a - b) for a, b in zip(vals, vals[1:])) / (len(vals) - 1)
               if len(vals) > 1 else 0.0)
        loss = (100.0 * sum(1 for v in self.hist if v is None) / len(self.hist)
                if self.hist else 0.0)
        return last, avg, jit, loss

    def to_state(self) -> dict:
        s = super().to_state()
        s.update({"host": self.host, "port": self.port})
        return s

    def restore(self, s: dict) -> None:
        host, port = s.get("host"), s.get("port")
        if isinstance(host, str) and isinstance(port, int):
            parsed = self.parse_target(f"{host}:{port}")
            if parsed is not None:
                self.host, self.port = parsed
        super().restore(s)

    def extra_menu(self, menu) -> None:
        act = menu.addAction("Set host\u2026")
        act.triggered.connect(self._ask_host)

    def _ask_host(self) -> None:
        cur = f"{self.host}:{self.port}" if self.host else ""
        text, ok = QInputDialog.getText(
            self, "Latency", "Host or IP, optionally with :port\n(e.g. 1.1.1.1:443)",
            text=cur)
        if ok and text.strip() and not self.set_target(text):
            QMessageBox.warning(self, "Latency", "That host is not valid.")

    def paint_dynamic(self, p, b) -> None:
        t = self.manager.theme
        if not self.host:
            draw_text(p, b.adjusted(10, 0, -10, 0),
                      "Right-click \u203a Set host\u2026\n\nOnly connects to the host\nyou choose.",
                      font(9), qc(t.text))
            return
        last, avg, jit, loss = self.stats()
        if last is None:
            big, color = ("..." if not self.hist else "timeout"), qc(t.second)
        else:
            big = f"{last:.0f} ms"
            color = qc(t.bar if last < 60 else "#e8a030" if last < 120 else t.second)
            color = color.darker(130) if last < 60 else color
        draw_text(p, QRectF(b.left(), b.top() + 6, b.width(), 34), big,
                  font(20, True, "mono"), color)
        draw_text(p, QRectF(b.left(), b.top() + 38, b.width(), 15),
                  f"{self.host}:{self.port}", font(8.5), qc(t.text, 190))
        avg_t = f"{avg:.0f}" if avg is not None else "-"
        draw_text(p, QRectF(b.left(), b.top() + 54, b.width(), 16),
                  f"avg {avg_t} ms   jitter {jit:.0f}   loss {loss:.0f}%",
                  font(8.5, True), qc(t.text))
        box = QRectF(b.left() + 10, b.top() + 76, b.width() - 20, 62)
        sunken_box(p, box, t)
        inner = box.adjusted(2, 2, -2, -2)
        vals = [v for v in self.hist if v is not None]
        top = max(100.0, (max(vals) * 1.2) if vals else 100.0)
        n = max(2, self.hist.maxlen)
        step = inner.width() / (n - 1)
        x0 = inner.right() - step * (len(self.hist) - 1)
        p.setPen(QPen(qc(t.accent), 1.4))
        prev = None
        for i, v in enumerate(self.hist):
            x = x0 + i * step
            if v is None:
                p.setPen(QPen(qc(t.second), 1.2))
                p.drawLine(QPointF(x, inner.top() + 2), QPointF(x, inner.bottom() - 2))
                p.setPen(QPen(qc(t.accent), 1.4))
                prev = None
                continue
            pt = QPointF(x, inner.bottom() - inner.height() * min(v, top) / top)
            if prev is not None:
                p.drawLine(prev, pt)
            prev = pt


# ======================================================================
class TodoWidget(XPWindow):
    kind = "todo"
    title = "To-do"
    SIZE = QSize(240, 280)
    MIN = QSize(190, 150)
    resizable = True

    def __init__(self, manager):
        super().__init__(manager)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(FB + 6, TB + 6, FB + 6, FB + 6)
        lay.setSpacing(5)
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Add a task and press Enter")
        self.list = QListWidget()
        self.clear_btn = QPushButton("Clear completed")
        lay.addWidget(self.entry)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.clear_btn)
        self.grip = QSizeGrip(self)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self.manager.save)
        self.entry.returnPressed.connect(self._add)
        self.list.itemChanged.connect(self._changed)
        self.clear_btn.clicked.connect(self.clear_done)
        sc = QShortcut(QKeySequence(Qt.Key_Delete), self.list)
        sc.setContext(Qt.WidgetShortcut)
        sc.activated.connect(self._delete_selected)

    def _style_item(self, item) -> None:
        t = self.manager.theme
        done = item.checkState() == Qt.Checked
        f = item.font()
        f.setStrikeOut(done)
        item.setFont(f)
        item.setForeground(QColor(t.field_border if done else t.text))

    def add_item(self, text: str, done: bool = False) -> None:
        item = QListWidgetItem(text)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if done else Qt.Unchecked)
        self.list.blockSignals(True)
        self.list.addItem(item)
        self._style_item(item)
        self.list.blockSignals(False)

    def _add(self) -> None:
        text = self.entry.text().strip()
        if text and self.list.count() < 200:
            self.add_item(text[:200])
            self.entry.clear()
            self._save_timer.start()

    def _changed(self, item) -> None:
        self.list.blockSignals(True)
        self._style_item(item)
        self.list.blockSignals(False)
        self._save_timer.start()

    def _delete_selected(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))
        self._save_timer.start()

    def clear_done(self) -> None:
        for row in reversed(range(self.list.count())):
            if self.list.item(row).checkState() == Qt.Checked:
                self.list.takeItem(row)
        self._save_timer.start()

    def apply_theme(self) -> None:
        t = self.manager.theme
        self.entry.setFont(font(9.5))
        self.list.setFont(font(9.5))
        self.clear_btn.setFont(font(8.5))
        self.entry.setStyleSheet(
            f"QLineEdit {{ background: {t.field}; border: 1px solid {t.field_border}; "
            f"color: {t.text}; padding: 2px 4px; }}")
        self.list.setStyleSheet(
            f"QListWidget {{ background: {t.field}; border: 1px solid {t.field_border}; "
            f"color: {t.text}; outline: 0; }}"
            f"QListWidget::item:selected {{ background: {t.accent}; "
            f"color: {on_color(t.accent).name()}; }}")
        self.clear_btn.setStyleSheet(button_qss(t))
        self.list.blockSignals(True)
        for row in range(self.list.count()):
            self._style_item(self.list.item(row))
        self.list.blockSignals(False)
        super().apply_theme()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.grip.move(self.width() - FB - self.grip.width() - 2,
                       self.height() - FB - self.grip.height() - 2)
        self.grip.raise_()

    def to_state(self) -> dict:
        s = super().to_state()
        s["items"] = [{"t": self.list.item(i).text(),
                       "d": self.list.item(i).checkState() == Qt.Checked}
                      for i in range(self.list.count())]
        return s

    def restore(self, s: dict) -> None:
        items = s.get("items")
        if isinstance(items, list):
            for it in items[:200]:
                if isinstance(it, dict) and isinstance(it.get("t"), str):
                    self.add_item(it["t"][:200], bool(it.get("d")))
        super().restore(s)


# ======================================================================
class WorldClockWidget(XPWindow):
    kind = "world"
    title = "World Clock"
    SIZE = QSize(250, 210)
    tick_every = 10
    DEFAULT = ["UTC", "America/New_York", "Europe/London", "Asia/Tokyo",
               "Australia/Sydney"]

    def __init__(self, manager):
        super().__init__(manager)
        self.zones = list(self.DEFAULT)
        self._tz = {}

    _ALIASES = None          # lower-case city / zone name -> canonical zone name

    @classmethod
    def _alias_map(cls) -> dict:
        if cls._ALIASES is None:
            cls._ALIASES = {}
            try:
                from zoneinfo import available_timezones
                zones = sorted(available_timezones())
                for full in zones:                      # exact names win over city names
                    cls._ALIASES[full.lower()] = full
                for full in zones:
                    last = full.split("/")[-1].lower().replace("_", " ")
                    cls._ALIASES.setdefault(last, full)
            except Exception:
                pass
        return cls._ALIASES

    def _resolve(self, name: str):
        """'Madrid', 'new york', 'utc' or 'Asia/Tokyo' -> canonical zone name or None."""
        name = name.strip()
        if not name:
            return None
        if self._zone(name) is not None:
            return name
        key = name.lower().replace("_", " ")
        found = self._alias_map().get(key) or self._alias_map().get(key.replace(" ", "_"))
        return found if found and self._zone(found) is not None else None

    def _zone(self, name):
        if name not in self._tz:
            try:
                from zoneinfo import ZoneInfo
                self._tz[name] = ZoneInfo(name)
            except Exception:
                self._tz[name] = None
        return self._tz[name]

    def set_zones(self, names) -> list:
        """Accepts zone names or plain city names. Returns the names that were rejected."""
        good, bad = [], []
        for n in names:
            n = n.strip()
            if not n:
                continue
            canon = self._resolve(n)
            if canon is None:
                bad.append(n)
            elif canon not in good:
                good.append(canon)
        if good:
            self.zones = good[:5]
            self.update()
            self.manager.save()
        return bad

    def extra_menu(self, menu) -> None:
        act = menu.addAction("Set time zones\u2026")
        act.triggered.connect(self._ask_zones)

    def _ask_zones(self) -> None:
        text, ok = QInputDialog.getText(
            self, "World Clock",
            "Up to 5 cities or time zones, separated by commas\n"
            "(e.g. Madrid, New York, Tokyo, America/Argentina/San_Juan)",
            text=", ".join(self.zones))
        if ok:
            bad = self.set_zones(text.split(","))
            if bad:
                QMessageBox.warning(self, "World Clock",
                                    "Unknown time zone(s): " + ", ".join(bad))

    def on_tick(self, n: int) -> None:
        self.update()

    def paint_dynamic(self, p, b) -> None:
        t = self.manager.theme
        now_utc = dt.datetime.now(dt.timezone.utc)
        local_date = dt.datetime.now().date()
        row_h = 34.0
        y = b.top() + 6
        for i, name in enumerate(self.zones[:5]):
            tz = self._zone(name)
            row = QRectF(b.left() + 8, y, b.width() - 16, row_h - 2)
            if i % 2 == 0:
                p.setPen(Qt.NoPen)
                p.setBrush(qc(t.field, 200))
                p.drawRect(row)
            label = name.split("/")[-1].replace("_", " ")
            if tz is None:
                draw_text(p, row, f"{label}: unknown", font(9), qc(t.second))
                y += row_h
                continue
            now = now_utc.astimezone(tz)
            off = int(now.utcoffset().total_seconds() // 60)
            sign = "+" if off >= 0 else "-"
            off_txt = f"UTC{sign}{abs(off) // 60}" + (f":{abs(off) % 60:02d}" if off % 60 else "")
            days = (now.date() - local_date).days
            sub = off_txt + (" \u00b7 tomorrow" if days == 1 else " \u00b7 yesterday" if days == -1 else "")
            txt = now.strftime("%H:%M") if self.manager.clock24 else now.strftime("%I:%M %p").lstrip("0")
            draw_text(p, QRectF(row.left() + 6, row.top() + 1, 150, 17), label,
                      font(9.5, True), qc(t.text), Qt.AlignVCenter | Qt.AlignLeft)
            draw_text(p, QRectF(row.left() + 6, row.top() + 16, 150, 14), sub,
                      font(8), qc(t.text, 170), Qt.AlignVCenter | Qt.AlignLeft)
            draw_text(p, QRectF(row.right() - 96, row.top(), 90, row_h - 2), txt,
                      font(13, True, "mono"), qc(t.accent), Qt.AlignVCenter | Qt.AlignRight)
            y += row_h

    def to_state(self) -> dict:
        s = super().to_state()
        s["zones"] = list(self.zones)
        return s

    def restore(self, s: dict) -> None:
        zones = s.get("zones")
        if isinstance(zones, list):
            self.set_zones([z for z in zones if isinstance(z, str)][:5])
        super().restore(s)


# ======================================================================
def _factorial(n):
    if isinstance(n, float) and n.is_integer():
        n = int(n)
    if not isinstance(n, int) or not 0 <= n <= 500:
        raise ValueError("factorial needs an integer 0..500")
    return math.factorial(n)


_FUNCS = {"sqrt": math.sqrt, "abs": abs, "round": round, "sin": math.sin,
          "cos": math.cos, "tan": math.tan, "log": math.log,
          "log10": math.log10, "log2": math.log2, "floor": math.floor,
          "ceil": math.ceil, "exp": math.exp, "asin": math.asin,
          "acos": math.acos, "atan": math.atan, "deg": math.degrees,
          "rad": math.radians, "fact": _factorial, "min": min, "max": max}
_CONSTS = {"pi": math.pi, "e": math.e}
_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
           ast.Mod: operator.mod, ast.Pow: operator.pow}


def safe_eval(expr: str, ans=0):
    """Evaluate a math expression WITHOUT eval(): only numbers, + - * / // % **,
    parentheses, pi, e, ans and a few functions are allowed."""
    expr = (expr.strip().replace("^", "**").replace("\u00d7", "*")
            .replace("\u00f7", "/"))
    if not expr or len(expr) > 200:
        raise ValueError("empty or too long")
    tree = ast.parse(expr, mode="eval")

    def ev(node, depth=0):
        if depth > 40:
            raise ValueError("too deep")
        if isinstance(node, ast.Expression):
            return ev(node.body, depth + 1)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.Name):
            key = node.id.lower()
            if key == "ans":
                return ans
            if key in _CONSTS:
                return _CONSTS[key]
            raise ValueError("unknown name")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = ev(node.operand, depth + 1)
            return -v if isinstance(node.op, ast.USub) else +v
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            a, b = ev(node.left, depth + 1), ev(node.right, depth + 1)
            if isinstance(node.op, ast.Pow):
                if abs(b) > 1000:
                    raise ValueError("exponent too large")
                if (isinstance(a, int) and isinstance(b, int) and b > 0 and a != 0
                        and abs(a).bit_length() * b > 4096):
                    raise ValueError("result too large")
            return _BINOPS[type(node.op)](a, b)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id.lower() in _FUNCS and not node.keywords
                and 1 <= len(node.args) <= 2):
            return _FUNCS[node.func.id.lower()](*[ev(x, depth + 1) for x in node.args])
        raise ValueError("unsupported")

    res = ev(tree)
    if isinstance(res, complex):
        raise ValueError("complex result")
    return res


def format_number(v) -> str:
    if isinstance(v, int):
        if abs(v) < 10 ** 15:
            return str(v)
        digits = str(abs(v))              # huge integers: scientific notation by hand
        mant = digits[0] + "." + digits[1:10].rstrip("0")
        return f"{'-' if v < 0 else ''}{mant.rstrip('.')}e+{len(digits) - 1}"
    if math.isnan(v) or math.isinf(v):
        raise ValueError("not a finite number")
    if abs(v) < 1e-12:                    # sin(pi) = 1.2e-16 -> 0
        return "0"
    if v.is_integer() and abs(v) < 1e15:
        return str(int(v))
    return f"{v:.10g}"


class CalcWidget(XPWindow):
    kind = "calc"
    title = "Calculator"
    SIZE = QSize(240, 232)

    def __init__(self, manager):
        super().__init__(manager)
        self.ans = 0
        self.lines = deque(maxlen=6)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(FB + 8, TB + 8, FB + 8, FB + 8)
        lay.setSpacing(6)
        self.input = QLineEdit()
        self.input.setPlaceholderText("2+2*3  sqrt(16)  5%3  ans*2  fact(5)")
        self.result = QLabel("0")
        self.result.setObjectName("result")
        self.result.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.result.setMinimumHeight(42)
        self.history = QLabel("")
        self.history.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.history.setWordWrap(True)
        lay.addWidget(self.input)
        lay.addWidget(self.result)
        lay.addWidget(self.history, 1)
        self.input.returnPressed.connect(self.evaluate)

    def extra_menu(self, menu) -> None:
        act = menu.addAction("Copy result")
        act.triggered.connect(
            lambda: QApplication.clipboard().setText(self.result.text()))
        act = menu.addAction("Clear history")
        act.triggered.connect(self._clear_history)

    def _clear_history(self) -> None:
        self.lines.clear()
        self.history.setText("")

    def evaluate(self) -> None:
        expr = self.input.text().strip()
        if not expr:
            return
        try:
            val = safe_eval(expr, self.ans)
            text = format_number(val)
        except ZeroDivisionError:
            self.result.setText("Division by 0")
            return
        except (ValueError, SyntaxError, TypeError, OverflowError, RecursionError):
            self.result.setText("Invalid")
            return
        self.ans = val
        self.result.setText(text)
        self.lines.appendleft(f"{expr} = {text}")
        self.history.setText("\n".join(self.lines))
        self.input.selectAll()

    def apply_theme(self) -> None:
        t = self.manager.theme
        self.input.setFont(font(11, False, "mono"))
        self.result.setFont(font(17, True, "mono"))
        self.history.setFont(font(8.5, False, "mono"))
        self.input.setStyleSheet(
            f"QLineEdit {{ background: {t.field}; border: 1px solid {t.field_border}; "
            f"color: {t.text}; padding: 2px 4px; }}")
        self.result.setStyleSheet(
            f"QLabel#result {{ background: {t.field}; border: 1px solid {t.field_border}; "
            f"color: {t.text}; padding: 2px 8px; }}")
        self.history.setStyleSheet(f"color: {t.text}; background: transparent;")
        super().apply_theme()


# ======================================================================
class PomodoroWidget(XPWindow):
    """Focus / short break / long break cycles (every 4th focus -> long break)."""
    kind = "pomodoro"
    title = "Pomodoro"
    SIZE = QSize(240, 170)
    tick_every = 1
    DISPLAY_H = 80
    NAMES = {"focus": "FOCUS", "short": "SHORT BREAK", "long": "LONG BREAK"}

    def __init__(self, manager):
        super().__init__(manager)
        self.lengths = {"focus": 25, "short": 5, "long": 15}      # minutes
        self.phase = "focus"
        self.done = 0               # focus sessions finished
        self.running = False
        self._t0 = 0.0
        self._before = 0.0
        self._beeps = 0
        self._flash = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(FB + 8, TB + 10 + self.DISPLAY_H + 8, FB + 8, FB + 8)
        lay.setSpacing(6)
        row = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.skip_btn = QPushButton("Skip")
        self.reset_btn = QPushButton("Reset")
        self.start_btn.clicked.connect(self.toggle)
        self.skip_btn.clicked.connect(lambda: self._advance(count=False))
        self.reset_btn.clicked.connect(self.reset)
        for btn in (self.start_btn, self.skip_btn, self.reset_btn):
            row.addWidget(btn)
        lay.addLayout(row)
        lay.addStretch(1)

    # ---- logic
    def phase_secs(self) -> int:
        return self.lengths[self.phase] * 60

    def elapsed(self) -> float:
        return self._before + ((time.monotonic() - self._t0) if self.running else 0.0)

    def remaining(self) -> float:
        return max(0.0, self.phase_secs() - self.elapsed())

    def toggle(self) -> None:
        if self.running:
            self._before = self.elapsed()
            self.running = False
            self.start_btn.setText("Start")
        else:
            self._t0 = time.monotonic()
            self.running = True
            self._flash = 0
            self.start_btn.setText("Pause")
        self.update()

    def reset(self) -> None:
        self.running = False
        self._before = 0.0
        self._flash = 0
        self._beeps = 0
        self.start_btn.setText("Start")
        self.update()

    def _advance(self, count: bool) -> None:
        """Move to the next phase and wait for Start."""
        if self.phase == "focus":
            if count:
                self.done += 1
            self.phase = "long" if (count and self.done % 4 == 0) else "short"
        else:
            self.phase = "focus"
        self.reset()
        self.manager.save()

    def keep_alive(self) -> bool:
        return self.running or self._beeps > 0

    def on_tick(self, n: int) -> None:
        finished = False
        if self.running and self.elapsed() >= self.phase_secs():
            ended = self.phase
            self._advance(count=True)
            self._flash, self._beeps = 8, 3
            finished = True
            self.manager.notify(
                "Pomodoro", "Focus session done - take a break!" if ended == "focus"
                else "Break over - back to focus.")
        if self._beeps > 0:
            self._beeps -= 1
            QApplication.beep()
        if self._flash > 0 and not finished:
            self._flash -= 1
        if self.running or self._flash > 0 or finished:
            self.update()

    # ---- settings
    def extra_menu(self, menu) -> None:
        act = menu.addAction("Set durations\u2026")
        act.triggered.connect(self._ask_lengths)

    def _ask_lengths(self) -> None:
        cur = "/".join(str(self.lengths[k]) for k in ("focus", "short", "long"))
        text, ok = QInputDialog.getText(
            self, "Pomodoro", "Minutes for focus / short break / long break\n(e.g. 25/5/15)",
            text=cur)
        if not ok:
            return
        m = re.fullmatch(r"\s*(\d{1,3})\s*[/, ]\s*(\d{1,3})\s*[/, ]\s*(\d{1,3})\s*", text)
        if not m or any(not 1 <= int(g) <= 180 for g in m.groups()):
            QMessageBox.warning(self, "Pomodoro", "Use three numbers from 1 to 180, like 25/5/15.")
            return
        self.lengths = dict(zip(("focus", "short", "long"), (int(g) for g in m.groups())))
        self.reset()
        self.manager.save()

    # ---- look
    def apply_theme(self) -> None:
        qss = button_qss(self.manager.theme)
        for btn in self.findChildren(QPushButton):
            btn.setStyleSheet(qss)
            btn.setFont(font(8.5))
        super().apply_theme()

    def paint_overlay(self, p) -> None:
        if self._flash % 2 == 1:
            p.fillRect(QRectF(0, 0, self.width(), TB), QColor(255, 60, 40, 150))

    def paint_dynamic(self, p, b) -> None:
        t = self.manager.theme
        box = QRectF(b.left() + 8, TB + 10, b.width() - 16, self.DISPLAY_H)
        sunken_box(p, box, t)
        draw_text(p, QRectF(box.left() + 6, box.top() + 2, box.width() - 12, 14),
                  self.NAMES[self.phase], font(7, True),
                  qc(t.accent if self.phase == "focus" else t.bar),
                  Qt.AlignVCenter | Qt.AlignLeft)
        secs = int(self.remaining() + 0.999)
        draw_text(p, QRectF(box.left(), box.top() + 10, box.width(), 40),
                  f"{secs // 60:02d}:{secs % 60:02d}", font(24, True, "mono"), qc(t.text))
        pct = 100.0 * self.elapsed() / max(1, self.phase_secs())
        draw_blocks(p, QRectF(box.left() + 8, box.bottom() - 22, box.width() - 70, 12),
                    min(100.0, pct), t)
        filled = self.done % 4 or (4 if self.phase == "long" else 0)
        p.setPen(QPen(qc(t.field_border), 1))
        for i in range(4):
            p.setBrush(qc(t.accent) if i < filled else QColor(t.field))
            p.drawEllipse(QRectF(box.right() - 56 + i * 12, box.bottom() - 21, 9, 9))

    def to_state(self) -> dict:
        s = super().to_state()
        s.update({"lengths": dict(self.lengths), "phase": self.phase, "done": self.done})
        return s

    def restore(self, s: dict) -> None:
        ln = s.get("lengths")
        if isinstance(ln, dict):
            for k in ("focus", "short", "long"):
                v = ln.get(k)
                if isinstance(v, int) and 1 <= v <= 180:
                    self.lengths[k] = v
        if s.get("phase") in self.NAMES:
            self.phase = s["phase"]
        d = s.get("done")
        if isinstance(d, int) and 0 <= d < 100000:
            self.done = d
        super().restore(s)


# ======================================================================
class BatteryWidget(XPWindow):
    kind = "battery"
    title = "Battery"
    SIZE = QSize(220, 116)
    tick_every = 15

    def __init__(self, manager):
        super().__init__(manager)
        self.pct = None
        self.plugged = False
        self.left = None
        self.sample()

    def sample(self) -> None:
        try:
            import psutil
            bat = psutil.sensors_battery()
        except Exception:
            bat = None
        if bat is None:
            self.pct = None
            return
        self.pct = float(bat.percent)
        self.plugged = bool(bat.power_plugged)
        self.left = bat.secsleft if isinstance(bat.secsleft, int) and bat.secsleft > 0 else None

    def on_tick(self, n: int) -> None:
        self.sample()
        self.update()

    def refresh(self) -> None:
        self.sample()
        self.update()

    def paint_dynamic(self, p, b) -> None:
        t = self.manager.theme
        if self.pct is None:
            draw_text(p, b, "No battery detected", font(9.5), qc(t.text))
            return
        low = self.pct < 20 and not self.plugged
        draw_text(p, QRectF(b.left(), b.top() + 6, b.width(), 34), f"{self.pct:.0f}%",
                  font(20, True, "mono"), qc(t.second if low else t.text))
        draw_blocks(p, QRectF(b.left() + 12, b.top() + 44, b.width() - 24, 18), self.pct, t)
        if self.plugged:
            status = "Fully charged" if self.pct >= 99 else "Charging"
        elif self.left:
            status = f"On battery \u00b7 {self.left // 3600}h {self.left % 3600 // 60:02d}m left"
        else:
            status = "On battery"
        draw_text(p, QRectF(b.left(), b.top() + 66, b.width(), 16), status,
                  font(8.5), qc(t.text, 200))


# ======================================================================
class ClipboardWidget(XPWindow):
    """Last copied texts. Event driven (no polling). Kept in memory only: never
    written to disk. Items flagged by password managers are ignored."""
    kind = "clipboard"
    title = "Clipboard"
    SIZE = QSize(250, 260)
    MIN = QSize(190, 150)
    resizable = True
    MAX_ITEMS = 12

    def __init__(self, manager):
        super().__init__(manager)
        self.items: list[str] = []
        self.paused = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(FB + 6, TB + 6, FB + 6, FB + 6)
        lay.setSpacing(5)
        self.list = QListWidget()
        self.list.setToolTip("Double-click (or Enter) to copy an item again")
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.ElideRight)
        self.clear_btn = QPushButton("Clear history")
        lay.addWidget(self.list, 1)
        lay.addWidget(self.clear_btn)
        self.grip = QSizeGrip(self)
        self.list.itemActivated.connect(self._copy_back)
        self.clear_btn.clicked.connect(self.clear)
        QApplication.clipboard().dataChanged.connect(self._on_clip)

    def _on_clip(self) -> None:
        if self.paused:
            return
        md = QApplication.clipboard().mimeData()
        if md is None or not md.hasText():
            return
        if any("ExcludeClipboardContent" in f or "PasswordManagerHint" in f
               for f in md.formats()):
            return
        text = md.text()
        if not text.strip():
            return
        text = text[:5000]
        if self.items and self.items[0] == text:
            return
        if text in self.items:
            self.items.remove(text)
        self.items.insert(0, text)
        del self.items[self.MAX_ITEMS:]
        self._fill()

    def _fill(self) -> None:
        self.list.clear()
        for text in self.items:
            line = " ".join(text.split())
            item = QListWidgetItem(line[:70] + ("\u2026" if len(line) > 70 else ""))
            item.setToolTip(text[:400])
            self.list.addItem(item)

    def _copy_back(self, item) -> None:
        row = self.list.row(item)
        if 0 <= row < len(self.items):
            QApplication.clipboard().setText(self.items[row])

    def clear(self) -> None:
        self.items.clear()
        self._fill()

    def extra_menu(self, menu) -> None:
        act = menu.addAction("Pause capture")
        act.setCheckable(True)
        act.setChecked(self.paused)
        act.triggered.connect(self._set_paused)

    def _set_paused(self, flag: bool) -> None:
        self.paused = bool(flag)
        self.manager.save()
        self.update()

    def apply_theme(self) -> None:
        t = self.manager.theme
        self.list.setFont(font(9))
        self.clear_btn.setFont(font(8.5))
        self.list.setStyleSheet(
            f"QListWidget {{ background: {t.field}; border: 1px solid {t.field_border}; "
            f"color: {t.text}; outline: 0; }}"
            f"QListWidget::item:selected {{ background: {t.accent}; "
            f"color: {on_color(t.accent).name()}; }}")
        self.clear_btn.setStyleSheet(button_qss(t))
        super().apply_theme()

    def paint_overlay(self, p) -> None:
        if self.paused:
            t = self.manager.theme
            draw_text(p, QRectF(self.width() - 140, 0, 84, TB), "paused",
                      font(8, True), qc(t.title_text, 200), Qt.AlignVCenter | Qt.AlignRight)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.grip.move(self.width() - FB - self.grip.width() - 2,
                       self.height() - FB - self.grip.height() - 2)
        self.grip.raise_()

    def to_state(self) -> dict:
        s = super().to_state()
        s["paused"] = self.paused           # the copied texts are never saved
        return s

    def restore(self, s: dict) -> None:
        self.paused = bool(s.get("paused"))
        super().restore(s)


# ======================================================================
class CountdownWidget(XPWindow):
    """Days until an event you choose (right-click > Set event)."""
    kind = "countdown"
    title = "Countdown"
    SIZE = QSize(230, 150)
    tick_every = 30            # only needed to notice that midnight has passed

    def __init__(self, manager):
        super().__init__(manager)
        self.name = ""
        self.date = None
        self._today = dt.date.today()

    def set_event(self, name: str, date) -> None:
        self.name = name.strip()[:40]
        self.date = date
        self.update()
        self.manager.save()

    def extra_menu(self, menu) -> None:
        act = menu.addAction("Set event\u2026")
        act.triggered.connect(self._ask_event)

    def _ask_event(self) -> None:
        cur = f"{self.name}, {self.date.isoformat()}" if self.date else ""
        text, ok = QInputDialog.getText(
            self, "Countdown", "Event name, date as YYYY-MM-DD\n(e.g. Exam, 2026-12-20)",
            text=cur)
        if not ok or not text.strip():
            return
        name, _, day = text.rpartition(",")
        try:
            date = dt.date.fromisoformat(day.strip())
        except ValueError:
            QMessageBox.warning(self, "Countdown", "Use the format: name, YYYY-MM-DD")
            return
        self.set_event(name or "Event", date)

    def on_tick(self, n: int) -> None:
        today = dt.date.today()
        if today != self._today:
            self._today = today
            self.update()

    def refresh(self) -> None:
        self._today = dt.date.today()
        self.update()

    def paint_dynamic(self, p, b) -> None:
        t = self.manager.theme
        if self.date is None:
            draw_text(p, b.adjusted(10, 0, -10, 0),
                      "Right-click \u203a Set event\u2026\n\nto count the days\nto a date.",
                      font(9), qc(t.text))
            return
        days = (self.date - self._today).days
        if days == 0:
            big, sub, color = "Today!", "", qc(t.second)
        elif days > 0:
            big, sub, color = str(days), "day left" if days == 1 else "days left", qc(t.accent)
        else:
            big, sub, color = str(-days), "day ago" if days == -1 else "days ago", qc(t.text, 170)
        draw_text(p, QRectF(b.left(), b.top() + 4, b.width(), 44), big,
                  font(26, True, "mono"), color)
        draw_text(p, QRectF(b.left(), b.top() + 48, b.width(), 16), sub,
                  font(9), qc(t.text, 200))
        draw_text(p, QRectF(b.left() + 6, b.top() + 68, b.width() - 12, 18), self.name,
                  font(10, True), qc(t.text))
        draw_text(p, QRectF(b.left(), b.top() + 86, b.width(), 15),
                  self.date.strftime("%a, %d %b %Y"), font(8.5), qc(t.text, 180))

    def to_state(self) -> dict:
        s = super().to_state()
        s.update({"name": self.name, "date": self.date.isoformat() if self.date else ""})
        return s

    def restore(self, s: dict) -> None:
        name, day = s.get("name"), s.get("date")
        if isinstance(name, str):
            self.name = name[:40]
        if isinstance(day, str) and day:
            try:
                self.date = dt.date.fromisoformat(day)
            except ValueError:
                pass
        super().restore(s)


# ======================================================================
class MusicWidget(XPWindow):
    """Small MP3 player (also wav, ogg, flac, m4a, aac, opus, wma).
    Keeps playing while the widgets are hidden. Needs no ticking for that:
    Qt's player is event driven; the 1 s tick only moves the seek bar."""
    kind = "music"
    title = "Music"
    SIZE = QSize(270, 350)
    MIN = QSize(236, 270)
    resizable = True
    tick_every = 1
    EXTS = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".wma", ".opus")

    def __init__(self, manager):
        super().__init__(manager)
        self.paths: list[str] = []
        self.index = -1
        self.shuffle = False
        self.repeat = "off"                  # off / all / one
        self._errors = 0
        self._playing = False
        self.player = self.audio = None
        try:
            from PySide6.QtMultimedia import QMediaPlayer
            self._QMP = QMediaPlayer
            self.player = QMediaPlayer(self)
            self._volume = 0.7
            self._ensure_audio()
            self.player.mediaStatusChanged.connect(self._status)
            self.player.errorOccurred.connect(self._error)
            self.player.playbackStateChanged.connect(self._state)
            self.player.durationChanged.connect(self._duration)
        except Exception:
            self.player = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(FB + 8, TB + 8, FB + 8, FB + 8)
        lay.setSpacing(5)
        self.now = QLabel("Nothing playing")
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 0)
        self.time = QLabel("00:00 / 00:00")
        self.time.setAlignment(Qt.AlignRight)
        self.prev_btn, self.play_btn = QPushButton("|\u25c0"), QPushButton("\u25b6")
        self.next_btn, self.stop_btn = QPushButton("\u25b6|"), QPushButton("\u25a0")
        self.shuf_btn, self.rep_btn = QPushButton("Shuf"), QPushButton("Rep off")
        self.shuf_btn.setCheckable(True)
        row = QHBoxLayout()
        for b in (self.prev_btn, self.play_btn, self.next_btn, self.stop_btn):
            row.addWidget(b)
        row2 = QHBoxLayout()
        row2.addWidget(self.shuf_btn)
        row2.addWidget(self.rep_btn)
        self.vol_label = QLabel("Vol")
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(70)
        row2.addWidget(self.vol_label)
        row2.addWidget(self.vol, 1)
        self.list = QListWidget()
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.ElideRight)
        row3 = QHBoxLayout()
        self.add_btn, self.clear_btn = QPushButton("Add files\u2026"), QPushButton("Clear")
        row3.addWidget(self.add_btn, 1)
        row3.addWidget(self.clear_btn)
        for w in (self.now, self.seek, self.time):
            lay.addWidget(w)
        lay.addLayout(row)
        lay.addLayout(row2)
        lay.addWidget(self.list, 1)
        lay.addLayout(row3)
        self.grip = QSizeGrip(self)
        self.setAcceptDrops(True)

        self.prev_btn.clicked.connect(self.prev)
        self.play_btn.clicked.connect(self.toggle)
        self.next_btn.clicked.connect(lambda: self.next(auto=False))
        self.stop_btn.clicked.connect(self.stop)
        self.shuf_btn.toggled.connect(self._set_shuffle)
        self.rep_btn.clicked.connect(self._cycle_repeat)
        self.add_btn.clicked.connect(self.add_files_dialog)
        self.clear_btn.clicked.connect(self.clear)
        self.list.itemActivated.connect(lambda it: self.play_index(self.list.row(it)))
        self.seek.sliderMoved.connect(self._seek_moved)
        self.seek.sliderReleased.connect(
            lambda: self.player and self.player.setPosition(self.seek.value()))
        self.vol.valueChanged.connect(self._set_volume)
        self.vol.sliderReleased.connect(self.manager.save)
        sc = QShortcut(QKeySequence(Qt.Key_Delete), self.list)
        sc.setContext(Qt.WidgetShortcut)
        sc.activated.connect(self._remove_selected)
        if self.player is None:
            self.now.setText("Audio support (QtMultimedia) not available")

    def _ensure_audio(self) -> bool:
        """Create the audio output only when the PC has a sound device (creating it
        without one can crash Qt). Retried on every play, so plugging in headphones
        later just works."""
        if self.audio is not None:
            return True
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaDevices
            if not QMediaDevices.audioOutputs():
                return False
            self.audio = QAudioOutput(self)
            self.audio.setVolume(self._volume)
            self.player.setAudioOutput(self.audio)
            return True
        except Exception:
            return False

    # ---- playlist
    def add_paths(self, paths) -> None:
        for path in paths:
            path = os.path.abspath(path)
            if os.path.isdir(path):
                names = sorted(os.listdir(path))
                self.add_paths([os.path.join(path, n) for n in names])
                continue
            if (path.lower().endswith(self.EXTS) and path not in self.paths
                    and len(self.paths) < 500 and os.path.isfile(path)):
                self.paths.append(path)
                self.list.addItem(os.path.splitext(os.path.basename(path))[0])
        self.manager.save()

    def add_files_dialog(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add music", "", "Audio (*.mp3 *.wav *.ogg *.flac *.m4a *.aac *.wma *.opus)")
        if files:
            self.add_paths(files)

    def add_folder_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add a music folder")
        if folder:
            self.add_paths([folder])

    def clear(self) -> None:
        self.stop()
        self.paths.clear()
        self.list.clear()
        self.index = -1
        self._show_title()
        self.manager.save()

    def _remove_selected(self) -> None:
        for item in self.list.selectedItems():
            row = self.list.row(item)
            self.list.takeItem(row)
            del self.paths[row]
            if row == self.index:
                self.stop()
                self.index = -1
            elif row < self.index:
                self.index -= 1
        self.manager.save()

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    # ---- playback
    def play_index(self, i: int) -> None:
        if self.player is None or not 0 <= i < len(self.paths):
            return
        self.index = i
        self.list.setCurrentRow(i)
        if not self._ensure_audio():
            self.now.setText("No audio output device found")
            return
        self.player.setSource(QUrl.fromLocalFile(self.paths[i]))
        self.player.play()
        self._show_title()

    def toggle(self) -> None:
        if self.player is None:
            return
        st = self.player.playbackState()
        if st == self._QMP.PlaybackState.PlayingState:
            self.player.pause()
        elif st == self._QMP.PlaybackState.PausedState:
            self.player.play()
        elif self.paths:
            sel = self.list.currentRow()
            self.play_index(sel if sel >= 0 else max(0, self.index))

    def stop(self) -> None:
        if self.player is not None:
            self.player.stop()
        self.seek.setValue(0)
        self._update_time()

    def next(self, auto: bool = False) -> None:
        n = len(self.paths)
        if not n:
            return
        if auto and self.repeat == "one" and self.index >= 0:
            self.play_index(self.index)
        elif self.shuffle and n > 1:
            import random
            choices = [i for i in range(n) if i != self.index]
            self.play_index(random.choice(choices))
        elif self.index + 1 < n:
            self.play_index(self.index + 1)
        elif self.repeat == "all" or not auto:
            self.play_index(0)
        else:
            self.stop()

    def prev(self) -> None:
        if self.player is None or not self.paths:
            return
        if self.player.position() > 3000 or self.index <= 0:
            self.player.setPosition(0)
            if self.index < 0:
                self.play_index(0)
        else:
            self.play_index(self.index - 1)

    def _status(self, status) -> None:
        if status == self._QMP.MediaStatus.EndOfMedia:
            self.next(auto=True)

    def _state(self, state) -> None:
        self._playing = state == self._QMP.PlaybackState.PlayingState
        self.play_btn.setText("\u275a\u275a" if self._playing else "\u25b6")
        if self._playing:
            self._errors = 0

    def _error(self, *_a) -> None:
        self._errors += 1
        self.now.setText("Can't play: " + os.path.basename(self.paths[self.index])
                         if 0 <= self.index < len(self.paths) else "Can't play this file")
        if self._errors < len(self.paths):           # skip unreadable files, never loop forever
            QTimer.singleShot(400, lambda: self.next(auto=True))

    def _duration(self, ms: int) -> None:
        self.seek.setRange(0, max(0, ms))
        self._update_time()

    def _seek_moved(self, ms: int) -> None:
        self._update_time(ms)

    def _set_shuffle(self, flag: bool) -> None:
        self.shuffle = bool(flag)
        self.manager.save()

    def _cycle_repeat(self) -> None:
        self.repeat = {"off": "all", "all": "one", "one": "off"}[self.repeat]
        self.rep_btn.setText({"off": "Rep off", "all": "Rep all", "one": "Rep 1"}[self.repeat])
        self.manager.save()

    def _set_volume(self, v: int) -> None:
        self._volume = v / 100.0
        if self.audio is not None:
            self.audio.setVolume(self._volume)

    # ---- display
    @staticmethod
    def _fmt(ms: int) -> str:
        s = max(0, int(ms // 1000))
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"

    def _update_time(self, pos=None) -> None:
        if self.player is None:
            return
        pos = self.player.position() if pos is None else pos
        self.time.setText(f"{self._fmt(pos)} / {self._fmt(self.player.duration())}")

    def _show_title(self) -> None:
        if self.player is None:
            return
        text = (os.path.splitext(os.path.basename(self.paths[self.index]))[0]
                if 0 <= self.index < len(self.paths) else "Nothing playing")
        self.now.setText(self.now.fontMetrics().elidedText(
            text, Qt.ElideRight, max(40, self.now.width())))

    def on_tick(self, n: int) -> None:
        if self.player is None or not self.isVisible() or self.collapsed:
            return
        if self._playing and not self.seek.isSliderDown():
            self.seek.setValue(self.player.position())
            self._update_time()

    def on_close(self) -> None:
        if self.player is not None:
            self.player.stop()

    def extra_menu(self, menu) -> None:
        act = menu.addAction("Add files\u2026")
        act.triggered.connect(self.add_files_dialog)
        act = menu.addAction("Add folder\u2026")
        act.triggered.connect(self.add_folder_dialog)

    def apply_theme(self) -> None:
        t = self.manager.theme
        qss = button_qss(t)
        for btn in self.findChildren(QPushButton):
            btn.setStyleSheet(qss)
            btn.setFont(font(8.5))
        self.now.setFont(font(9.5, True))
        self.time.setFont(font(8.5, False, "mono"))
        self.vol_label.setFont(font(8.5))
        for lab in (self.now, self.time, self.vol_label):
            lab.setStyleSheet(f"color: {t.text}; background: transparent;")
        sl = (f"QSlider::groove:horizontal {{ height: 6px; background: {t.field}; "
              f"border: 1px solid {t.field_border}; border-radius: 3px; }}"
              f"QSlider::sub-page:horizontal {{ background: {t.bar}; border-radius: 3px; }}"
              "QSlider::handle:horizontal { width: 10px; margin: -5px 0; "
              "background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
              f"stop:0 {t.button_top}, stop:1 {t.button_bottom}); "
              f"border: 1px solid {t.button_border}; border-radius: 3px; }}")
        self.seek.setStyleSheet(sl)
        self.vol.setStyleSheet(sl)
        self.list.setFont(font(9))
        self.list.setStyleSheet(
            f"QListWidget {{ background: {t.field}; border: 1px solid {t.field_border}; "
            f"color: {t.text}; outline: 0; }}"
            f"QListWidget::item:selected {{ background: {t.accent}; "
            f"color: {on_color(t.accent).name()}; }}")
        super().apply_theme()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.grip.move(self.width() - FB - self.grip.width() - 2,
                       self.height() - FB - self.grip.height() - 2)
        self.grip.raise_()
        self._show_title()

    def to_state(self) -> dict:
        s = super().to_state()
        s.update({"paths": list(self.paths), "index": self.index,
                  "volume": self.vol.value(), "shuffle": self.shuffle,
                  "repeat": self.repeat})
        return s

    def restore(self, s: dict) -> None:
        paths = s.get("paths")
        if isinstance(paths, list):
            self.add_paths([p for p in paths if isinstance(p, str)][:500])
        v = s.get("volume")
        if isinstance(v, int) and 0 <= v <= 100:
            self.vol.setValue(v)
        if s.get("shuffle"):
            self.shuf_btn.setChecked(True)
        if s.get("repeat") in ("all", "one"):
            self.repeat = s["repeat"]
            self.rep_btn.setText("Rep all" if self.repeat == "all" else "Rep 1")
        i = s.get("index")
        if isinstance(i, int) and 0 <= i < len(self.paths):
            self.index = i
            self.list.setCurrentRow(i)
        super().restore(s)


KINDS = {"clock": ClockWidget, "calendar": CalendarWidget,
         "monitor": MonitorWidget, "timer": TimerWidget, "note": NoteWidget,
         "todo": TodoWidget, "world": WorldClockWidget, "calc": CalcWidget,
         "latency": LatencyWidget, "pomodoro": PomodoroWidget,
         "battery": BatteryWidget, "clipboard": ClipboardWidget,
         "countdown": CountdownWidget, "music": MusicWidget}
KIND_LABELS = [("clock", "Clock"), ("calendar", "Calendar"),
               ("monitor", "System monitor"), ("latency", "Latency (ping)"),
               ("timer", "Timer / stopwatch"), ("pomodoro", "Pomodoro"),
               ("countdown", "Countdown to a date"), ("note", "Notes"),
               ("todo", "To-do list"), ("world", "World clock"),
               ("calc", "Calculator"), ("music", "Music player (mp3)"),
               ("clipboard", "Clipboard history"),
               ("battery", "Battery")]

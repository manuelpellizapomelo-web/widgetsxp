"""widgets.py - Windows XP / Silver style desktop widgets (PySide6).

Performance design (so it can sit next to a game):
  * Normal opaque windows with a shape mask (no per-pixel transparency).
  * The window frame and everything static is painted ONCE into a cached
    pixmap; each tick only the moving parts are drawn on top.
  * No timers inside widgets: the Manager runs ONE shared 1-second ticker,
    which is stopped completely while the widgets are hidden.
"""
from __future__ import annotations

import calendar
import datetime as dt
import time
from collections import deque

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap, QPolygonF,
                           QRadialGradient, QRegion)
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QPushButton,
                               QSizeGrip, QTextEdit, QVBoxLayout, QWidget)

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


def on_color(hex_color: str) -> QColor:
    """Black or white, whichever reads better on top of hex_color."""
    c = QColor(hex_color)
    lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return QColor("#101010") if lum > 150 else QColor("white")


def font(pt: float, bold: bool = False, kind: str = "ui") -> QFont:
    f = QFont()
    f.setFamilies({"ui": UI_FONTS, "title": TITLE_FONTS, "mono": MONO_FONTS}[kind])
    f.setPointSizeF(pt)
    f.setBold(bold)
    return f


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
        flags = Qt.FramelessWindowHint | Qt.Tool       # Tool = no taskbar button
        if manager.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
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
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseDoubleClickEvent(self, e) -> None:
        pos = e.position()
        if (e.button() == Qt.LeftButton and pos.y() < TB
                and not self.close_rect().contains(pos)
                and not self.collapse_rect().contains(pos)):
            self.set_collapsed(not self.collapsed)

    def mouseMoveEvent(self, e) -> None:
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

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
        bez.setColorAt(0.0, QColor(255, 255, 255))
        bez.setColorAt(1.0, qc(t.frame))
        p.setPen(QPen(qc(t.frame), 1.5))
        p.setBrush(bez)
        p.drawEllipse(c, R, R)
        face = QRadialGradient(QPointF(c.x(), c.y() - R * 0.3), R)
        face.setColorAt(0.0, qc(t.field))
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
        now = self.manager.now()

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
        self._today = self.manager.now().date()
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
        today = self.manager.now().date()
        if today != self._today:
            self._today = today
            self.view = (today.year, today.month)
            self.update()


# ======================================================================
class MonitorWidget(XPWindow):
    kind = "monitor"
    title = "System Monitor"
    SIZE = QSize(250, 216)
    tick_every = 2

    def __init__(self, manager):
        super().__init__(manager)
        self.cpu = 0.0
        self.mem_pct = 0.0
        self.mem_used = 0
        self.mem_total = 0
        self.rx = 0.0
        self.tx = 0.0
        self.hist = deque([0.0] * 60, maxlen=60)
        self._last_net = None
        self._psutil = None
        try:
            import psutil
            self._psutil = psutil
            psutil.cpu_percent(None)        # prime (first call always returns 0)
        except ImportError:
            pass

    def sample(self) -> None:
        ps = self._psutil
        if ps is None:
            return
        self.cpu = float(ps.cpu_percent(None))
        self.hist.append(self.cpu)
        vm = ps.virtual_memory()
        self.mem_pct = float(vm.percent)
        self.mem_total = vm.total
        self.mem_used = vm.total - vm.available
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
        self.sample()
        self.update()

    def refresh(self) -> None:
        self._last_net = None        # avoid a huge fake spike after being hidden
        self.sample()
        self.update()

    def paint_dynamic(self, p, b) -> None:
        t = self.manager.theme
        if self._psutil is None:
            draw_text(p, b, "psutil is not installed", font(9), qc(t.text))
            return
        left, width = b.left() + 10, b.width() - 20
        y = b.top() + 10
        for label, pct, value in (("CPU", self.cpu, f"{self.cpu:.0f}%"),
                                  ("RAM", self.mem_pct, f"{self.mem_pct:.0f}%")):
            draw_text(p, QRectF(left, y, 36, 18), label, font(9, True), qc(t.text),
                      Qt.AlignVCenter | Qt.AlignLeft)
            draw_blocks(p, QRectF(left + 38, y, width - 38 - 46, 18), pct, t)
            draw_text(p, QRectF(left + width - 44, y, 44, 18), value, font(9),
                      qc(t.text), Qt.AlignVCenter | Qt.AlignRight)
            y += 26
        gb = 1024 ** 3
        draw_text(p, QRectF(left, y - 4, width, 16),
                  f"{self.mem_used / gb:.1f} / {self.mem_total / gb:.1f} GB in use",
                  font(8.5), qc(t.text, 190))
        y += 20

        box = QRectF(left, y, width, 56)
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
        y += 62
        draw_text(p, QRectF(left, y, width, 18),
                  f"Down {fmt_rate(self.rx)}    Up {fmt_rate(self.tx)}",
                  font(9), qc(t.text))


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
        self.start_btn.setText("Start")
        self.update()

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
            finished = True
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


KINDS = {"clock": ClockWidget, "calendar": CalendarWidget,
         "monitor": MonitorWidget, "timer": TimerWidget, "note": NoteWidget}
KIND_LABELS = [("clock", "Clock"), ("calendar", "Calendar"),
               ("monitor", "System monitor"), ("timer", "Timer / stopwatch"),
               ("note", "Notes")]

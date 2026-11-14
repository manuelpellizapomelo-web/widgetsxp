"""Y2K Widgets - Windows XP / Silver style desktop widgets.

Run:       python main.py
Controls:  drag a widget to move it | double-click its title bar to roll it up
           right-click for the menu | left-click the tray icon to hide/show all
           Ctrl+Alt+W hides/shows every widget (handy while gaming)
Data:      positions, notes and settings are stored in a small state.json in
           your app-data folder. The only thing it ever asks the internet for is
           the current time (NTP); turn that off in the menu if you prefer.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QStandardPaths, QTimer
from PySide6.QtGui import (QActionGroup, QColor, QIcon, QLinearGradient,
                           QPainter, QPen, QPixmap)
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from hotkey import HOTKEY_LABEL, install_hotkey
from themes import load_themes
from timesync import TimeSync
from widgets import KIND_LABELS, KINDS


def make_icon_pixmap(size: int = 64) -> QPixmap:
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 64.0
    g = QLinearGradient(0, 0, 0, size)
    g.setColorAt(0, QColor("#ffffff"))
    g.setColorAt(1, QColor("#8f9ab3"))
    p.setBrush(g)
    p.setPen(QPen(QColor("#0549d8"), 3 * s))
    p.drawEllipse(int(4 * s), int(4 * s), int(56 * s), int(56 * s))
    p.setPen(QPen(QColor("#1f3f8f"), 4.5 * s, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(int(32 * s), int(32 * s), int(32 * s), int(14 * s))
    p.drawLine(int(32 * s), int(32 * s), int(44 * s), int(38 * s))
    p.end()
    return pix


def lower_priority() -> None:
    """Run below normal priority so the widgets never compete with a game."""
    try:
        import psutil
        proc = psutil.Process()
        if sys.platform == "win32":
            proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            proc.nice(10)
    except Exception:
        pass


class Manager:
    def __init__(self, app: QApplication, data_dir: Path):
        self.app = app
        self.data_dir = data_dir
        self.state_path = data_dir / "state.json"
        self.themes = load_themes(data_dir / "themes")
        self.theme = self.themes["Luna Blue"]
        self.always_on_top = False
        self.opacity = 1.0
        self.clock24 = True
        self.week_monday = True
        self.sync_time = True
        self.time_offset = dt.timedelta(0)
        self.time_ok: bool | None = None      # None = not tried yet
        self.timesync = TimeSync(self._on_time_synced, app)
        self.resync = QTimer(app)
        self.resync.setInterval(60 * 60 * 1000)   # once an hour
        self.resync.timeout.connect(self.timesync.start)
        self.visible = True
        self.widgets: list = []
        self.tray: QSystemTrayIcon | None = None
        self.hotkey_cleanup = None
        self._loading = False
        self._tick = 0
        self.ticker = QTimer(app)
        self.ticker.setInterval(1000)
        self.ticker.setTimerType(Qt.CoarseTimer)
        self.ticker.timeout.connect(self._on_tick)

    # ------------------------------------------------------------ startup
    def start(self) -> None:
        lower_priority()
        self._loading = True
        state = self._read_state()
        if state.get("theme") in self.themes:
            self.theme = self.themes[state["theme"]]
        self.always_on_top = bool(state.get("always_on_top", False))
        self.clock24 = bool(state.get("clock24", True))
        self.sync_time = bool(state.get("sync_time", True))
        self.week_monday = bool(state.get("week_monday", True))
        op = state.get("opacity", 1.0)
        self.opacity = op if isinstance(op, (int, float)) and 0.3 <= op <= 1.0 else 1.0

        items = state.get("widgets")
        if not isinstance(items, list) or not items:
            geo = self.app.primaryScreen().availableGeometry()
            x1, x2, y = geo.right() - 270, geo.right() - 530, geo.top() + 40
            items = [{"type": "clock", "x": x1, "y": y},
                     {"type": "monitor", "x": x1, "y": y + 296},
                     {"type": "note", "x": x2, "y": y,
                      "text": "Welcome!\nRight-click any widget for options.\n"
                              "Ctrl+Alt+W hides everything."}]
        for it in items:
            if isinstance(it, dict):
                self.add_widget(it.get("type", ""), it)
        self._loading = False
        self.hotkey_cleanup = install_hotkey(self.app, self.toggle_visible)
        self._setup_tray()
        self._update_ticker()
        self._apply_sync_setting()
        self.save()

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(QIcon(make_icon_pixmap(64)), self.app)
        self.tray.setToolTip("Y2K Widgets")
        self._tray_menu = QMenu()
        self._tray_menu.aboutToShow.connect(lambda: self.fill_menu(self._tray_menu))
        self.tray.setContextMenu(self._tray_menu)
        self.tray.activated.connect(
            lambda reason: self.toggle_visible()
            if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    # ------------------------------------------------------------ widgets
    def add_widget(self, kind: str, state: dict | None = None):
        cls = KINDS.get(kind)
        if cls is None:
            return None
        state = state or {}
        wd = cls(self)
        wd.restore(state)
        x, y = state.get("x"), state.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            x, y = self._spawn_pos()
        wd.move(self._clamp_to_screen(x, y))
        wd.apply_theme()
        self.widgets.append(wd)
        if self.visible:
            wd.show()
        self._update_ticker()
        self.save()
        return wd

    def new_widget(self, kind: str) -> None:
        if not self.visible:
            self.set_all_visible(True)
        self.add_widget(kind)

    def _spawn_pos(self):
        geo = self.app.primaryScreen().availableGeometry()
        n = len(self.widgets) % 6
        return geo.center().x() - 120 + 26 * n, geo.center().y() - 150 + 26 * n

    def _clamp_to_screen(self, x: int, y: int) -> QPoint:
        """Keep restored widgets reachable if a monitor was removed."""
        for screen in self.app.screens():
            if screen.availableGeometry().contains(QPoint(x + 40, y + 20)):
                return QPoint(x, y)
        geo = self.app.primaryScreen().availableGeometry()
        return QPoint(geo.left() + 60, geo.top() + 60)

    def remove(self, wd) -> None:
        if wd in self.widgets:
            self.widgets.remove(wd)
        wd.hide()
        wd.deleteLater()
        self._update_ticker()
        self.save()
        if not self.widgets and self.tray is None:
            self.quit()

    # ------------------------------------------------------------- ticker
    def _on_tick(self) -> None:
        self._tick += 1
        for wd in list(self.widgets):
            if wd.tick_every and self._tick % wd.tick_every == 0:
                wd.on_tick(self._tick)

    def _update_ticker(self) -> None:
        """The ticker only runs while something is visible: zero wake-ups otherwise."""
        want = self.visible and any(w.tick_every for w in self.widgets)
        if want and not self.ticker.isActive():
            self.ticker.start()
        elif not want and self.ticker.isActive():
            self.ticker.stop()

    # -------------------------------------------------------- visibility
    def set_all_visible(self, visible: bool) -> None:
        self.visible = bool(visible)
        for wd in self.widgets:
            wd.setVisible(self.visible)
            if self.visible:
                wd.refresh()
        self._update_ticker()

    def toggle_visible(self) -> None:
        self.set_all_visible(not self.visible)

    # ----------------------------------------------------------- settings
    def set_theme(self, name: str) -> None:
        if name in self.themes:
            self.theme = self.themes[name]
            for wd in self.widgets:
                wd.apply_theme()
            self.save()

    def set_opacity(self, value: float) -> None:
        self.opacity = float(value)
        for wd in self.widgets:
            wd.setWindowOpacity(self.opacity)
        self.save()

    def set_always_on_top(self, flag: bool) -> None:
        self.always_on_top = bool(flag)
        for wd in self.widgets:
            pos, was_visible = wd.pos(), wd.isVisible()
            wd.setWindowFlag(Qt.WindowStaysOnTopHint, self.always_on_top)
            wd.move(pos)
            if was_visible:
                wd.show()
        self.save()

    # --------------------------------------------------------- real time
    def now(self) -> dt.datetime:
        """PC time corrected with the offset learned from the internet."""
        return dt.datetime.now() + self.time_offset

    def _apply_sync_setting(self) -> None:
        if self.sync_time:
            self.timesync.start()
            self.resync.start()
        else:
            self.resync.stop()
            self.time_offset = dt.timedelta(0)
            self.time_ok = None
            self._refresh_all()

    def _on_time_synced(self, offset) -> None:
        if not self.sync_time:
            return
        if offset is None:
            self.time_ok = False          # offline: keep the last known offset
        else:
            self.time_offset = dt.timedelta(seconds=offset)
            self.time_ok = True
        self._refresh_all()

    def _refresh_all(self) -> None:
        for wd in self.widgets:
            wd.update()

    def set_sync_time(self, flag: bool) -> None:
        self.sync_time = bool(flag)
        self._apply_sync_setting()
        self.save()

    def sync_now(self) -> None:
        if self.sync_time:
            self.timesync.start()
        else:
            self.set_sync_time(True)

    def set_clock24(self, flag: bool) -> None:
        self.clock24 = bool(flag)
        for wd in self.widgets:
            wd.update()
        self.save()

    def set_week_monday(self, flag: bool) -> None:
        self.week_monday = bool(flag)
        for wd in self.widgets:
            wd.update()
        self.save()

    # -------------------------------------------------------------- menus
    def fill_menu(self, menu: QMenu, widget=None) -> None:
        menu.clear()
        sub = menu.addMenu("New widget")
        for kind, label in KIND_LABELS:
            act = sub.addAction(label)
            act.triggered.connect(lambda _c=False, k=kind: self.new_widget(k))

        label = "Show all widgets" if not self.visible else "Hide all widgets"
        if self.hotkey_cleanup is not None:
            label += f"\t{HOTKEY_LABEL}"
        act = menu.addAction(label)
        act.triggered.connect(self.toggle_visible)
        menu.addSeparator()

        sub = menu.addMenu("Theme")
        group = QActionGroup(sub)
        group.setExclusive(True)
        for name in self.themes:
            act = sub.addAction(name)
            act.setCheckable(True)
            act.setChecked(name == self.theme.name)
            group.addAction(act)
            act.triggered.connect(lambda _c=False, n=name: self.set_theme(n))

        sub = menu.addMenu("Opacity")
        group = QActionGroup(sub)
        group.setExclusive(True)
        for pct in (100, 85, 70, 50):
            act = sub.addAction(f"{pct}%")
            act.setCheckable(True)
            act.setChecked(abs(self.opacity - pct / 100.0) < 0.01)
            group.addAction(act)
            act.triggered.connect(lambda _c=False, v=pct / 100.0: self.set_opacity(v))

        for text, value, setter in (
                ("Always on top", self.always_on_top, self.set_always_on_top),
                ("24-hour clock", self.clock24, self.set_clock24),
                ("Time from the internet", self.sync_time, self.set_sync_time),
                ("Week starts on Monday", self.week_monday, self.set_week_monday)):
            act = menu.addAction(text)
            act.setCheckable(True)
            act.setChecked(value)
            act.triggered.connect(setter)

        if self.sync_time:
            if self.time_ok is None:
                status = "Checking the time..."
            elif self.time_ok:
                secs = self.time_offset.total_seconds()
                status = f"Synced (PC was off by {secs:+.1f} s) - sync now"
            else:
                status = "No internet - using PC time - retry"
            act = menu.addAction(status)
            act.triggered.connect(self.sync_now)

        if widget is not None:
            menu.addSeparator()
            act = menu.addAction("Close this widget")
            act.triggered.connect(lambda: self.remove(widget))
        menu.addSeparator()
        act = menu.addAction("Quit")
        act.triggered.connect(self.quit)

    def make_menu(self, widget=None) -> QMenu:
        menu = QMenu()
        self.fill_menu(menu, widget)
        return menu

    # --------------------------------------------------------- persistence
    def _read_state(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self) -> None:
        if self._loading:
            return
        data = {"theme": self.theme.name, "always_on_top": self.always_on_top,
                "opacity": self.opacity, "clock24": self.clock24,
                "week_monday": self.week_monday, "sync_time": self.sync_time,
                "widgets": [w.to_state() for w in self.widgets]}
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    def quit(self) -> None:
        self.save()
        self.ticker.stop()
        self.resync.stop()
        if self.hotkey_cleanup is not None:
            self.hotkey_cleanup()
        if self.tray is not None:
            self.tray.hide()
        self.app.quit()


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Y2KWidgets")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(make_icon_pixmap(64)))
    override = os.environ.get("Y2K_WIDGETS_HOME")
    data_dir = Path(override) if override else Path(
        QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
    manager = Manager(app, data_dir)
    manager.start()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

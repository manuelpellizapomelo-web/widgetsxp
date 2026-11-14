"""Y2K Widgets - Windows XP / Silver style desktop widgets.

Run:       python main.py
Controls:  drag a widget to move it | double-click its title bar to roll it up
           right-click for the menu | left-click the tray icon to hide/show all
           Ctrl+Alt+W hides/shows every widget, Ctrl+Alt+G toggles click-through,
           Ctrl+Alt+T switches to the next skin
Data:      positions, notes and settings are stored in a small state.json in
           your app-data folder. The app never connects to the internet.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import (QActionGroup, QColor, QDesktopServices, QIcon,
                           QLinearGradient, QPainter, QPen, QPixmap)
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import autostart
from hotkey import PREFIX, install_hotkeys
from themes import ensure_user_dir, load_themes
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
        self.themes_dir = data_dir / "themes"
        ensure_user_dir(self.themes_dir)
        self.themes = load_themes(self.themes_dir)
        self.theme = self.themes["Luna Blue"]
        self.always_on_top = False
        self.opacity = 1.0
        self.clock24 = True
        self.week_monday = True
        self.snap = True
        self.locked = False
        self.click_through = False
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
        self.week_monday = bool(state.get("week_monday", True))
        self.snap = bool(state.get("snap", True))
        self.locked = bool(state.get("locked", False))
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
        self.hotkey_cleanup = install_hotkeys(
            self.app, {"W": self.toggle_visible, "G": self.toggle_click_through,
                       "T": self.next_theme})
        self._setup_tray()
        self._update_ticker()
        self.save()

    def notify(self, title: str, text: str) -> None:
        """Tray balloon (used by timers, which must be able to ring while hidden)."""
        if self.tray is not None:
            self.tray.showMessage(title, text, QSystemTrayIcon.Information, 6000)

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
        wd.on_close()
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
            if not wd.tick_every or self._tick % wd.tick_every:
                continue
            if self.visible or wd.keep_alive():
                wd.on_tick(self._tick)
        if not self.visible:
            self._update_ticker()       # stop again as soon as no timer is running

    def _update_ticker(self) -> None:
        """The ticker only runs while something is visible (or a timer is running
        and has to ring): zero wake-ups otherwise."""
        want = any(w.tick_every and (self.visible or w.keep_alive())
                   for w in self.widgets)
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

    # ------------------------------------------------------- window helpers
    def window_flags(self):
        flags = Qt.FramelessWindowHint | Qt.Tool          # Tool = no taskbar button
        if self.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        if self.click_through:
            flags |= Qt.WindowTransparentForInput
        return flags

    def _reapply_flags(self) -> None:
        for wd in self.widgets:
            pos, was_visible = wd.pos(), wd.isVisible()
            wd.setWindowFlags(self.window_flags())
            wd.setWindowOpacity(self.opacity)
            wd.move(pos)
            if was_visible:
                wd.show()

    def snap_pos(self, wd, pos: QPoint) -> QPoint:
        """Magnetic edges: snap to screen borders and to other widgets."""
        if not self.snap:
            return pos
        thr = 14
        w, h = wd.width(), wd.height()
        center = QPoint(pos.x() + w // 2, pos.y() + h // 2)
        geo = (self.app.screenAt(center) or self.app.primaryScreen()).availableGeometry()
        x, y = pos.x(), pos.y()
        if abs(x - geo.left()) < thr:
            x = geo.left()
        elif abs(x + w - (geo.right() + 1)) < thr:
            x = geo.right() + 1 - w
        if abs(y - geo.top()) < thr:
            y = geo.top()
        elif abs(y + h - (geo.bottom() + 1)) < thr:
            y = geo.bottom() + 1 - h
        for o in self.widgets:
            if o is wd or not o.isVisible():
                continue
            og = o.geometry()
            if y < og.bottom() + thr and y + h > og.top() - thr:
                if abs(x - (og.right() + 1)) < thr:
                    x = og.right() + 1
                elif abs(x + w - og.left()) < thr:
                    x = og.left() - w
                elif abs(x - og.left()) < thr:
                    x = og.left()
            if x < og.right() + thr and x + w > og.left() - thr:
                if abs(y - (og.bottom() + 1)) < thr:
                    y = og.bottom() + 1
                elif abs(y + h - og.top()) < thr:
                    y = og.top() - h
                elif abs(y - og.top()) < thr:
                    y = og.top()
        return QPoint(x, y)

    def can_escape_click_through(self) -> bool:
        return self.tray is not None or self.hotkey_cleanup is not None

    # ----------------------------------------------------------- settings
    def set_theme(self, name: str) -> None:
        if name in self.themes:
            self.theme = self.themes[name]
            for wd in self.widgets:
                wd.apply_theme()
            self.save()

    def next_theme(self) -> None:
        names = list(self.themes)
        i = names.index(self.theme.name) if self.theme.name in names else -1
        self.set_theme(names[(i + 1) % len(names)])

    def reload_themes(self) -> None:
        """Pick up skins added to / edited in the themes folder without restarting."""
        self.themes = load_themes(self.themes_dir)
        if self.theme.name not in self.themes:
            self.theme = self.themes["Luna Blue"]
        else:
            self.theme = self.themes[self.theme.name]
        for wd in self.widgets:
            wd.apply_theme()
        self.save()

    def open_themes_folder(self) -> None:
        ensure_user_dir(self.themes_dir)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.themes_dir)))

    def reset_layout(self) -> None:
        """Tidy every widget into columns along the right edge of the main screen."""
        geo = self.app.primaryScreen().availableGeometry()
        right, y, col_w = geo.right() + 1 - 10, geo.top() + 10, 0
        for wd in self.widgets:
            w, h = wd.width(), wd.height()
            if col_w and y + h > geo.bottom():
                right -= col_w + 10
                y, col_w = geo.top() + 10, 0
            wd.move(max(geo.left() + 10, right - w), y)
            y += h + 10
            col_w = max(col_w, w)
        self.save()

    def set_opacity(self, value: float) -> None:
        self.opacity = float(value)
        for wd in self.widgets:
            wd.setWindowOpacity(self.opacity)
        self.save()

    def set_always_on_top(self, flag: bool) -> None:
        self.always_on_top = bool(flag)
        self._reapply_flags()
        self.save()

    def set_click_through(self, flag: bool) -> None:
        """Widgets ignore the mouse (clicks go to the game). Needs a way back:
        the tray icon or the Ctrl+Alt+G hotkey."""
        flag = bool(flag)
        if flag and not self.can_escape_click_through():
            return
        self.click_through = flag
        self._reapply_flags()

    def toggle_click_through(self) -> None:
        self.set_click_through(not self.click_through)

    def set_locked(self, flag: bool) -> None:
        self.locked = bool(flag)
        self.save()

    def set_snap(self, flag: bool) -> None:
        self.snap = bool(flag)
        self.save()

    def set_autostart(self, flag: bool) -> None:
        autostart.set_enabled(bool(flag))

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

        registered = getattr(self.hotkey_cleanup, "registered", set())
        label = "Show all widgets" if not self.visible else "Hide all widgets"
        if "W" in registered:
            label += f"\t{PREFIX}W"
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
        sub.addSeparator()
        label = "Next skin"
        if "T" in registered:
            label += f"\t{PREFIX}T"
        act = sub.addAction(label)
        act.triggered.connect(self.next_theme)
        act = sub.addAction("Reload skins")
        act.triggered.connect(self.reload_themes)
        act = sub.addAction("Open skins folder")
        act.triggered.connect(self.open_themes_folder)

        act = menu.addAction("Reset layout")
        act.triggered.connect(self.reset_layout)

        sub = menu.addMenu("Opacity")
        group = QActionGroup(sub)
        group.setExclusive(True)
        for pct in (100, 85, 70, 50):
            act = sub.addAction(f"{pct}%")
            act.setCheckable(True)
            act.setChecked(abs(self.opacity - pct / 100.0) < 0.01)
            group.addAction(act)
            act.triggered.connect(lambda _c=False, v=pct / 100.0: self.set_opacity(v))

        ct_label = "Click-through (ignore mouse)"
        if "G" in registered:
            ct_label += f"\t{PREFIX}G"
        items = [("Always on top", self.always_on_top, self.set_always_on_top, True),
                 (ct_label, self.click_through, self.set_click_through,
                  self.can_escape_click_through()),
                 ("Lock positions", self.locked, self.set_locked, True),
                 ("Snap to edges", self.snap, self.set_snap, True),
                 ("24-hour clock", self.clock24, self.set_clock24, True),
                 ("Week starts on Monday", self.week_monday, self.set_week_monday, True)]
        if autostart.supported():
            items.append(("Start with Windows", autostart.is_enabled(),
                          self.set_autostart, True))
        for text, value, setter, enabled in items:
            act = menu.addAction(text)
            act.setCheckable(True)
            act.setChecked(value)
            act.setEnabled(enabled)
            act.triggered.connect(setter)

        if widget is not None:
            menu.addSeparator()
            before = len(menu.actions())
            widget.extra_menu(menu)
            if len(menu.actions()) > before:
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
                "week_monday": self.week_monday, "snap": self.snap,
                "locked": self.locked,
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

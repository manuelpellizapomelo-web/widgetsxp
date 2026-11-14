"""themes.py - XP-style color themes ("skins"). Built-in + custom .json themes.

To make your own theme, drop a .json file in the `themes` folder (inside the
app data folder) with any of the keys below. Missing or invalid keys fall back
to "Luna Blue". Colors are "#RRGGBB".
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class Theme:
    name: str
    title_top: str        # title bar gradient: top highlight
    title_mid: str        #                     main body
    title_bottom: str     #                     bottom edge
    title_text: str
    title_shadow: str
    frame: str            # window border
    body: str             # window background
    text: str
    field: str            # text boxes / displays
    field_border: str
    accent: str           # clock hands, today marker, graph line
    second: str           # seconds hand, Sundays
    bar: str              # progress blocks
    button_top: str
    button_bottom: str
    button_border: str
    close_top: str
    close_bottom: str


BUILTIN = [
    Theme("Luna Blue",
          title_top="#4d9bff", title_mid="#0a5cf2", title_bottom="#0549d8",
          title_text="#ffffff", title_shadow="#0a2a80", frame="#0831d9",
          body="#ece9d8", text="#1a1a1a", field="#ffffff",
          field_border="#7f9db9", accent="#1f3f8f", second="#d32f2f",
          bar="#34c534", button_top="#ffffff", button_bottom="#dcd8c4",
          button_border="#003c74", close_top="#e98a6f", close_bottom="#c8401b"),
    Theme("Silver",
          title_top="#fbfcfe", title_mid="#d9deea", title_bottom="#a9b2c8",
          title_text="#1d2740", title_shadow="#ffffff", frame="#8a93a8",
          body="#eeeff4", text="#1a1a22", field="#ffffff",
          field_border="#9aa3b8", accent="#3b4560", second="#d9363e",
          bar="#4aa3e8", button_top="#ffffff", button_bottom="#dfe2ea",
          button_border="#6b7690", close_top="#e98a6f", close_bottom="#c8401b"),
    Theme("Olive",
          title_top="#b9d38e", title_mid="#8fae5c", title_bottom="#738f43",
          title_text="#ffffff", title_shadow="#3b4d1c", frame="#5a7433",
          body="#ecead8", text="#1a1a1a", field="#ffffff",
          field_border="#8fa56a", accent="#4b6126", second="#c0392b",
          bar="#7cc23c", button_top="#ffffff", button_bottom="#dcdcc4",
          button_border="#5a7433", close_top="#e98a6f", close_bottom="#c8401b"),
    Theme("Royale Noir",
          title_top="#4a5a78", title_mid="#1c2438", title_bottom="#0b0f1c",
          title_text="#ffffff", title_shadow="#000000", frame="#05070f",
          body="#1b1f2b", text="#e6e9f2", field="#0e1119",
          field_border="#3a4560", accent="#4da3ff", second="#ff5a5a",
          bar="#4da3ff", button_top="#3a4258", button_bottom="#1c2132",
          button_border="#05070f", close_top="#e98a6f", close_bottom="#c8401b"),
    Theme("Bubblegum",
          title_top="#ffb3dd", title_mid="#ff5cb8", title_bottom="#e0359a",
          title_text="#ffffff", title_shadow="#8a1a5c", frame="#c2287f",
          body="#ffe9f5", text="#4a1238", field="#ffffff",
          field_border="#e58fc3", accent="#c2287f", second="#2a7fff",
          bar="#ff5cb8", button_top="#ffffff", button_bottom="#ffd0ea",
          button_border="#c2287f", close_top="#e98a6f", close_bottom="#c8401b"),
    Theme("Bondi Aqua",
          title_top="#9ff0f0", title_mid="#19c3c8", title_bottom="#0b9ea6",
          title_text="#ffffff", title_shadow="#05545a", frame="#0a8089",
          body="#e3f8f8", text="#0f3a3d", field="#ffffff",
          field_border="#6fc7cc", accent="#0a6f78", second="#ff6a3d",
          bar="#19c3c8", button_top="#ffffff", button_bottom="#c6eeee",
          button_border="#0a8089", close_top="#e98a6f", close_bottom="#c8401b"),
    Theme("Grape",
          title_top="#d5b8ff", title_mid="#9b64ec", title_bottom="#7a3fd0",
          title_text="#ffffff", title_shadow="#3b1a78", frame="#5f2fb0",
          body="#f1e9ff", text="#2a1650", field="#ffffff",
          field_border="#b496ea", accent="#5f2fb0", second="#e0357a",
          bar="#9b64ec", button_top="#ffffff", button_bottom="#ddd0f5",
          button_border="#5f2fb0", close_top="#e98a6f", close_bottom="#c8401b"),
    Theme("Tangerine",
          title_top="#ffc27a", title_mid="#ff8a1f", title_bottom="#e06a00",
          title_text="#ffffff", title_shadow="#7a3500", frame="#b85400",
          body="#fff1e0", text="#3d2000", field="#ffffff",
          field_border="#e8b070", accent="#b85400", second="#2a6fd0",
          bar="#ff8a1f", button_top="#ffffff", button_bottom="#f5dcbc",
          button_border="#b85400", close_top="#e98a6f", close_bottom="#c8401b"),
    Theme("Cherry",
          title_top="#ff8d8d", title_mid="#d9232d", title_bottom="#a8121a",
          title_text="#ffffff", title_shadow="#5a0509", frame="#7e0c12",
          body="#f7e6e6", text="#3a0b0e", field="#ffffff",
          field_border="#d79a9d", accent="#8e1018", second="#2a6fd0",
          bar="#d9232d", button_top="#ffffff", button_bottom="#ecd0d1",
          button_border="#7e0c12", close_top="#e98a6f", close_bottom="#c8401b"),
    Theme("Graphite",
          title_top="#8d939c", title_mid="#565c66", title_bottom="#3a3f48",
          title_text="#ffffff", title_shadow="#14171c", frame="#23272e",
          body="#d9dce1", text="#1b1e24", field="#ffffff",
          field_border="#8d939c", accent="#3a3f48", second="#d9363e",
          bar="#6b7a90", button_top="#ffffff", button_bottom="#cfd3da",
          button_border="#3a3f48", close_top="#e98a6f", close_bottom="#c8401b"),
    Theme("Matrix",
          title_top="#1f5a2a", title_mid="#0d3b18", title_bottom="#06240d",
          title_text="#7dff9a", title_shadow="#000000", frame="#02120a",
          body="#07140b", text="#7dff9a", field="#020a05",
          field_border="#1f7a38", accent="#3dff6e", second="#ff4d4d",
          bar="#3dff6e", button_top="#12351b", button_bottom="#08210f",
          button_border="#1f7a38", close_top="#e98a6f", close_bottom="#c8401b"),
    Theme("Vaporwave",
          title_top="#5b2a86", title_mid="#2d1450", title_bottom="#1a0b33",
          title_text="#ff9af5", title_shadow="#000000", frame="#0f0620",
          body="#1a0e33", text="#e9d8ff", field="#0d0620",
          field_border="#6b3fb0", accent="#00e5ff", second="#ff4fa8",
          bar="#00e5ff", button_top="#3a2266", button_bottom="#1f1040",
          button_border="#0f0620", close_top="#e98a6f", close_bottom="#c8401b"),
]

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def load_themes(user_dir: Path | None = None) -> dict[str, Theme]:
    """Built-in themes plus any valid *.json theme found in user_dir."""
    themes = {t.name: t for t in BUILTIN}
    if user_dir is None or not user_dir.is_dir():
        return themes
    base = asdict(BUILTIN[0])
    color_keys = {f.name for f in fields(Theme)} - {"name"}
    for path in sorted(user_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            merged = dict(base)
            merged["name"] = str(data.get("name") or path.stem)[:40]
            for key in color_keys:
                value = data.get(key)
                if isinstance(value, str) and _HEX.match(value):
                    merged[key] = value
            themes[merged["name"]] = Theme(**merged)
        except (OSError, ValueError, TypeError):
            continue
    return themes

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
    face: str = "#ffffff"   # clock face center


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

    Theme("Classic 98",
          title_top="#2a6fd0", title_mid="#0a2a8a", title_bottom="#000080",
          title_text="#ffffff", title_shadow="#000040", frame="#404040",
          body="#c0c0c0", text="#000000", field="#ffffff",
          field_border="#808080", accent="#000080", second="#c00000",
          bar="#000080", button_top="#e4e4e4", button_bottom="#c0c0c0",
          button_border="#404040", close_top="#e98a6f", close_bottom="#c8401b", face="#ffffff"),
    Theme("Royale Noir",
          title_top="#3b4a66", title_mid="#1d2740", title_bottom="#0f1626",
          title_text="#ffffff", title_shadow="#000000", frame="#0a0f1c",
          body="#262c3a", text="#e8ecf5", field="#171c28",
          field_border="#4a5672", accent="#6fb4ff", second="#ff6b6b",
          bar="#3fa9ff", button_top="#3a4560", button_bottom="#262e44",
          button_border="#5a6788", close_top="#e98a6f", close_bottom="#c8401b", face="#1a2030"),
    Theme("Frutiger Aero",
          title_top="#c9f3ff", title_mid="#5fc8f0", title_bottom="#2aa0d8",
          title_text="#ffffff", title_shadow="#0b5a82", frame="#1b86b8",
          body="#e8f8ff", text="#0b3a52", field="#ffffff",
          field_border="#7cc4e0", accent="#1a9ad6", second="#ff7a3d",
          bar="#4cd964", button_top="#ffffff", button_bottom="#cdeefb",
          button_border="#4aa6cc", close_top="#e98a6f", close_bottom="#c8401b", face="#ffffff"),
    Theme("Bubblegum",
          title_top="#ffd1ee", title_mid="#ff7fc4", title_bottom="#f05aa8",
          title_text="#ffffff", title_shadow="#8a1e63", frame="#c2278b",
          body="#fff0f9", text="#5b1040", field="#ffffff",
          field_border="#f0a6d2", accent="#d6338f", second="#6a5cff",
          bar="#ff6fb5", button_top="#ffffff", button_bottom="#ffd9ef",
          button_border="#d6338f", close_top="#e98a6f", close_bottom="#c8401b", face="#ffffff"),
    Theme("Terminal",
          title_top="#1f3a1f", title_mid="#0f220f", title_bottom="#081408",
          title_text="#7dff7d", title_shadow="#000000", frame="#2aa02a",
          body="#050a05", text="#8dff8d", field="#000000",
          field_border="#2aa02a", accent="#33ff33", second="#ffb000",
          bar="#33ff33", button_top="#142814", button_bottom="#0a160a",
          button_border="#2aa02a", close_top="#a04040", close_bottom="#601818",
          face="#0a140a"),
    Theme("Sunset",
          title_top="#ffb36b", title_mid="#ff6b6b", title_bottom="#a83279",
          title_text="#ffffff", title_shadow="#5a1040", frame="#7a2a6a",
          body="#fff1e6", text="#4a1f3a", field="#ffffff",
          field_border="#e2a0a8", accent="#c2406a", second="#7a3cff",
          bar="#ff8a4a", button_top="#ffffff", button_bottom="#ffe0cc",
          button_border="#c2406a", close_top="#e98a6f", close_bottom="#c8401b", face="#ffffff"),
    Theme("Lime Soda",
          title_top="#e8ff9a", title_mid="#8be04a", title_bottom="#4cae1f",
          title_text="#0f3a00", title_shadow="#ffffff", frame="#3a8a14",
          body="#f2ffe6", text="#1d3d0a", field="#ffffff",
          field_border="#9ad06a", accent="#2f8f12", second="#ff5a3c",
          bar="#5ed32a", button_top="#ffffff", button_bottom="#dff5c4",
          button_border="#3a8a14", close_top="#e98a6f", close_bottom="#c8401b", face="#ffffff"),

    # ---- extra skins -------------------------------------------------
    Theme("Zune Orange",
          title_top="#ff9a3c", title_mid="#f26a00", title_bottom="#c24f00",
          title_text="#ffffff", title_shadow="#5a2400", frame="#8a3a00",
          body="#2b2b2e", text="#f2f2f2", field="#1a1a1c",
          field_border="#6a4a30", accent="#ff8a1f", second="#4fc3f7",
          bar="#ff8a1f", button_top="#4a4a50", button_bottom="#2e2e33",
          button_border="#8a3a00", close_top="#e98a6f", close_bottom="#c8401b",
          face="#232326"),
    Theme("Aqua",
          title_top="#e9f4ff", title_mid="#9ccaf5", title_bottom="#5a9fe0",
          title_text="#0d2a4a", title_shadow="#ffffff", frame="#4b7fb8",
          body="#f2f6fb", text="#1b2a3a", field="#ffffff",
          field_border="#9db8d6", accent="#2f7de1", second="#e5453a",
          bar="#4da3ff", button_top="#ffffff", button_bottom="#d5e4f5",
          button_border="#5b86b8", close_top="#ff8d82", close_bottom="#d9362b"),
    Theme("iMac Graphite",
          title_top="#cfd2d6", title_mid="#9aa0a8", title_bottom="#6f757e",
          title_text="#ffffff", title_shadow="#2a2d33", frame="#4a4f57",
          body="#d9dbde", text="#1d1f23", field="#ffffff",
          field_border="#8c919a", accent="#4a5160", second="#e0463c",
          bar="#6f8fbf", button_top="#f4f5f6", button_bottom="#c2c5ca",
          button_border="#4a4f57", close_top="#e98a6f", close_bottom="#c8401b",
          face="#f4f5f6"),
    Theme("Tangerine",
          title_top="#ffd08a", title_mid="#ff9a2e", title_bottom="#e06d00",
          title_text="#ffffff", title_shadow="#7a3600", frame="#b85a00",
          body="#fff3e3", text="#5a2c00", field="#ffffff",
          field_border="#f0b46a", accent="#e06d00", second="#2f7de1",
          bar="#ff9a2e", button_top="#ffffff", button_bottom="#ffe2bd",
          button_border="#b85a00", close_top="#e98a6f", close_bottom="#c8401b"),
    Theme("Grape Soda",
          title_top="#d9b8ff", title_mid="#9b59f0", title_bottom="#6d2fc7",
          title_text="#ffffff", title_shadow="#2e0f63", frame="#5a24a8",
          body="#f4ecff", text="#3a1a66", field="#ffffff",
          field_border="#bb98ee", accent="#7b3fdc", second="#ff5a8a",
          bar="#a066ff", button_top="#ffffff", button_bottom="#e3d2fb",
          button_border="#5a24a8", close_top="#e98a6f", close_bottom="#c8401b"),
    Theme("Amber CRT",
          title_top="#3a2a0a", title_mid="#241806", title_bottom="#140d03",
          title_text="#ffb000", title_shadow="#000000", frame="#b87800",
          body="#0d0802", text="#ffb84a", field="#000000",
          field_border="#b87800", accent="#ffb000", second="#ff5a2a",
          bar="#ffb000", button_top="#2a1c08", button_bottom="#160f04",
          button_border="#b87800", close_top="#a04040", close_bottom="#601818",
          face="#140d03"),
    Theme("Vaporwave",
          title_top="#ff8bd8", title_mid="#b84dff", title_bottom="#5a3cff",
          title_text="#ffffff", title_shadow="#1a0a4a", frame="#2a1a6a",
          body="#1f1647", text="#e8dcff", field="#150f35",
          field_border="#7a5cff", accent="#00f0ff", second="#ff4fd8",
          bar="#00f0ff", button_top="#4a3a9a", button_bottom="#2a2070",
          button_border="#7a5cff", close_top="#ff8bd8", close_bottom="#c2278b",
          face="#241a55"),
    Theme("Sakura",
          title_top="#fff0f5", title_mid="#ffc2d6", title_bottom="#f59ab8",
          title_text="#7a1f45", title_shadow="#ffffff", frame="#d97a9c",
          body="#fff7fa", text="#5a2a3c", field="#ffffff",
          field_border="#f2b6c9", accent="#d9577f", second="#6a8fd8",
          bar="#ff9bb8", button_top="#ffffff", button_bottom="#ffe1ea",
          button_border="#d97a9c", close_top="#e98a6f", close_bottom="#c8401b"),
    Theme("Ocean Teal",
          title_top="#7fe0d8", title_mid="#1fb0a6", title_bottom="#0e7f78",
          title_text="#ffffff", title_shadow="#04403c", frame="#0a6a64",
          body="#e6f7f5", text="#0b3a36", field="#ffffff",
          field_border="#7cc9c3", accent="#0e8f86", second="#ff7a4a",
          bar="#27c9bd", button_top="#ffffff", button_bottom="#c9eeea",
          button_border="#0a6a64", close_top="#e98a6f", close_bottom="#c8401b"),
    Theme("Chocolate",
          title_top="#a8805e", title_mid="#7a4e2d", title_bottom="#54331a",
          title_text="#fff1e0", title_shadow="#2a1608", frame="#3d2410",
          body="#efe2d2", text="#2e1a0a", field="#fffaf3",
          field_border="#b8926e", accent="#7a4e2d", second="#c2402b",
          bar="#b5773a", button_top="#fff6ea", button_bottom="#dcc6aa",
          button_border="#3d2410", close_top="#e98a6f", close_bottom="#c8401b",
          face="#fffaf3"),
    Theme("Mint Cream",
          title_top="#e6fff2", title_mid="#a6efc8", title_bottom="#6cd6a0",
          title_text="#0f4a2e", title_shadow="#ffffff", frame="#4fb582",
          body="#f4fffa", text="#15412a", field="#ffffff",
          field_border="#a6dcc0", accent="#2fa56a", second="#ff6f6f",
          bar="#4fd391", button_top="#ffffff", button_bottom="#d3f5e3",
          button_border="#4fb582", close_top="#e98a6f", close_bottom="#c8401b"),
    Theme("Cherry Cola",
          title_top="#e0505a", title_mid="#a01a28", title_bottom="#6a0a16",
          title_text="#ffffff", title_shadow="#2a0006", frame="#4a0610",
          body="#2a1014", text="#f6dede", field="#1a0a0c",
          field_border="#7a3038", accent="#ff6b78", second="#ffd166",
          bar="#e03a4a", button_top="#5a2028", button_bottom="#3a1219",
          button_border="#8a3a44", close_top="#e98a6f", close_bottom="#c8401b",
          face="#331418"),
]

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def ensure_user_dir(user_dir: Path) -> None:
    """Create the custom-skins folder (with an example file that is NOT loaded:
    rename example_theme.json.txt to something.json to try it)."""
    try:
        user_dir.mkdir(parents=True, exist_ok=True)
        example = user_dir / "example_theme.json.txt"
        if not example.exists():
            base = asdict(BUILTIN[0])
            base["name"] = "My Theme"
            example.write_text(json.dumps(base, indent=2), encoding="utf-8")
    except OSError:
        pass


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

"""System tray icon for Janus (Windows / macOS / Linux via pystray).

The icon shows a pending-file count badge and a menu with:
  - Open Dashboard  (opens http://localhost:<port> in the browser)
  - Pause / Resume Watcher  (toggle)
  - Quit

If pystray or Pillow can't be imported (e.g. headless CI), the function
start_tray() simply returns None and Janus runs without a tray icon.
"""
from __future__ import annotations

import sqlite3
import threading
import webbrowser
from typing import Callable

# ── Icon rendering ───────────────────────────────────────────────────────────

def _make_icon_image(pending: int = 0):
    """Return a PIL Image for the tray icon with optional pending badge."""
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    SIZE = 64
    img  = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d    = ImageDraw.Draw(img)

    # Background circle — Janus blue
    d.ellipse([4, 4, SIZE - 4, SIZE - 4], fill=(108, 142, 245, 255))

    # Door arch (two rectangles suggesting a doorway)
    cx = SIZE // 2
    d.rectangle([cx - 9, 22, cx + 9, SIZE - 10], fill=(15, 17, 23, 255))   # door body
    d.ellipse(  [cx - 9, 18, cx + 9, 30], fill=(15, 17, 23, 255))           # arch top

    # Badge if pending > 0
    if pending > 0:
        badge_txt = str(min(pending, 99))
        bx, by = SIZE - 4, 4
        r = 11
        d.ellipse([bx - r, by - r, bx + r, by + r], fill=(248, 113, 113, 255))
        try:
            font = ImageFont.truetype("arial.ttf", 14)
        except Exception:
            font = ImageFont.load_default()
        bbox = d.textbbox((0, 0), badge_txt, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text((bx - tw // 2, by - th // 2 - 1), badge_txt, fill="white", font=font)

    return img


# ── Tray launcher ────────────────────────────────────────────────────────────

def start_tray(
    conn: sqlite3.Connection,
    port: int = 8000,
    *,
    get_pending: Callable[[], int] | None = None,
    on_pause:    Callable[[], None] | None = None,
    on_resume:   Callable[[], None] | None = None,
    on_quit:     Callable[[], None] | None = None,
) -> threading.Thread | None:
    """Start the tray icon in a daemon thread.

    Returns the Thread, or None if pystray/Pillow is unavailable.
    """
    try:
        import pystray          # noqa: PLC0415
    except ImportError:
        print("[tray] pystray not installed — running without tray icon")
        return None

    try:
        from PIL import Image   # noqa: F401, PLC0415
    except ImportError:
        print("[tray] Pillow not installed — running without tray icon")
        return None

    paused = threading.Event()          # set = paused
    dashboard_url = f"http://127.0.0.1:{port}"

    def _open_dashboard(icon, item):
        webbrowser.open(dashboard_url)

    def _pause_resume(icon, item):
        if paused.is_set():
            paused.clear()
            if on_resume: on_resume()
        else:
            paused.set()
            if on_pause: on_pause()

    def _quit(icon, item):
        icon.stop()
        if on_quit: on_quit()

    def _pause_label(item):
        return "Resume watcher" if paused.is_set() else "Pause watcher"

    def _build_menu():
        return pystray.Menu(
            pystray.MenuItem("Open Dashboard", _open_dashboard, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(_pause_label, _pause_resume),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Janus", _quit),
        )

    icon = pystray.Icon(
        "Janus",
        icon=_make_icon_image(0),
        title="Janus — file organiser",
        menu=_build_menu(),
    )

    def _badge_updater():
        """Poll pending count and redraw the icon badge every 5 s."""
        last = -1
        while True:
            try:
                n = get_pending() if get_pending else 0
                if n != last:
                    icon.icon = _make_icon_image(n)
                    icon.title = (
                        f"Janus — {n} file{'s' if n != 1 else ''} waiting"
                        if n > 0 else "Janus — all clear"
                    )
                    last = n
            except Exception:
                pass
            threading.Event().wait(5)   # sleep 5 s without blocking anything

    updater_thread = threading.Thread(target=_badge_updater, daemon=True)

    def _run():
        updater_thread.start()
        icon.run()

    t = threading.Thread(target=_run, name="janus-tray", daemon=True)
    t.start()
    print(f"[tray] icon started — right-click to open dashboard or quit")
    return t

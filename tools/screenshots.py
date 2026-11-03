#!/usr/bin/env python3
"""Make the README screenshots: a separate Kollect window filled with example batches.

Nothing is downloaded. The window keeps its queue and settings in a temporary folder, so your own queue
is not touched, and it runs beside a Kollect that is already open. It appears on screen for a few seconds.
  python3 tools/screenshots.py docs/screenshots
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gio, GLib, Graphene, Gtk  # noqa: E402

from kollect import core, ui  # noqa: E402
from kollect.runner import Controller  # noqa: E402

OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots")
TMP = tempfile.mkdtemp(prefix="kollect-shots-")
HOME_PICTURES = os.path.expanduser("~/Pictures")


def link(url, status="waiting", title="", note="", **extra):
    return dict({"id": core.new_id(), "url": url, "status": status, "note": note, "title": title,
                 "folder": TMP if status == "done" else "", "new": 0, "old": 0, "failed": 0}, **extra)


def batch(base, links, mode="full", numbered=True, state="active", start_at=None):
    return {"id": core.new_id(), "base": os.path.join(HOME_PICTURES, base), "numbered": numbered, "mode": mode,
            "if_downloaded": "skip", "state": state, "start_at": start_at, "added": time.time(), "links": links}


def example_queue():
    running = link("https://photos.example.com/albums/city-lights.html", "running", new=7, how="fullsize")
    first = batch("Kollect Downloads", [
        link("https://www.example.org/galleries/autumn-walk/", "done", "Autumn walk in the hills",
             "24 images · full size from the page"),
        running,
        link("https://www.example.net/gallery/2024/harbour/"),
    ])
    trips = batch("Trips", [link("https://www.example.org/travel/lisbon/"),
                            link("https://www.example.org/travel/porto/")],
                  mode="all", numbered=False, state="scheduled", start_at=core.next_time(23, 0))
    paused = batch("Kollect Downloads", [
        link("https://art.example.com/collections/sketches/?page=2", note="Paused after 12 images")],
        state="paused")
    return [first, trips, paused], first, running


class FakeRun:
    """Stands in for a download, so the window shows one as running."""

    def __init__(self, batch_, link_):
        self.batch, self.link = batch_, link_


class ShotApp(ui.App):
    def __init__(self):
        super().__init__()
        self.set_flags(self.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.settings = core.Settings(os.path.join(TMP, "settings.json"))
        self.settings.update(base="~/Pictures/Kollect Downloads", numbered=True, mode="full")
        self.ctl = Controller(core.Store(os.path.join(TMP, "queue.json")), self.settings, self.on_change)
        batches, first, running = example_queue()
        self.ctl.store.batches = batches
        self.ctl.run = FakeRun(first, running)

    def do_command_line(self, command_line):
        self.do_activate()
        win = self.window
        win.set_default_size(820, 900)
        win.links.get_buffer().set_text("https://www.example.org/galleries/spring-garden/\n"
                                        "https://www.example.org/galleries/spring-garden/page/2/\n"
                                        "https://photos.example.com/albums/night-market.html\n")
        win.on_change()
        browsers_dialog = ui.BrowsersDialog(win)
        plan = {"links": ["https://www.example.org/galleries/autumn-walk/",
                          "https://photos.example.com/albums/city-lights.html",
                          "https://www.example.net/gallery/2024/harbour/"],
                "base": os.path.join(HOME_PICTURES, "Kollect Downloads"), "start": "now", "start_at": None,
                "in_queue": 0}
        duplicates = [(plan["links"][0], {"folder": TMP, "title": "Autumn walk in the hills", "images": 24,
                                          "downloaded": "2026-11-02 19:50:31"}),
                      (plan["links"][1], {"folder": TMP, "title": "City lights at night", "images": 15,
                                          "downloaded": "2026-11-01 22:10:05"})]
        steps = [(1.5, lambda: shoot(win, "main.png")),
                 (0.3, lambda: win.add_menu.popup()),
                 (1.0, lambda: shoot(win, "add-menu.png", win.add_menu)),
                 (0.3, lambda: win.add_menu.popdown()),
                 (0.3, lambda: open_clock(win, 1)),
                 (1.0, lambda: shoot(win, "start-time.png", clock_popover(win, 1))),
                 (0.3, lambda: clock_buttons(win)[1].popdown()),
                 (0.3, lambda: browsers_dialog.present(win)),
                 (1.5, lambda: shoot(win, "add-to-browsers.png")),
                 (0.3, lambda: browsers_dialog.close()),
                 (0.5, lambda: win.ask_about_duplicates(dict(plan), duplicates)),
                 (1.5, lambda: shoot(win, "duplicates.png")),
                 (0.3, self.quit)]
        run_steps(steps)
        return 0


def run_steps(steps):
    if not steps:
        return
    delay, action = steps[0]

    def fire():
        action()
        run_steps(steps[1:])
        return False

    GLib.timeout_add(int(delay * 1000), fire)


def clock_buttons(win):
    found = []

    def walk(w):
        if isinstance(w, Gtk.MenuButton) and w.get_icon_name() == "alarm-symbolic":
            found.append(w)
        child = w.get_first_child()
        while child:
            walk(child)
            child = child.get_next_sibling()

    walk(win)
    return found


def open_clock(win, index):
    clock_buttons(win)[index].popup()


def clock_popover(win, index):
    return clock_buttons(win)[index].get_popover()


def origin_in_surface(widget):
    """Where a native widget (window or popover) starts, in its own surface's coordinates."""
    x, y = widget.get_surface_transform()
    return x, y


def shoot(win, name, popover=None):
    """Save the window, with an open popover drawn where it sits on screen (it may stick out of the window)."""
    width, height = win.get_width(), win.get_height()
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(win).snapshot(snapshot, width, height)
    if popover is not None and popover.get_visible():
        win_x, win_y = origin_in_surface(win)
        pop_x, pop_y = origin_in_surface(popover)
        surface = popover.get_surface()
        x = surface.get_position_x() + pop_x - win_x
        y = surface.get_position_y() + pop_y - win_y
        snapshot.save()
        snapshot.translate(Graphene.Point().init(x, y))
        Gtk.WidgetPaintable.new(popover).snapshot(snapshot, popover.get_width(), popover.get_height())
        snapshot.restore()
        width, height = max(width, int(x + popover.get_width())), max(height, int(y + popover.get_height()))
    node = snapshot.to_node()
    texture = win.get_native().get_renderer().render_texture(node, Graphene.Rect().init(0, 0, width, height))
    os.makedirs(OUT, exist_ok=True)
    texture.save_to_png(os.path.join(OUT, name))
    print("saved", name, f"{texture.get_width()}x{texture.get_height()}", flush=True)


if __name__ == "__main__":
    sys.exit(ShotApp().run([sys.argv[0]]))

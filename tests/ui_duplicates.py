"""Check the duplicate options in a real Kollect window (GTK 4's check boxes cannot be clicked over AT-SPI).

Opens a separate window with its own queue and settings in a temporary folder, ticks the boxes as a click would
(set_active fires the same "toggled" signal) and checks that "Skip it" and "Download it again" stay either-or and
are saved. Run on a desktop: python3 tests/ui_duplicates.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib  # noqa: E402

from kollect import core, ui  # noqa: E402
from kollect.runner import Controller  # noqa: E402

TMP = tempfile.mkdtemp(prefix="kollect-ui-test-")
results = []


class TestApp(ui.App):
    def __init__(self):
        super().__init__()
        self.set_flags(self.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.settings = core.Settings(os.path.join(TMP, "settings.json"))
        self.ctl = Controller(core.Store(os.path.join(TMP, "queue.json")), self.settings, self.on_change)

    def do_command_line(self, command_line):
        self.do_activate()
        GLib.timeout_add(800, self.check)
        return 0

    def check(self):
        win = self.window

        def saved():
            with open(os.path.join(TMP, "settings.json"), encoding="utf-8") as f:
                data = json.load(f)
            return data["warn_duplicates"], data["if_downloaded"]

        def expect(what, got, want):
            results.append(f"{'ok  ' if got == want else 'FAIL'} {what}: {got}")

        expect("defaults (warn, skip, again)", (win.warn.get_active(), win.skip_rule.get_active(),
                                                 win.again_rule.get_active()), (True, True, False))
        win.again_rule.set_active(True)
        expect("tick Download it again unticks Skip it", (win.skip_rule.get_active(), saved()),
               (False, (True, "overwrite")))
        win.again_rule.set_active(False)
        expect("neither ticked = add new pictures only", saved(), (True, "update"))
        win.skip_rule.set_active(True)
        win.warn.set_active(False)
        expect("Skip it again, warning off", saved(), (False, "skip"))
        expect("rule for new batches", win.duplicate_rule(), "skip")
        self.quit()
        return False


if __name__ == "__main__":
    TestApp().run([sys.argv[0]])
    print("\n".join(results))
    sys.exit(0 if results and all(r.startswith("ok") for r in results) else 1)

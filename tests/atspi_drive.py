"""Drive a running Kollect window through AT-SPI (the accessibility bus), for testing without seeing the screen.

  python3 tests/atspi_drive.py dump                       print the window's accessible tree
  python3 tests/atspi_drive.py show                       status line, batches and links as the window shows them
  python3 tests/atspi_drive.py add --folder DIR [--take full|everything] [--numbered on|off]
                                   [--mode now|paused|at] [--at HH:MM] LINK...
  python3 tests/atspi_drive.py press NAME [INDEX]          press the INDEX-th (0-based) button called NAME
  python3 tests/atspi_drive.py schedule INDEX HH:MM        give batch INDEX a start time
"""
import argparse
import sys
import time

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi  # noqa: E402


def walk(node, depth=0):
    if node is None or depth > 60:
        return
    yield node, depth
    try:
        count = node.get_child_count()
    except Exception:
        return
    for i in range(count):
        try:
            yield from walk(node.get_child_at_index(i), depth + 1)
        except Exception:
            pass


def window():
    for _ in range(40):
        desktop = Atspi.get_desktop(0)
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            for j in range(app.get_child_count() if app else 0):
                w = app.get_child_at_index(j)
                if w and w.get_role() == Atspi.Role.FRAME and w.get_name() == "Kollect":
                    return w
        time.sleep(0.5)
    sys.exit("Kollect window not found")


def nodes(role=None, name=None, showing=True):
    out = []
    for n, _ in walk(window()):
        try:
            if role is not None and n.get_role() not in (role if isinstance(role, tuple) else (role,)):
                continue
            if name is not None and n.get_name() != name:
                continue
            if showing and not n.get_state_set().contains(Atspi.StateType.SHOWING):
                continue
        except Exception:
            continue
        out.append(n)
    return out


def is_on(n):
    states = n.get_state_set()
    return states.contains(Atspi.StateType.CHECKED) or states.contains(Atspi.StateType.PRESSED)


def text_of(n):
    try:
        return Atspi.Text.get_text(n, 0, -1)
    except Exception:
        return ""


def set_text(n, text):
    try:
        if Atspi.EditableText.set_text_contents(n, text):
            return True
    except Exception:
        pass
    Atspi.EditableText.delete_text(n, 0, len(text_of(n)))
    return Atspi.EditableText.insert_text(n, 0, text, len(text.encode()))


def activate(node):
    """Click a node; a menu button carries its click on an inner toggle button."""
    for n, _ in walk(node):
        try:
            if n.get_n_actions() > 0:
                return Atspi.Action.do_action(n, 0)
        except Exception:
            pass
    raise RuntimeError(f"nothing to click in {node.get_name()!r}")


def press(name, index=0):
    found = nodes((Atspi.Role.PUSH_BUTTON, Atspi.Role.TOGGLE_BUTTON, Atspi.Role.BUTTON), name)
    if not -len(found) <= index < len(found):
        sys.exit(f"button {name!r} #{index} not found ({len(found)} showing)")
    activate(found[index])
    time.sleep(0.6)


def cmd_dump(_args):
    for n, depth in walk(window()):
        try:
            states = n.get_state_set()
            if not states.contains(Atspi.StateType.SHOWING):
                continue
            extra = text_of(n) if n.get_role() in (Atspi.Role.ENTRY, Atspi.Role.TEXT) else ""
            print("  " * depth + f"{n.get_role_name()} | {n.get_name()!r} | {n.get_description()!r} {extra!r}")
        except Exception:
            pass


def cmd_show(_args):
    labels = [n.get_name() for n in nodes(Atspi.Role.LABEL) if n.get_name()]
    for lab in labels:
        print(lab)


def cmd_add(args):
    entry = nodes((Atspi.Role.ENTRY, Atspi.Role.TEXT), "Save in")[0]
    set_text(entry, args.folder)
    links = nodes((Atspi.Role.TEXT, Atspi.Role.ENTRY), "Links")[0]
    set_text(links, "\n".join(args.links) + "\n")
    take = nodes(name={"full": "Full size", "everything": "Everything"}[args.take])[0]
    if not is_on(take):
        activate(take)
        time.sleep(0.3)
    switch = nodes(name="Number files")[0]
    want = args.numbered == "on"
    if is_on(switch) != want:
        Atspi.Action.do_action(switch, 0)
        time.sleep(0.3)
    print("links box:", text_of(links).strip().replace("\n", " | "))
    print("save in:", text_of(entry), "| full size:", is_on(nodes(name="Full size")[0]),
          "| numbered:", is_on(nodes(name="Number files")[0]))
    if args.mode == "now":
        press("Add and start")
        return
    toggles = [n for n in nodes(Atspi.Role.TOGGLE_BUTTON) if "start time" in (n.get_description() or n.get_name())]
    activate(toggles[0])
    time.sleep(0.6)
    if args.mode == "paused":
        press("Add paused")
    else:
        set_time(args.at)
        press("Add with this start time")


def set_time(hhmm):
    hour, minute = (int(x) for x in hhmm.split(":"))
    Atspi.Value.set_current_value(nodes(name="Hour")[-1], hour)
    Atspi.Value.set_current_value(nodes(name="Minute")[-1], minute)
    time.sleep(0.3)


def cmd_press(args):
    press(args.name, args.index)


def cmd_schedule(args):
    press("Start time", args.index)
    set_time(args.at)
    press("Set start time")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("dump")
    sub.add_parser("show")
    a = sub.add_parser("add")
    a.add_argument("--folder", required=True)
    a.add_argument("--numbered", choices=("on", "off"), default="off")
    a.add_argument("--take", choices=("full", "everything"), default="full")
    a.add_argument("--mode", choices=("now", "paused", "at"), default="now")
    a.add_argument("--at", default="")
    a.add_argument("links", nargs="+")
    b = sub.add_parser("press")
    b.add_argument("name")
    b.add_argument("index", type=int, nargs="?", default=0)
    s = sub.add_parser("schedule")
    s.add_argument("index", type=int)
    s.add_argument("at")
    args = p.parse_args()
    globals()["cmd_" + args.cmd](args)


if __name__ == "__main__":
    main()

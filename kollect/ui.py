"""Kollect window: paste links, pick a folder, add. Each batch has Play/Pause, a start time and its own folder."""
import os
import re
import subprocess
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from . import __version__, browsers, core  # noqa: E402
from .runner import Controller  # noqa: E402

STATUS_ICONS = {"waiting": "content-loading-symbolic", "done": "object-select-symbolic",
                "failed": "dialog-warning-symbolic", "skipped": "media-skip-forward-symbolic"}
RULE_TEXT = {"skip": "skips galleries it has", "overwrite": "replaces galleries it has",
             "update": "adds new pictures only"}


def a11y(widget, label):
    widget.update_property([Gtk.AccessibleProperty.LABEL], [label])
    return widget


def icon_button(icon, label, callback, *args):
    button = Gtk.Button(icon_name=icon, tooltip_text=label, valign=Gtk.Align.CENTER)
    button.add_css_class("flat")
    a11y(button, label)
    button.connect("clicked", lambda *_: callback(*args))
    return button


def open_folder(path):
    if path and os.path.isdir(path):
        Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(path, None), None)


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


class TimePicker(Gtk.Box):
    """'Start at HH : MM' with two spin buttons."""

    def __init__(self, start_at=None):
        super().__init__(spacing=6)
        when = time.localtime(start_at or time.time() + 3600)
        self.hour = self._spin(23, when.tm_hour, 1, "Hour")
        self.minute = self._spin(59, when.tm_min if start_at else 0, 5, "Minute")
        self.append(Gtk.Label(label="Start at"))
        self.append(self.hour)
        self.append(Gtk.Label(label=":"))
        self.append(self.minute)

    @staticmethod
    def _spin(top, value, step, label):
        spin = Gtk.SpinButton.new_with_range(0, top, step)
        spin.set_wrap(True)
        spin.set_numeric(True)
        spin.set_width_chars(2)
        spin.set_value(value)

        def two_digits(s):
            s.set_text(f"{int(s.get_value()):02d}")
            return True

        spin.connect("output", two_digits)
        return a11y(spin, label)

    def timestamp(self):
        return core.next_time(int(self.hour.get_value()), int(self.minute.get_value()))


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Kollect", default_width=780, default_height=700)
        self.ctl = app.ctl
        self.settings = app.settings
        self.collapsed = set()
        self.batch_widgets = {}
        self.link_widgets = {}
        self.shape = None
        self.last_clip = None
        self.closing = False

        # links box
        self.links = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                                  top_margin=8, bottom_margin=8, left_margin=10, right_margin=10)
        a11y(self.links, "Links")
        hint = Gtk.Label(label="Paste links here, one per line", xalign=0, yalign=0,
                         margin_top=8, margin_start=12, can_target=False)
        hint.add_css_class("dim-label")
        self.links.get_buffer().connect("changed", lambda buf: hint.set_visible(buf.get_char_count() == 0))
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_links_key)
        self.links.add_controller(keys)
        scroller = Gtk.ScrolledWindow(child=self.links, min_content_height=100, max_content_height=180,
                                      propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        overlay = Gtk.Overlay(child=scroller)
        overlay.add_overlay(hint)
        links_frame = Gtk.Frame(child=overlay)

        # where the batch is saved
        self.folder = Gtk.Entry(text=core.shorten(core.expand(self.settings["base"])), hexpand=True)
        a11y(self.folder, "Save in")
        folder_row = Gtk.Box(spacing=8)
        folder_row.append(Gtk.Label(label="Save in"))
        folder_row.append(self.folder)
        folder_row.append(icon_button("folder-open-symbolic", "Choose folder", self.choose_folder))

        # what to take, numbering and the add button
        self.full = Gtk.ToggleButton(label="Full size", active=self.settings.get("mode") != "all",
                                     tooltip_text="Only the large picture each thumbnail opens, at the largest "
                                                  "size the page offers")
        self.everything = Gtk.ToggleButton(label="Everything", group=self.full,
                                           active=self.settings.get("mode") == "all",
                                           tooltip_text="Every picture on the page, also thumbnails, logos and icons")
        take = Gtk.Box(valign=Gtk.Align.CENTER)
        take.add_css_class("linked")
        take.append(self.full)
        take.append(self.everything)
        self.number = Gtk.Switch(active=bool(self.settings["numbered"]), valign=Gtk.Align.CENTER)
        a11y(self.number, "Number files")
        number_label = Gtk.Label(label="Number files 001, 002… in page order", xalign=0, hexpand=True)
        self.add_time = TimePicker()
        add_later = Gtk.Button(label="Add paused")
        add_later.add_css_class("flat")
        add_at = Gtk.Button(label="Add with this start time")
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=6, margin_bottom=6,
                       margin_start=6, margin_end=6)
        for w in (add_later, Gtk.Separator(), self.add_time, add_at):
            menu.append(w)
        self.add_menu = Gtk.Popover(child=menu)
        self.add_button = Adw.SplitButton(label="Add and start", popover=self.add_menu,
                                          dropdown_tooltip="Add paused, or with a start time")
        self.add_button.add_css_class("suggested-action")
        self.add_button.connect("clicked", lambda *_: self.add("now"))
        add_later.connect("clicked", lambda *_: self.add("paused"))
        add_at.connect("clicked", lambda *_: self.add("at", self.add_time.timestamp()))
        options_row = Gtk.Box(spacing=10)
        options_row.append(take)
        options_row.append(self.number)
        options_row.append(number_label)
        options_row.append(self.add_button)

        # galleries that are already downloaded: ask when you add links; a fixed rule when nobody is asked
        rule = self.settings.get("if_downloaded", "skip")
        self.warn = Gtk.CheckButton(label="Warn me before downloading a gallery again",
                                    active=bool(self.settings.get("warn_duplicates", True)))
        self.skip_rule = Gtk.CheckButton(label="Skip it", active=rule == "skip")
        self.again_rule = Gtk.CheckButton(label="Download it again", active=rule == "overwrite")
        unattended = Gtk.Label(label="When I'm not asked (timed or queued):", margin_start=8)
        unattended.add_css_class("dim-label")
        tip = ("When a link's turn comes and its gallery is already in the folder: in a batch with a start time, "
               "for links added from the command line, or for a gallery downloaded after you added the link. "
               "Download it again moves the old pictures to the Trash. Neither ticked: only pictures that are new "
               "on the page since the last download are added.")
        for widget in (unattended, self.skip_rule, self.again_rule):
            widget.set_tooltip_text(tip)
        self.warn.connect("toggled", lambda *_: self.save_duplicate_choice())
        self.skip_rule.connect("toggled", self.on_rule_toggled, self.again_rule)
        self.again_rule.connect("toggled", self.on_rule_toggled, self.skip_rule)
        duplicates_row = Gtk.Box(spacing=10)
        for widget in (self.warn, unattended, self.skip_rule, self.again_rule):
            duplicates_row.append(widget)

        self.status = Gtk.Label(xalign=0, wrap=True)
        self.status.add_css_class("heading")

        # the queue
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, valign=Gtk.Align.START)
        self.list.add_css_class("boxed-list")
        empty = Gtk.Label(label="Nothing in the queue yet", margin_top=24, margin_bottom=24)
        empty.add_css_class("dim-label")
        self.list.set_placeholder(empty)
        list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=2, margin_end=2, margin_bottom=6)
        list_box.append(self.list)
        list_scroll = Gtk.ScrolledWindow(child=list_box, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)

        clear = Gtk.Button(label="Clear finished")
        clear.connect("clicked", lambda *_: self.ctl.clear_finished())
        open_all = Gtk.Button(label="Open downloads folder")
        open_all.connect("clicked", lambda *_: self.open_base())
        bottom = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        bottom.append(clear)
        bottom.append(open_all)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        for w in (links_frame, folder_row, options_row, duplicates_row, self.status, list_scroll, bottom):
            body.append(w)
        self.toasts = Adw.ToastOverlay(child=body)
        view = Adw.ToolbarView(content=self.toasts)
        header = Adw.HeaderBar()
        to_browsers = Gtk.Button(label="Add to browsers",
                                 tooltip_text="Send open tabs or highlighted links from your browsers to Kollect")
        to_browsers.connect("clicked", lambda *_: BrowsersDialog(self).present(self))
        header.pack_start(to_browsers)
        view.add_top_bar(header)
        self.set_content(view)

        self.connect("notify::is-active", self.on_active)
        self.connect("close-request", self.on_close)
        self.on_change()

    # ------------------------------------------------------------ adding

    def on_links_key(self, _ctrl, keyval, _code, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.CONTROL_MASK:
            self.add("now")
            return True
        return False

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=4))

    def add(self, start, start_at=None):
        self.add_menu.popdown()
        buf = self.links.get_buffer()
        links = core.find_links(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False))
        if not links:
            self.toast("Paste links that start with http:// or https://")
            return
        queued = self.ctl.store.queued_urls()
        fresh = [u for u in links if u not in queued]
        if not fresh:
            self.toast("Those links are already in the queue")
            return
        base = core.expand(self.folder.get_text()) or core.expand(core.DEFAULT_BASE)
        try:
            os.makedirs(base, exist_ok=True)
        except OSError as e:
            self.toast(f"Cannot use that folder: {e.strerror}")
            return
        self.folder.set_text(core.shorten(base))
        self.settings["base"] = core.shorten(base)
        self.settings["numbered"] = self.number.get_active()
        self.settings["mode"] = "all" if self.everything.get_active() else "full"
        self.settings.save()
        plan = {"links": fresh, "base": base, "start": start, "start_at": start_at,
                "in_queue": len(links) - len(fresh)}
        duplicates = [(u, g) for u in fresh if (g := core.existing_gallery(base, u))] if self.warn.get_active() else []
        if duplicates:
            self.ask_about_duplicates(plan, duplicates)
        else:
            self.finish_add(plan)

    def finish_add(self, plan, redo=(), skipped=0):
        if plan["links"]:
            self.ctl.add_batch(plan["links"], plan["base"], self.number.get_active(),
                               "all" if self.everything.get_active() else "full", plan["start"], plan["start_at"],
                               self.duplicate_rule(), redo)
        self.links.get_buffer().set_text("")
        msg = "Added " + plural(len(plan["links"]), "link")
        if plan["in_queue"]:
            msg += f" ({plan['in_queue']} already in the queue)"
        if skipped:
            msg += f" · {skipped} already downloaded {'was' if skipped == 1 else 'were'} skipped"
        if redo:
            msg += f" · {len(redo)} will be downloaded again"
        if plan["links"] and plan["start"] == "paused":
            msg += ", paused"
        elif plan["links"] and plan["start"] == "at":
            msg += ", starts " + core.describe_time(plan["start_at"])
        if not core.find_gallery_dl(self.settings):
            msg = "gallery-dl is not installed. Run: pipx install gallery-dl"
        self.toast(msg)

    def ask_about_duplicates(self, plan, duplicates):
        total, found = len(plan["links"]), len(duplicates)
        heading = (f"{found} of {total} galleries are already downloaded" if total > 1
                   else "This gallery is already downloaded")
        others = total - found
        body = f"They are in {core.shorten(plan['base'])}." if found > 1 else f"It is in {core.shorten(plan['base'])}."
        if others:
            body += f" The other {plural(others, 'link')} {'is' if others == 1 else 'are'} added either way."
        body += "\n\nOverwrite all moves the old pictures to the Trash, then downloads the galleries again."
        dialog = Adw.AlertDialog(heading=heading, body=body, prefer_wide_layout=True)
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        rows.add_css_class("boxed-list")
        for url, gallery in duplicates:
            address = re.sub(r"^https?://(www\.)?", "", core.split_prefix(url)[1])
            details = [address, plural(gallery["images"], "image"), core.describe_date(gallery["downloaded"])]
            row = Adw.ActionRow(title=gallery["title"] or address, subtitle=" · ".join(d for d in details if d),
                                use_markup=False, title_lines=1, subtitle_lines=2)
            row.add_suffix(icon_button("folder-open-symbolic", "Open its folder", open_folder, gallery["folder"]))
            rows.append(row)
        dialog.set_extra_child(Gtk.ScrolledWindow(child=rows, propagate_natural_height=True, max_content_height=260,
                                                  hscrollbar_policy=Gtk.PolicyType.NEVER))
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("overwrite", "Overwrite all")
        dialog.add_response("skip", "Skip all")
        dialog.set_response_appearance("overwrite", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("skip", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("skip")
        dialog.set_close_response("cancel")
        dialog.connect("response", self.on_duplicates_answer, plan, {u for u, _ in duplicates})
        dialog.present(self)

    def on_duplicates_answer(self, _dialog, response, plan, duplicate_urls):
        if response == "skip":
            plan["links"] = [u for u in plan["links"] if u not in duplicate_urls]
            self.finish_add(plan, skipped=len(duplicate_urls))
        elif response == "overwrite":
            self.finish_add(plan, redo=duplicate_urls)
        else:
            self.toast("Nothing added")

    def duplicate_rule(self):
        return "skip" if self.skip_rule.get_active() else "overwrite" if self.again_rule.get_active() else "update"

    def on_rule_toggled(self, button, other):
        if button.get_active() and other.get_active():
            other.set_active(False)  # the two are either-or; both off means add new pictures only
        self.save_duplicate_choice()

    def save_duplicate_choice(self):
        self.settings["warn_duplicates"] = self.warn.get_active()
        self.settings["if_downloaded"] = self.duplicate_rule()
        self.settings.save()

    def on_active(self, *_):
        if self.is_active() and not self.links.get_buffer().get_char_count():
            Gdk.Display.get_default().get_clipboard().read_text_async(None, self.on_clipboard)

    def on_clipboard(self, clipboard, result):
        try:
            text = clipboard.read_text_finish(result) or ""
        except GLib.Error:
            return
        if text == self.last_clip:
            return
        self.last_clip = text
        queued = self.ctl.store.queued_urls()
        links = [u for u in core.find_links(text) if u not in queued]
        buf = self.links.get_buffer()
        if links and not buf.get_char_count():
            buf.set_text("\n".join(links) + "\n")

    def choose_folder(self):
        dialog = Gtk.FileDialog(title="Save in", modal=True)
        current = core.expand(self.folder.get_text())
        if os.path.isdir(current):
            dialog.set_initial_folder(Gio.File.new_for_path(current))
        dialog.select_folder(self, None, self.on_folder_chosen)

    def on_folder_chosen(self, dialog, result):
        try:
            chosen = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if chosen and chosen.get_path():
            self.folder.set_text(core.shorten(chosen.get_path()))

    def open_base(self):
        base = core.expand(self.folder.get_text()) or core.expand(core.DEFAULT_BASE)
        os.makedirs(base, exist_ok=True)
        open_folder(base)

    def paste_links(self, links):
        """Links sent by the browser add-on: added to the links box, below what is already there."""
        buf = self.links.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        have = set(core.find_links(text))
        fresh = [u for u in links if u not in have]
        if fresh:
            if text and not text.endswith("\n"):
                buf.insert(buf.get_end_iter(), "\n")
            buf.insert(buf.get_end_iter(), "\n".join(fresh) + "\n")
        self.last_clip = "\n".join(links) + "\n"  # the add-on copied the same text; do not fill it in twice
        self.present()
        self.toast(f"{plural(len(fresh), 'link')} from the browser" if fresh else "Those links are already in the box")

    # ------------------------------------------------------------ the queue list

    def on_change(self, batch_id=None, link_id=None):
        batches = self.ctl.store.batches
        shape = [(b["id"], [x["id"] for x in b["links"]]) for b in batches]
        if batch_id is None and shape != self.shape:
            self.rebuild(batches)
            self.shape = shape
        for b in batches:
            if batch_id is None or b["id"] == batch_id:
                self.update_batch(b)
                for link in b["links"]:
                    if link_id is None or link["id"] == link_id:
                        self.update_link(link)
        self.status.set_label(self.overall())

    def rebuild(self, batches):
        self.list.remove_all()
        self.batch_widgets.clear()
        self.link_widgets.clear()
        for b in batches:
            bid = b["id"]
            exp = Adw.ExpanderRow(use_markup=False, title_lines=1, subtitle_lines=2,
                                  expanded=bid not in self.collapsed)
            exp.connect("notify::expanded", self.on_expanded, bid)
            play = Gtk.Button(valign=Gtk.Align.CENTER)
            play.add_css_class("flat")
            play.connect("clicked", self.on_play, bid)
            when = Gtk.MenuButton(icon_name="alarm-symbolic", tooltip_text="Start time", valign=Gtk.Align.CENTER)
            when.add_css_class("flat")
            a11y(when, "Start time")
            when.set_create_popup_func(self.fill_time_menu, bid)
            for w in (play, when, icon_button("folder-open-symbolic", "Open folder", self.open_batch, bid),
                      icon_button("user-trash-symbolic", "Remove batch", self.ctl.remove_batch, bid)):
                exp.add_suffix(w)
            self.batch_widgets[bid] = (exp, play)
            for link in b["links"]:
                row = Adw.ActionRow(use_markup=False, title_lines=1, subtitle_lines=2)
                holder = Gtk.Box(valign=Gtk.Align.CENTER)
                row.add_prefix(holder)
                folder = icon_button("folder-open-symbolic", "Open gallery folder", self.open_link, link)
                row.add_suffix(folder)
                row.add_suffix(icon_button("window-close-symbolic", "Remove link", self.ctl.remove_link,
                                           bid, link["id"]))
                exp.add_row(row)
                self.link_widgets[link["id"]] = (row, holder, folder, [None])
            self.list.append(exp)

    def on_expanded(self, exp, _pspec, bid):
        (self.collapsed.discard if exp.get_expanded() else self.collapsed.add)(bid)

    def update_batch(self, b):
        widgets = self.batch_widgets.get(b["id"])
        if not widgets:
            return
        exp, play = widgets
        run = self.ctl.run
        running = run is not None and run.batch is b
        links = b["links"]
        done = sum(x["status"] == "done" for x in links)
        failed = sum(x["status"] == "failed" for x in links)
        waiting = sum(x["status"] == "waiting" for x in links)
        skipped = sum(x["status"] == "skipped" for x in links)
        parts = [plural(len(links), "link")]
        if skipped:
            parts.append(f"{skipped} skipped")
        if done:
            parts.append(f"{done} done")
        if failed:
            parts.append(f"{failed} failed")
        parts.append("everything" if b.get("mode", "all") == "all" else "full size")
        if b["numbered"]:
            parts.append("numbered")
        parts.append(RULE_TEXT[b.get("if_downloaded", "update")])
        if running:
            state = "Downloading"
        elif b["state"] == "scheduled":
            state = f"Starts {core.describe_time(b['start_at'])}"
        elif b["state"] == "paused":
            state = "Paused" if waiting or failed else "Finished"
        elif waiting:
            state = "Waiting for its turn"
        else:
            state = "Finished"
        exp.set_title(core.shorten(b["base"]))
        exp.set_subtitle(" · ".join(parts) + " — " + state)
        pause = running or (b["state"] == "active" and waiting > 0)
        label = "Pause" if pause else "Start now" if b["state"] == "scheduled" else "Start"
        play.set_icon_name("media-playback-pause-symbolic" if pause else "media-playback-start-symbolic")
        play.set_tooltip_text(label)
        a11y(play, label)
        play.set_sensitive(pause or waiting > 0 or failed > 0)

    def update_link(self, link):
        widgets = self.link_widgets.get(link["id"])
        if not widgets:
            return
        row, holder, folder, shown = widgets
        row.set_title(link["title"] or link["url"])
        row.set_tooltip_text(link["url"])
        row.set_subtitle(self.link_text(link))
        if shown[0] != link["status"]:
            shown[0] = link["status"]
            if holder.get_first_child():
                holder.remove(holder.get_first_child())
            if link["status"] == "running":
                holder.append(Adw.Spinner())
            else:
                holder.append(Gtk.Image(icon_name=STATUS_ICONS.get(link["status"], "content-loading-symbolic")))
        folder.set_sensitive(bool(link["folder"]) and os.path.isdir(link["folder"]))

    @staticmethod
    def link_text(link):
        status, note = link["status"], link["note"]
        if status == "running":
            got = link["new"] + link["old"]
            if not got:
                return note or "Starting…"
            text = f"Downloading… {link['new']} new"
            if link["old"]:
                text += f", {link['old']} already here"
            if link["failed"]:
                text += f", {link['failed']} failed"
            return text + {"generic": " · everything on the page", "fullsize": " · full size"}.get(link.get("how"), "")
        if status == "done":
            return "Done: " + note
        if status == "failed":
            return "Not downloaded: " + note
        if status == "skipped":
            return "Skipped: " + note
        return note or "Waiting"

    def overall(self):
        batches = self.ctl.store.batches
        run = self.ctl.run
        if run:
            return "Downloading " + (run.link["title"] or run.link["url"])
        if not batches:
            return "Paste links above, then press Add and start"
        scheduled = [b["start_at"] for b in batches if b["state"] == "scheduled" and b.get("start_at")]
        if scheduled:
            return f"Next start {core.describe_time(min(scheduled))}. Kollect opens by itself at that time."
        if any(b["state"] == "paused" and any(x["status"] == "waiting" for x in b["links"]) for b in batches):
            return "Paused"
        return "All done"

    def fill_time_menu(self, button, bid):
        b = self.ctl.store.batch(bid)
        if not b:
            return
        picker = TimePicker(b.get("start_at"))
        set_time = Gtk.Button(label="Set start time")
        set_time.add_css_class("suggested-action")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=6, margin_bottom=6,
                      margin_start=6, margin_end=6)
        box.append(picker)
        box.append(set_time)
        if b["state"] == "scheduled":
            clear = Gtk.Button(label="Remove start time")
            clear.connect("clicked", lambda *_: (button.popdown(), self.ctl.schedule(bid, None)))
            box.append(clear)
        set_time.connect("clicked", lambda *_: (button.popdown(), self.ctl.schedule(bid, picker.timestamp())))
        button.set_popover(Gtk.Popover(child=box))

    def on_play(self, button, bid):
        b = self.ctl.store.batch(bid)
        if not b:
            return
        run = self.ctl.run
        waiting = any(x["status"] == "waiting" for x in b["links"])
        if (run is not None and run.batch is b) or (b["state"] == "active" and waiting):
            self.ctl.pause(bid)
        else:
            self.ctl.play(bid)

    def open_batch(self, bid):
        b = self.ctl.store.batch(bid)
        if b:
            open_folder(b["base"])

    @staticmethod
    def open_link(link):
        open_folder(link["folder"])

    # ------------------------------------------------------------ closing

    def on_close(self, *_):
        if self.closing or not self.ctl.busy:
            return False
        dialog = Adw.AlertDialog(heading="Stop downloading and close?",
                                 body="The batch is paused and stays in the queue. Press Start next time to continue where it stopped.")
        dialog.add_response("cancel", "Keep downloading")
        dialog.add_response("close", "Stop and close")
        dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self.on_close_response)
        dialog.present(self)
        return True

    def on_close_response(self, _dialog, response):
        if response == "close":
            self.ctl.shutdown()
            self.closing = True
            self.close()


class BrowsersDialog(Adw.Dialog):
    """Add to browsers: short steps for each browser on this computer, with buttons for the parts Kollect can do.
    Chrome-family browsers do not open their add-on page for other programs, so its address is copied instead."""

    CHROMIUM_STEPS = ("1. Press Open {name}: {name} opens and its add-on page's address is copied. "
                      "In {name}, press Ctrl+L, Ctrl+V and Enter.\n"
                      "2. Turn on Developer mode.\n"
                      "3. Press Copy add-on folder here, then Load unpacked in {name}, then Ctrl+L, Ctrl+V and Enter.\n"
                      "4. Open the add-on's Details and turn on {private}.\n"
                      "This is done once; the add-on stays.")
    FIREFOX_STEPS = ("1. Press Open add-on test page: Firefox opens it and the add-on file's address is copied.\n"
                     "2. Press Load Temporary Add-on…, then Ctrl+L, Ctrl+V and Enter.\n"
                     "3. For private windows: press Open add-ons manager, choose Kollect, and set Run in Private "
                     "Windows to Allow.\n"
                     "The first time you send links, your system may ask whether Firefox may start Kollect's "
                     "helper: choose Allow. Firefox removes temporary add-ons when it closes, so do steps 1 to 3 "
                     "again after each restart.")
    PRIVATE = {"Brave": "Allow in Private", "Microsoft Edge": "Allow in InPrivate"}

    def __init__(self, window):
        super().__init__(title="Add to browsers", content_width=600, content_height=680)
        page = Adw.PreferencesPage()
        try:
            ready = browsers.register()
        except OSError as e:
            ready = []
            page.add(Adw.PreferencesGroup(description=f"The connection to Kollect could not be set up: {e.strerror}"))
        intro = ("The add-on's button sends the addresses of your open tabs (this window or all windows) to "
                 "Kollect's links box. Right-click on highlighted text and choose Send selected links to Kollect "
                 "to send the links in it. Both are also copied to the clipboard.")
        if ready:
            intro += "\n\nKollect's connection is ready for " + ", ".join(ready) + "."
        page.add(Adw.PreferencesGroup(description=intro))
        for browser in browsers.found():
            if browser.kind == "firefox":
                steps = self.FIREFOX_STEPS
                buttons = [("Open add-on test page", lambda b=browser: self.launch(b, b.page, browsers.what_to_load(b))),
                           ("Open add-ons manager", lambda b=browser: self.launch(b, "about:addons"))]
            else:
                steps = self.CHROMIUM_STEPS.format(name=browser.name,
                                                   private=self.PRIVATE.get(browser.name, "Allow in Incognito"))
                buttons = [(f"Open {browser.name}", lambda b=browser: self.launch(b, None, b.page)),
                           ("Copy add-on folder", lambda b=browser: self.copy(browsers.what_to_load(b)))]
            group = Adw.PreferencesGroup(title=browser.name, description=steps)
            row = Gtk.Box(spacing=8, margin_top=6)
            for i, (label, action) in enumerate(buttons):
                button = Gtk.Button(label=label)
                if i == 0:
                    button.add_css_class("suggested-action")
                button.connect("clicked", lambda _b, act=action: act())
                row.append(button)
            group.add(row)
            page.add(group)
        if not browsers.found():
            page.add(Adw.PreferencesGroup(description="No supported browser was found on this computer."))
        self.toasts = Adw.ToastOverlay(child=page)
        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(Adw.HeaderBar())
        self.set_child(view)

    def copy(self, text):
        value = GObject.Value(GObject.TYPE_STRING, text)
        self.get_clipboard().set_content(Gdk.ContentProvider.new_for_value(value))
        self.toasts.add_toast(Adw.Toast(title=f"Copied: {text}", timeout=5))

    def launch(self, browser, url, copy_text=None):
        """Open the browser (at url when it accepts one from other programs) and copy what the next step pastes."""
        try:
            subprocess.Popen([browser.command] + ([url] if url else []), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except (OSError, TypeError) as e:
            self.toasts.add_toast(Adw.Toast(title=f"{browser.name} did not open: {e}", timeout=5))
            return
        if copy_text:
            self.copy(copy_text)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=core.APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None
        self.ctl = None
        self.settings = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.settings = core.Settings()
        self.ctl = Controller(core.Store().load(), self.settings, self.on_change)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.window and self.window.close())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def do_command_line(self, command_line):
        args = command_line.get_arguments()[1:]
        if "--version" in args:
            command_line.print_literal(f"Kollect {__version__}\n")
            return 0
        self.do_activate()
        links = core.find_links(" ".join(a for a in args if not a.startswith("--")))
        if "--paste" in args:  # from the browser add-on: into the links box, not straight into the queue
            self.window.paste_links(links)
            return 0
        fresh = [u for u in links if u not in self.ctl.store.queued_urls()]
        if fresh:
            base = core.expand(self.settings["base"]) or core.expand(core.DEFAULT_BASE)
            # nobody is asked here: galleries already downloaded follow the "When I'm not asked" choice
            self.ctl.add_batch(fresh, base, bool(self.settings["numbered"]), self.settings.get("mode", "full"),
                               if_downloaded=self.settings.get("if_downloaded", "skip"))
        if "--start-due" in args:
            self.ctl.start_due()
        return 0

    def do_activate(self):
        if not self.window:
            self.window = Window(self)
            self.window.connect("destroy", self.on_window_gone)
        self.window.present()

    def on_window_gone(self, *_):
        self.window = None

    def on_change(self, batch_id=None, link_id=None):
        if self.window:
            self.window.on_change(batch_id, link_id)


def main(argv):
    return App().run(argv)

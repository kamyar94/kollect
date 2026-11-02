"""Runs the queue: one gallery-dl at a time, batches in the order they were added, start times.

Needs GLib/Gio only, so it keeps working while no window is open.
"""
import os
import shutil
import signal
import subprocess
import time

from gi.repository import Gio, GLib

from . import core

LOG_LIMIT = 1_000_000


class Run:
    def __init__(self, batch, link, folder, generic):
        self.batch, self.link, self.folder, self.generic = batch, link, folder, generic
        self.proc = None
        self.index = 0
        self.files = []
        self.errors = []
        self.stop_reason = None


class Controller:
    def __init__(self, store, settings, notify):
        self.store = store
        self.settings = settings
        self.notify = notify  # notify(batch_id=None, link_id=None): None means "the list changed"
        self.run = None
        now = time.time()
        for b in store.batches:
            if b["state"] == "active" and any(x["status"] == "waiting" for x in b["links"]):
                b["state"] = "paused"  # nothing starts by itself when Kollect opens, except start times
            if b["state"] == "scheduled":
                if (b.get("start_at") or 0) <= now:
                    self._disarm(b)
                    b["state"], b["start_at"] = "active", None
                else:
                    self._arm(b)
        store.save()
        GLib.timeout_add_seconds(15, self._tick)
        GLib.idle_add(self._next_once)  # a start time that passed while Kollect was closed

    # ------------------------------------------------------------ commands

    def add_batch(self, links, base, numbered, start="now", start_at=None):
        b = core.new_batch(links, base, numbered)
        if start == "paused":
            b["state"] = "paused"
        elif start == "at":
            b["state"], b["start_at"] = "scheduled", start_at
            self._arm(b)
        self.store.batches.append(b)
        self._changed()
        self._next()
        return b

    def play(self, batch_id):
        b = self.store.batch(batch_id)
        if not b:
            return
        self._disarm(b)
        b["state"], b["start_at"] = "active", None
        for link in b["links"]:
            if link["status"] == "failed":
                link["status"], link["note"] = "waiting", ""
        self._changed()
        self._next()

    def pause(self, batch_id):
        b = self.store.batch(batch_id)
        if not b:
            return
        self._disarm(b)
        b["state"], b["start_at"] = "paused", None
        self._stop_if_in(b, "pause")
        self._changed()

    def schedule(self, batch_id, start_at):
        b = self.store.batch(batch_id)
        if not b:
            return
        self._disarm(b)
        if start_at:
            b["state"], b["start_at"] = "scheduled", start_at
            self._arm(b)
        else:
            b["state"], b["start_at"] = "paused", None
        self._stop_if_in(b, "pause")
        self._changed()

    def remove_batch(self, batch_id):
        b = self.store.batch(batch_id)
        if not b:
            return
        self._disarm(b)
        if self.run and self.run.batch is b:
            self._stop("remove-batch")
        else:
            self.store.batches.remove(b)
        self._changed()

    def remove_link(self, batch_id, link_id):
        b = self.store.batch(batch_id)
        link = next((x for x in b["links"] if x["id"] == link_id), None) if b else None
        if not link:
            return
        if self.run and self.run.link is link:
            self._stop("remove-link")
        else:
            b["links"].remove(link)
            if not b["links"]:
                self._disarm(b)
                self.store.batches.remove(b)
        self._changed()

    def clear_finished(self):
        for b in list(self.store.batches):
            b["links"] = [x for x in b["links"] if x["status"] in ("waiting", "running")]
            if not b["links"]:
                self._disarm(b)
                self.store.batches.remove(b)
        self._changed()

    def start_due(self):
        due = self.store.due()
        for b in due:
            self._disarm(b)
            b["state"], b["start_at"] = "active", None
        if due:
            self._changed()
        self._next()

    def shutdown(self):
        """Window closed: stop the download; the link waits in the queue for next time."""
        if self.run:
            self.run.link["status"] = "waiting"
            self.run.link["note"] = self._paused_note(self.run)
            self._stop("quit")
        self.store.save()

    @property
    def busy(self):
        return self.run is not None

    # ------------------------------------------------------------ running

    def _changed(self):
        self.store.save()
        self.notify()

    def _tick(self):
        if self.store.due():
            self.start_due()
        return True

    def _next(self):
        if self.run:
            return
        pick = self.store.next_link()
        if pick:
            self._start(*pick)
        else:
            self.notify()

    def _start(self, batch, link, generic=None):
        generic = link.get("generic", False) if generic is None else generic
        gallery_dl = core.find_gallery_dl(self.settings)
        base = core.expand(batch["base"]) or core.expand(core.DEFAULT_BASE)
        folder = core.gallery_folder(base, link["url"])
        link.update(status="running", folder=folder, generic=generic, new=0, old=0, failed=0,
                    note="Taking every image on the page" if generic else "")
        if not gallery_dl:
            return self._fail(link, "gallery-dl is not installed. Run: pipx install gallery-dl")
        try:
            os.makedirs(os.path.join(folder, core.WORK_DIR, "incoming"), exist_ok=True)
        except OSError as e:
            return self._fail(link, f"Cannot make the folder: {e.strerror}")
        run = self.run = Run(batch, link, folder, generic)
        args = core.gallery_dl_args(gallery_dl, folder, link["url"], generic, self.settings.get("extra_args") or [])
        self._log(f"=== start {link['url']} -> {folder}\n    {' '.join(args)}")
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        launcher.setenv("PYTHONUNBUFFERED", "1", True)
        launcher.setenv("PYTHONIOENCODING", "utf-8", True)
        try:
            run.proc = launcher.spawnv(args)
        except GLib.Error as e:
            self.run = None
            return self._fail(link, f"gallery-dl did not start: {e.message}")
        stream = Gio.DataInputStream.new(run.proc.get_stdout_pipe())
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line, run)
        self._changed()

    def _fail(self, link, note):
        link["status"], link["note"] = "failed", note
        self._changed()
        GLib.idle_add(self._next_once)

    def _next_once(self):
        self._next()
        return False

    def _on_line(self, stream, result, run):
        try:
            line, _ = stream.read_line_finish_utf8(result)
        except GLib.Error as e:
            if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.INVALID_DATA):  # one line that is not UTF-8
                stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line, run)
                return
            line = None
        if line is None:  # end of output
            run.proc.wait_async(None, self._on_exit, run)
            return
        self._log(line)
        kind, text = core.parse_line(line)
        link = run.link
        if kind in ("new", "old"):
            run.index += 1
            try:
                if kind == "new":
                    name = core.place_new(text, run.folder, run.index, run.batch["numbered"])
                    link["new"] += 1
                else:
                    # None: the site lists the same picture twice, or it was deleted after an earlier download
                    name = core.place_old(text, run.folder, run.index, run.batch["numbered"])
                    if name:
                        link["old"] += 1
            except OSError as e:
                name = None
                run.errors.append(f"moving {os.path.basename(text)}: {e.strerror}")
            if name:
                run.files.append({"n": run.index, "file": name, "original": os.path.basename(text)})
                self.notify(run.batch["id"], link["id"])
        elif kind == "error":
            run.errors.append(text)
            if text.startswith("download:"):
                link["failed"] += 1
                self.notify(run.batch["id"], link["id"])
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line, run)

    def _on_exit(self, proc, result, run):
        try:
            proc.wait_finish(result)
        except GLib.Error:
            pass
        code = proc.get_exit_status() if proc.get_if_exited() else -1
        self._log(f"=== exit {code}")
        if self.run is run:
            self.run = None
        b, link = run.batch, run.link
        if run.stop_reason:
            if run.stop_reason == "remove-link" and link in b["links"]:
                b["links"].remove(link)
            if run.stop_reason == "remove-batch" or (run.stop_reason == "remove-link" and not b["links"]):
                if b in self.store.batches:
                    self.store.batches.remove(b)
            if run.stop_reason in ("pause", "quit"):
                link["status"], link["note"] = "waiting", self._paused_note(run)
            self.store.save()
            if run.stop_reason != "quit":
                self.notify()
                self._next()
            return

        total = link["new"] + link["old"]
        unknown = code >= 0 and code & 64 or any("Unsupported URL" in e or "No suitable extractor" in e
                                                  for e in run.errors)
        if total == 0 and unknown and not run.generic:
            self._log("=== site not supported by gallery-dl: retrying with generic:")
            self._start(b, link, generic=True)
            return

        base = core.expand(b["base"]) or core.expand(core.DEFAULT_BASE)
        meta = core.take_info_json(run.folder)
        core.tidy(run.folder, base)
        if total:
            try:
                info = core.write_gallery_info(run.folder, link["url"], meta, run.files, b["numbered"], run.generic)
                core.update_library(base, info)
                link["title"] = info["title"]
                extra = ""
            except OSError as e:
                extra = f" (info file not saved: {e.strerror})"
            note = f"{total} image{'s' if total != 1 else ''}"
            if link["old"]:
                note += f", {link['new']} new"
            if link["failed"]:
                note += f", {link['failed']} failed"
            if run.generic:
                note += " · whole-page grab"
            link["status"], link["note"] = "done", note + extra
        else:
            link["status"], link["note"] = "failed", self._why_nothing(code, run)
        link["finished"] = time.time()
        self._changed()
        self._next()

    @staticmethod
    def _paused_note(run):
        n = run.link["new"] + run.link["old"]
        return f"Paused after {n} image{'s' if n != 1 else ''}" if n else "Paused"

    @staticmethod
    def _why_nothing(code, run):
        text = " ".join(run.errors).lower()
        if run.generic and not run.errors:
            return "No images found on this page"
        if run.generic and ("unsupported url" in text or "no suitable extractor" in text):
            return "No images found on this page"
        if code > 0 and code & 16 or any(w in text for w in ("login", "cookies", " 401", " 403", "authoriz")):
            return "This site wants you signed in (see README: cookies)"
        if code > 0 and code & 8 or " 404" in text or "not found" in text:
            return "Page not found"
        if run.errors:
            return run.errors[0].split(": ", 1)[-1][:160]
        return "Nothing downloaded"

    def _stop_if_in(self, batch, reason):
        if self.run and self.run.batch is batch:
            self._stop(reason)

    def _stop(self, reason):
        run = self.run
        if not run or not run.proc:
            return
        run.stop_reason = reason
        run.proc.send_signal(signal.SIGINT)

        def force():
            if run.proc.get_identifier() is not None:  # still running
                run.proc.force_exit()
            return False

        GLib.timeout_add_seconds(6, force)

    # ------------------------------------------------------------ start-time timers

    def _arm(self, b):
        """A systemd user timer opens Kollect at the start time, so the batch starts even if Kollect is closed."""
        self._disarm(b)
        cmd = core.self_command()
        if not cmd or not shutil.which("systemd-run") or not b.get("start_at"):
            return
        unit = f"kollect-start-{b['id']}-{int(b['start_at'])}"
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(b["start_at"]))
        try:
            subprocess.run(["systemd-run", "--user", "--quiet", "--collect", f"--unit={unit}",
                            f"--on-calendar={when}", "--timer-property=AccuracySec=1s", "--", *cmd, "--start-due"],
                           check=True, capture_output=True, timeout=10)
            b["timer"] = unit
        except (OSError, subprocess.SubprocessError):
            pass

    @staticmethod
    def _disarm(b):
        unit = b.pop("timer", None)
        if unit:
            try:
                subprocess.run(["systemctl", "--user", "stop", unit + ".timer"], capture_output=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                pass

    # ------------------------------------------------------------ log

    def _log(self, line):
        path = core.log_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if os.path.exists(path) and os.path.getsize(path) > LOG_LIMIT:
                os.replace(path, path + ".1")
            with open(path, "a", encoding="utf-8") as f:
                f.write(time.strftime("%H:%M:%S ") + line.rstrip("\n") + "\n")
        except OSError:
            pass

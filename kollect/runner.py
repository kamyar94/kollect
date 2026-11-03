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


STAGE_NOTES = {"site": "", "generic": "Taking every picture on the page", "find": "Looking for the full-size pictures…",
               "fullsize": "Downloading the full-size pictures…"}
STAGE_DONE = {"generic": " · everything on the page", "fullsize": " · full size from the page"}


class Run:
    def __init__(self, batch, link, folder, stage):
        self.batch, self.link, self.folder, self.stage = batch, link, folder, stage
        self.found = None
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

    def add_batch(self, links, base, numbered, mode="full", start="now", start_at=None, if_downloaded="skip",
                  redo=()):
        """redo: links whose old gallery you chose to overwrite when you added them."""
        b = core.new_batch(links, base, numbered, mode, if_downloaded)
        for link in b["links"]:
            if link["url"] in redo:
                link["redo"] = True
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
            self._begin(*pick)
        else:
            self.notify()

    def _begin(self, batch, link):
        """A link's turn: if its gallery is already downloaded, do what was chosen for it, without asking."""
        base = core.expand(batch["base"]) or core.expand(core.DEFAULT_BASE)
        existing = core.existing_gallery(base, link["url"])
        rule = batch.get("if_downloaded", "update")  # batches from before this option added new pictures only
        if existing and (link.get("redo") or rule == "overwrite"):
            link["replaced"] = self._move_old(existing["folder"])
        elif existing and rule == "skip":
            when = core.describe_date(existing["downloaded"])
            link.update(status="skipped", folder=existing["folder"], title=link["title"] or existing["title"],
                        note=f"already downloaded{' on ' + when if when else ''}, "
                             f"{existing['images']} image{'s' if existing['images'] != 1 else ''}",
                        finished=time.time())
            self._log(f"=== skip {link['url']}: already in {existing['folder']}")
            self._changed()
            GLib.idle_add(self._next_once)
            return
        link.pop("redo", None)  # once replaced, a pause and resume must not replace the new pictures
        self._start(batch, link)

    def _move_old(self, folder):
        """Move the old gallery to the Trash (into a dated subfolder where there is no Trash) and forget it."""
        paths = core.replaced_files(folder)
        stamp = time.strftime("%Y-%m-%d %H-%M")
        where = "the Trash"
        for path in paths:
            try:
                Gio.File.new_for_path(path).trash(None)
            except GLib.Error:
                keep = os.path.join(folder, f"Replaced {stamp}")
                try:
                    os.makedirs(keep, exist_ok=True)
                    os.replace(path, os.path.join(keep, os.path.basename(path)))
                    where = f"“Replaced {stamp}”"
                except OSError as e:
                    self._log(f"=== could not move {path} out of the way: {e.strerror}")
        core.forget_downloads(folder)
        self._log(f"=== replaced {len(paths)} files in {folder}, moved to {where}")
        pictures = len([p for p in paths if os.path.basename(p) != core.INFO_FILE])
        return f"the old {pictures} picture{'s' if pictures != 1 else ''} are in {where}" if pictures else ""

    def _start(self, batch, link, stage="site", found=None):
        """stage "site": gallery-dl's own extractor. For a site it does not know, "find" (finder.py reads the
        page) then "fullsize" (gallery-dl downloads that list) in full-size mode, or "generic" in everything mode."""
        if stage == "site" and core.split_prefix(link["url"])[0]:
            stage = "generic"  # the link itself asks for every picture on the page
        gallery_dl = core.find_gallery_dl(self.settings)
        base = core.expand(batch["base"]) or core.expand(core.DEFAULT_BASE)
        folder = core.gallery_folder(base, link["url"])
        link.update(status="running", folder=folder, how=stage, new=0, old=0, failed=0, note=STAGE_NOTES[stage])
        if stage != "find" and not gallery_dl:
            return self._fail(link, "gallery-dl is not installed. Run: pipx install gallery-dl")
        try:
            os.makedirs(os.path.join(folder, core.WORK_DIR, "incoming"), exist_ok=True)
        except OSError as e:
            return self._fail(link, f"Cannot make the folder: {e.strerror}")
        run = self.run = Run(batch, link, folder, stage)
        run.found = found
        if stage == "find":
            args = core.finder_args(folder, link["url"])
        else:
            args = core.gallery_dl_args(gallery_dl, folder, link["url"], stage, self.settings.get("extra_args") or [])
        self._log(f"=== start {stage} {link['url']} -> {folder}\n    {' '.join(args)}")
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        launcher.setenv("PYTHONUNBUFFERED", "1", True)
        launcher.setenv("PYTHONIOENCODING", "utf-8", True)
        try:
            run.proc = launcher.spawnv(args)
        except GLib.Error as e:
            self.run = None
            return self._fail(link, f"{os.path.basename(args[0])} did not start: {e.message}")
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
        link = run.link
        if run.stage == "find":
            word, _, rest = line.partition(" ")
            if word == "progress":
                done, _, total = rest.partition(" ")
                link["note"] = f"Looking for the full-size pictures… photo page {done} of {total}"
                self.notify(run.batch["id"], link["id"])
            elif word == "error":
                run.errors.append(rest)
            stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line, run)
            return
        kind, text = core.parse_line(line)
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

        base = core.expand(b["base"]) or core.expand(core.DEFAULT_BASE)
        if not os.path.isdir(run.folder):
            return self._finish(link, "failed", "The gallery folder was deleted while downloading")
        if run.stage == "find":
            found = core.take_found(run.folder) if code == 0 else None
            if found and found.get("pictures"):
                self._log(f"=== {len(found['pictures'])} full-size pictures found ({found.get('how')})")
                self._start(b, link, "fullsize", found)
                return
            core.tidy(run.folder, base)
            return self._finish(link, "failed", run.errors[0] if run.errors else
                                "No full-size gallery pictures found on this page. "
                                "Add it again with Everything to take every picture on it.")

        total = link["new"] + link["old"]
        unknown = code >= 0 and code & 64 or any("Unsupported URL" in e or "No suitable extractor" in e
                                                  for e in run.errors)
        if total == 0 and unknown and run.stage == "site":
            full = b.get("mode", "all") == "full"
            self._log("=== site not supported by gallery-dl: " +
                      ("looking for the full-size pictures" if full else "retrying with generic:"))
            self._start(b, link, "find" if full else "generic")
            return

        if run.stage == "fullsize":
            meta = {k: v for k, v in (run.found or {}).items() if k != "pictures"}
            core.drop_list(run.folder)
        else:
            meta = core.take_info_json(run.folder)
        core.tidy(run.folder, base)
        if not total:
            return self._finish(link, "failed", self._why_nothing(code, run))
        extra = ""
        try:
            info = core.write_gallery_info(run.folder, link["url"], meta, run.files, b["numbered"], run.stage)
            core.update_library(base, info)
            link["title"] = info["title"]
        except OSError as e:
            extra = f" (info file not saved: {e.strerror})"
        note = f"{total} image{'s' if total != 1 else ''}"
        if link["old"]:
            note += f", {link['new']} new"
        if link["failed"]:
            note += f", {link['failed']} failed"
        if link.get("replaced"):
            extra += " · " + link.pop("replaced")
        self._finish(link, "done", note + STAGE_DONE.get(run.stage, "") + extra)

    def _finish(self, link, status, note):
        link["status"], link["note"], link["finished"] = status, note, time.time()
        self._changed()
        self._next()

    @staticmethod
    def _paused_note(run):
        n = run.link["new"] + run.link["old"]
        return f"Paused after {n} image{'s' if n != 1 else ''}" if n else "Paused"

    @staticmethod
    def _why_nothing(code, run):
        text = " ".join(run.errors).lower()
        if "readonly database" in text:
            return "The gallery folder was deleted while downloading"
        if run.stage == "generic" and (not run.errors or "unsupported url" in text or "no suitable extractor" in text):
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

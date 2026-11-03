"""Kollect core: everything that works without a window.

Folder names from page addresses, gallery-dl output lines, gallery metadata,
the per-gallery info file, the library CSV and the saved queue.
"""
import csv
import filecmp
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
import uuid

APP_ID = "io.github.kamyar94.Kollect"
DEFAULT_BASE = "~/Pictures/Kollect Downloads"
INFO_FILE = "gallery-info.json"
LIBRARY_FILE = "library.csv"
WORK_DIR = ".kollect"  # inside each gallery folder: download archive + incoming files

LINK_RE = re.compile(r"(?:\b(?:generic|g):)?https?://[^\s<>\"']+", re.I)
PREFIX_RE = re.compile(r"^(generic|g):", re.I)
TRACKING_RE = re.compile(r"^(utm_\w+|fbclid|gclid|igshid|mc_cid|mc_eid|ref_src|si)$", re.I)
PAGE_EXT_RE = re.compile(r"\.(html?|php\d?|aspx?|jsp|cgi)$", re.I)
BAD_CHARS_RE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
LOG_RE = re.compile(r"^\[([\w.\-]+)\]\[(error|warning|info|debug)\] ?(.*)$")


# ---------------------------------------------------------------- links and folders

def find_links(text):
    """Links in pasted text, in order, without repeats."""
    seen, links = set(), []
    for m in LINK_RE.finditer(text or ""):
        link = m.group(0).rstrip(".,;:!?'\"")
        while link.endswith(")") and link.count("(") < link.count(")"):
            link = link[:-1]
        if link not in seen:
            seen.add(link)
            links.append(link)
    return links


def split_prefix(link):
    """'generic:https://x' -> (True, 'https://x')."""
    m = PREFIX_RE.match(link)
    return (True, link[m.end():]) if m else (False, link)


def safe_segment(text, limit=100):
    """One folder name that works on Linux, Windows and exFAT drives."""
    text = BAD_CHARS_RE.sub("_", text).strip(" .")
    while len(text.encode("utf-8")) > limit:
        text = text[:-1]
    text = text.strip(" .")
    return "" if text in ("", ".", "..") else text


def folder_parts(link):
    """https://www.example.com/galleries/summer-2024/ -> ['example.com', 'galleries', 'summer-2024']"""
    url = urllib.parse.urlsplit(split_prefix(link)[1])
    host = (url.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if url.port:
        host += f"_{url.port}"
    parts = [safe_segment(host) or "unknown-site"]
    segments = [s for s in url.path.split("/") if s]
    for i, seg in enumerate(segments):
        seg = urllib.parse.unquote(seg)
        if i == len(segments) - 1 and PAGE_EXT_RE.search(seg):
            seg = PAGE_EXT_RE.sub("", seg)
            if seg.lower() in ("index", "default"):
                continue
        seg = safe_segment(seg)
        if seg:
            parts.append(seg)
    query = [(k, v) for k, v in urllib.parse.parse_qsl(url.query, keep_blank_values=True)
             if not TRACKING_RE.match(k)]
    if query:
        seg = safe_segment(" ".join(f"{k}={v}" if v else k for k, v in query), 60)
        if seg:
            parts.append(seg)
    return parts


def site_of(link):
    return folder_parts(link)[0]


def gallery_folder(base, link):
    return os.path.join(base, *folder_parts(link))


def expand(path):
    return os.path.abspath(os.path.expanduser(path.strip())) if path and path.strip() else ""


def shorten(path):
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


# ---------------------------------------------------------------- gallery-dl

def find_gallery_dl(settings=None):
    configured = (settings or {}).get("gallery_dl") or ""
    for cand in (configured, shutil.which("gallery-dl"), os.path.expanduser("~/.local/bin/gallery-dl")):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def gallery_dl_args(gallery_dl, folder, link, stage, extra_args=()):
    """gallery-dl saves into <folder>/.kollect/incoming and Kollect moves each file out as soon as it is reported.
    skip=enumerate: a same-named file is saved as name.1.ext instead of being skipped because the incoming folder
    still held its namesake; the download archive alone decides what was downloaded before.

    stage "site": gallery-dl's own extractor for the site; "generic": every picture on the page;
    "fullsize": the list of full-size pictures that finder.py found (one direct link per line)."""
    work = os.path.join(folder, WORK_DIR)
    args = [gallery_dl, "-D", os.path.join(work, "incoming"),
            "--download-archive", os.path.join(work, "archive.sqlite3"), "-o", "skip=enumerate"]
    if stage == "fullsize":
        return args + ["-o", "extractor.directlink.filename={filename}.{extension}", *extra_args,
                       "-i", os.path.join(work, "full-size.txt")]
    _, url = split_prefix(link)
    return args + ["--write-info-json", *extra_args, ("generic:" + url) if stage == "generic" else url]


def finder_args(folder, link):
    return [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "finder.py"),
            split_prefix(link)[1], os.path.join(folder, WORK_DIR, "found.json")]


def take_found(folder):
    """Read and remove the finder's result; write the picture list gallery-dl reads. Returns the result or None."""
    path = os.path.join(folder, WORK_DIR, "found.json")
    try:
        with open(path, encoding="utf-8") as f:
            found = json.load(f)
        os.remove(path)
    except (OSError, ValueError):
        return None
    with open(os.path.join(folder, WORK_DIR, "full-size.txt"), "w", encoding="utf-8") as f:
        f.write("".join(u + "\n" for u in found.get("pictures", [])))
    return found


def drop_list(folder):
    try:
        os.remove(os.path.join(folder, WORK_DIR, "full-size.txt"))
    except OSError:
        pass


def parse_line(line):
    """('new', path) | ('old', path) | ('error'|'warning'|'info'|'debug', 'module: text') | ('text', line)"""
    line = line.rstrip("\r\n")
    m = LOG_RE.match(line)
    if m:
        return m.group(2), f"{m.group(1)}: {m.group(3)}"
    if line.startswith("# /"):
        return "old", line[2:]
    if line.startswith("/"):
        return "new", line
    return "text", line


def numbered_name(index, original):
    return f"{index:03d}{os.path.splitext(original)[1].lower()}"


def _free_name(folder, name):
    stem, ext = os.path.splitext(name)
    n = 2
    while os.path.exists(os.path.join(folder, name)):
        name = f"{stem} ({n}){ext}"
        n += 1
    return name


def place_new(src, folder, index, numbered):
    """Move a freshly downloaded file out of the incoming folder. Returns the final name."""
    original = os.path.basename(src)
    name = numbered_name(index, original) if numbered else original
    dest = os.path.join(folder, name)
    if os.path.exists(dest):
        if not numbered and filecmp.cmp(src, dest, shallow=False):
            os.remove(src)  # the same picture is already there
            return name
        name = _free_name(folder, name)
        dest = os.path.join(folder, name)
    os.replace(src, dest)
    return name


def place_old(reported, folder, index, numbered):
    """A file gallery-dl skipped because it had it already. Returns its name in the folder, or None."""
    original = os.path.basename(reported)
    if os.path.isfile(reported):  # left in incoming by an interrupted run
        return place_new(reported, folder, index, numbered)
    number = numbered_name(index, original)
    have_original = os.path.isfile(os.path.join(folder, original))
    have_number = os.path.isfile(os.path.join(folder, number))
    if numbered:
        if not have_number and have_original:
            os.replace(os.path.join(folder, original), os.path.join(folder, number))
            return number
        return number if have_number else (original if have_original else None)
    return original if have_original else (number if have_number else None)


def take_info_json(folder):
    """Read and remove the info.json gallery-dl wrote into the incoming folder."""
    path = os.path.join(folder, WORK_DIR, "incoming", "info.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    try:
        os.remove(path)
    except OSError:
        pass
    return data if isinstance(data, dict) else {}


def tidy(folder, base):
    """Remove the empty incoming folder; if the gallery got nothing at all, remove what Kollect made."""
    work = os.path.join(folder, WORK_DIR)
    try:
        os.rmdir(os.path.join(work, "incoming"))
    except OSError:
        return
    try:
        entries = os.listdir(folder)
    except OSError:
        return
    if entries == [WORK_DIR] and os.listdir(work) in ([], ["archive.sqlite3"]):
        shutil.rmtree(work, ignore_errors=True)
        path, base = os.path.abspath(folder), os.path.abspath(base)
        while path.startswith(base + os.sep):
            try:
                os.rmdir(path)
            except OSError:
                break
            path = os.path.dirname(path)


# ---------------------------------------------------------------- metadata

TITLE_KEYS = ("title", "gallery_title", "album_title", "title_en", "post_title", "headline")
NAME_KEYS = ("name", "album_name", "gallery_name", "album", "user", "username", "user_name", "author",
             "artist", "uploader", "owner", "creator", "og_site_name", "site_name")
TAG_KEYS = ("tags", "tag_string", "keywords", "hashtags", "categories")
NESTED_KEYS = ("gallery", "album", "post", "collection", "set")


def _text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, dict):
        for k in ("name", "title", "username", "nick"):
            if isinstance(value.get(k), str) and value[k].strip():
                return value[k].strip()
    return ""


def _tags(value):
    if isinstance(value, str):
        items = re.split(r"[,;\n]+", value) if re.search(r"[,;\n]", value) else value.split()
    elif isinstance(value, dict):
        items = [t for v in value.values() for t in _tags(v)]
    elif isinstance(value, (list, tuple)):
        items = [t for v in value for t in (_tags(v) if isinstance(v, (list, dict)) else [_text(v)])]
    else:
        items = []
    seen, out = set(), []
    for t in (i.strip() for i in items):
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def pick_meta(meta):
    """(title, name, tags) from gallery-dl's gallery metadata, whatever the site calls them."""
    nested = [meta[k] for k in NESTED_KEYS if isinstance(meta.get(k), dict)]
    title = next((t for k in TITLE_KEYS if (t := _text(meta.get(k)))), "")
    if not title:
        title = next((t for d in nested for k in TITLE_KEYS if (t := _text(d.get(k)))), "")
    name = next((n for k in NAME_KEYS if (n := _text(meta.get(k))) and n != title), "")
    tags = []
    for d in [meta, *nested]:
        for k in TAG_KEYS:
            tags = _tags(d.get(k))
            if tags:
                break
        if tags:
            break
    return title, name, tags


PICKED = {"site": "gallery-dl's extractor for this site", "generic": "everything on the page",
          "fullsize": "full-size pictures from the page"}


def write_gallery_info(folder, link, meta, files, numbered, stage):
    title, name, tags = pick_meta(meta)
    info = {
        "url": split_prefix(link)[1],
        "site": site_of(link),
        "title": title,
        "name": name,
        "tags": tags,
        "folder": folder,
        "downloaded": time.strftime("%Y-%m-%d %H:%M:%S"),
        "images": len(files),
        "numbered": numbered,
        "picked": PICKED.get(stage, stage),
        "files": files,
        "metadata": meta,
    }
    _write_json(os.path.join(folder, INFO_FILE), info)
    return info


def _read_info(folder):
    try:
        with open(os.path.join(folder, INFO_FILE), encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) else None


def _info_pictures(folder, info):
    names = [f["file"] for f in info.get("files", []) if isinstance(f, dict) and isinstance(f.get("file"), str)]
    return [n for n in names if os.path.basename(n) == n and os.path.isfile(os.path.join(folder, n))]


def existing_gallery(base, link):
    """The gallery already downloaded for this address under base, or None.

    A gallery counts once Kollect finished it (its gallery-info.json is there) and some of its pictures are still
    in the folder; a paused or half-done download has no info file yet, so it is not a duplicate."""
    folder = gallery_folder(base, link)
    info = _read_info(folder)
    if info is None:
        return None
    pictures = _info_pictures(folder, info)
    if info.get("files") and not pictures:
        return None
    return {"folder": folder, "url": info.get("url") or split_prefix(link)[1], "title": info.get("title") or "",
            "images": len(pictures) if info.get("files") else info.get("images", 0),
            "downloaded": info.get("downloaded", "")}


def replaced_files(folder):
    """What an overwrite moves out of the way: the pictures Kollect saved there and its info file."""
    info = _read_info(folder) or {}
    paths = [os.path.join(folder, n) for n in _info_pictures(folder, info)]
    if os.path.isfile(os.path.join(folder, INFO_FILE)):
        paths.append(os.path.join(folder, INFO_FILE))
    return paths


def forget_downloads(folder):
    """Drop the download archive (Kollect's own file), so gallery-dl fetches every picture again."""
    try:
        os.remove(os.path.join(folder, WORK_DIR, "archive.sqlite3"))
    except OSError:
        pass


LIBRARY_COLUMNS = ["Downloaded", "Site", "Title", "Name", "Tags", "URL", "Folder", "Images"]


def update_library(base, info):
    """One row per gallery address in <base>/library.csv; a new download of the same address replaces its row."""
    path = os.path.join(base, LIBRARY_FILE)
    rows = []
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = [r for r in csv.DictReader(f)]
    except OSError:
        pass
    folder = os.path.relpath(info["folder"], base) if info["folder"].startswith(base) else info["folder"]
    row = {"Downloaded": info["downloaded"], "Site": info["site"], "Title": info["title"], "Name": info["name"],
           "Tags": "; ".join(info["tags"]), "URL": info["url"], "Folder": folder, "Images": str(info["images"])}
    rows = [r for r in rows if r.get("URL") != info["url"]] + [row]
    def write(f):
        w = csv.DictWriter(f, fieldnames=LIBRARY_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    _replace_file(path, write, newline="", encoding="utf-8-sig")


# ---------------------------------------------------------------- start times

def next_time(hour, minute, now=None):
    """The next moment the clock shows hour:minute (today, or tomorrow if that has passed)."""
    now = time.time() if now is None else now
    lt = time.localtime(now)
    ts = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hour, minute, 0, 0, 0, -1))
    if ts <= now + 30:
        lt = time.localtime(now + 86400)
        ts = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hour, minute, 0, 0, 0, -1))
    return ts


def describe_date(stamp):
    """'2026-11-02 19:50:31' (as gallery-info.json keeps it) -> '2 Nov 2026, 19:50'."""
    try:
        t = time.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return stamp or ""
    return f"{t.tm_mday} {time.strftime('%b %Y, %H:%M', t)}"


def describe_time(ts, now=None):
    now = time.time() if now is None else now
    day = time.strftime("%Y-%m-%d", time.localtime(ts))
    if day == time.strftime("%Y-%m-%d", time.localtime(now)):
        return "today " + time.strftime("%H:%M", time.localtime(ts))
    if day == time.strftime("%Y-%m-%d", time.localtime(now + 86400)):
        return "tomorrow " + time.strftime("%H:%M", time.localtime(ts))
    return time.strftime("%a %d %b %H:%M", time.localtime(ts))


# ---------------------------------------------------------------- saved state

def _state_dir():
    return os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "kollect")


def _config_dir():
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "kollect")


def _replace_file(path, write, **open_args):
    """Write a whole file safely: into a hidden temporary file next to it, then swap it in, so a crash
    never leaves a half-written file. The temporary file is removed if anything goes wrong."""
    folder, name = os.path.split(path)
    tmp = os.path.join(folder, f".{name}.tmp")
    try:
        with open(tmp, "w", **open_args) as f:
            write(f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _replace_file(path, lambda f: json.dump(data, f, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def new_id():
    return uuid.uuid4().hex[:8]


def new_batch(links, base, numbered, mode="full", if_downloaded="skip"):
    """mode "full": full-size pictures only; "all": everything on the page (for sites gallery-dl does not know).
    if_downloaded: what a link does when its turn comes and its gallery is already there (nobody is asked then):
    "skip", "overwrite" (old pictures to the Trash, download again) or "update" (add pictures that are new on the
    page; the download archive keeps pictures you deleted from coming back)."""
    return {"id": new_id(), "base": base, "numbered": bool(numbered), "mode": mode, "if_downloaded": if_downloaded,
            "state": "active", "start_at": None, "added": time.time(),
            "links": [{"id": new_id(), "url": u, "status": "waiting", "note": "", "title": "", "folder": "",
                       "new": 0, "old": 0, "failed": 0, "generic": split_prefix(u)[0]} for u in links]}


class Settings(dict):
    DEFAULTS = {"base": DEFAULT_BASE, "numbered": False, "mode": "full", "warn_duplicates": True,
                "if_downloaded": "skip", "extra_args": [], "gallery_dl": ""}

    def __init__(self, path=None):
        super().__init__(self.DEFAULTS)
        self.path = path or os.path.join(_config_dir(), "settings.json")
        try:
            data = _read_json(self.path)
            if isinstance(data, dict):
                self.update(data)
        except (OSError, ValueError):
            pass

    def save(self):
        _write_json(self.path, dict(self))


class Store:
    """The queue: batches of links, saved after every change."""

    def __init__(self, path=None):
        self.path = path or os.path.join(_state_dir(), "queue.json")
        self.batches = []

    def load(self):
        try:
            data = _read_json(self.path)
            self.batches = [b for b in data.get("batches", []) if isinstance(b, dict) and b.get("links") is not None]
        except FileNotFoundError:
            self.batches = []
        except (OSError, ValueError, AttributeError):
            try:
                os.replace(self.path, self.path + ".bad")
            except OSError:
                pass
            self.batches = []
        for b in self.batches:
            for link in b["links"]:
                if link.get("status") == "running":  # Kollect was closed mid-download
                    link["status"] = "waiting"
        return self

    def save(self):
        _write_json(self.path, {"version": 1, "batches": self.batches})

    def batch(self, batch_id):
        return next((b for b in self.batches if b["id"] == batch_id), None)

    def queued_urls(self):
        return {link["url"] for b in self.batches for link in b["links"] if link["status"] in ("waiting", "running")}

    def next_link(self):
        for b in self.batches:
            if b["state"] != "active":
                continue
            for link in b["links"]:
                if link["status"] == "waiting":
                    return b, link
        return None

    def due(self, now=None):
        now = time.time() if now is None else now
        return [b for b in self.batches if b["state"] == "scheduled" and (b.get("start_at") or 0) <= now]


def log_path():
    return os.path.join(_state_dir(), "kollect.log")


def self_command():
    """How a start-time timer can launch Kollect again."""
    script = os.path.realpath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    if script and os.path.isfile(script) and not script.endswith("__main__.py"):
        return [sys.executable, script]
    return None

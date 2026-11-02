"""Find the full-size pictures of a gallery page: the large picture each thumbnail opens.

1. Thumbnails inside links (and lightbox elements): the link's picture, choosing the largest size the
   link lists (data-srcset and similar), or the original when the link names one.
2. If that finds fewer than two, thumbnails that open a page per photo: each of those pages is visited
   and its main picture taken. Pages on another site (image hosts) are handed to gallery-dl as they are.

Runs on its own (standard library only) so Kollect can start it as a separate process:
  python3 finder.py <page url> <result.json>
Prints "progress <done> <total>" while visiting photo pages and "error <text>" when the page cannot be read.
"""
import gzip
import html.parser
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import zlib

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
IMAGE_RE = re.compile(r"\.(jpe?g|jpe|png|gif|bmp|webp|avif|heic|tiff?)$", re.I)
# attributes that name the full picture (on a link, a lightbox element or the thumbnail itself)
FULL_ATTRS = ("data-orig-file", "data-full-url", "data-full", "data-fullsrc", "data-highres", "data-hires",
              "data-zoom-image", "data-pswp-src", "data-big", "data-large-file", "data-large")
LINK_ATTRS = FULL_ATTRS + ("href", "data-src", "data-href", "data-original", "data-image", "data-img")
SRCSET_ATTRS = ("data-srcset", "data-pswp-srcset")  # on a link: the sizes of the big picture
LIGHTBOX_ATTRS = ("data-fancybox", "data-lightbox", "data-sub-html", "data-pswp-width", "data-pswp-src",
                  "data-gallery", "data-lg-size", "data-featherlight")
MAX_PHOTO_PAGES = 300


def is_image(url):
    return bool(IMAGE_RE.search(urllib.parse.urlsplit(url).path))


def parse_srcset(value):
    """'a.jpg 1200w, b.jpg 800w' -> [('a.jpg', 1200), ('b.jpg', 800)]; '2x' counts as 2000."""
    out = []
    for part in re.split(r",\s+(?=\S)", value.strip()):
        bits = part.split()
        if not bits:
            continue
        size = 0
        if len(bits) > 1:
            m = re.fullmatch(r"(\d+(?:\.\d+)?)([wx])", bits[1])
            if m:
                size = int(float(m.group(1)) * (1 if m.group(2) == "w" else 1000))
        out.append((bits[0], size))
    return out


class PageParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.base = None
        self.meta = {}
        self.title = ""
        self.in_title = False
        self.link = None
        self.items = []  # links and lightbox elements, in page order
        self.images = []  # every <img>

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "base" and a.get("href") and self.base is None:
            self.base = a["href"]
        elif tag == "title":
            self.in_title = True
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content") and key not in self.meta:
                self.meta[key] = a["content"]
        elif tag == "a":
            self.link = {"attrs": a, "imgs": []}  # links cannot be nested, a new one closes the last
            self.items.append(self.link)
        elif tag in ("img", "source"):
            if tag == "img":
                self.images.append(a)
            if self.link is not None:
                self.link["imgs"].append(a)
            elif tag == "img" and any(k in a for k in FULL_ATTRS):
                self.items.append({"attrs": {}, "imgs": [a]})
        elif any(k in a for k in LIGHTBOX_ATTRS):
            self.items.append({"attrs": a, "imgs": []})

    def handle_endtag(self, tag):
        if tag == "a":
            self.link = None
        elif tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data


def full_picture(item, base):
    """The full picture of one thumbnail link or lightbox element, or None."""
    a = item["attrs"]
    sizes = {}
    for key in SRCSET_ATTRS:
        for url, size in parse_srcset(a.get(key, "")):
            url = urllib.parse.urljoin(base, url)
            sizes[url] = max(size, sizes.get(url, 0))
    largest = max(sizes.items(), key=lambda kv: kv[1]) if sizes else None
    named = [(a, key) for key in LINK_ATTRS] + [(img, key) for img in item["imgs"] for key in FULL_ATTRS]
    for attrs, key in named:
        value = attrs.get(key, "").strip()
        if not value or value.startswith(("#", "javascript:", "data:")):
            continue
        url = urllib.parse.urljoin(base, value)
        if key in FULL_ATTRS or is_image(url):
            if largest and url in sizes and sizes[url] < largest[1]:
                return largest[0]
            return url
    return largest[0] if largest else None


def is_thumbnail_item(item):
    return bool(item["imgs"]) or any(k in item["attrs"] for k in LIGHTBOX_ATTRS)


def gallery_pictures(parser, base):
    pictures = []
    for item in parser.items:
        if is_thumbnail_item(item):
            url = full_picture(item, base)
            if url and url not in pictures and url.startswith(("http://", "https://")):
                pictures.append(url)
    return pictures


def photo_page_links(parser, base, page_url):
    """Links from thumbnails to other pages, the largest group of similar addresses (two or more)."""
    groups = {}
    for item in parser.items:
        href = item["attrs"].get("href", "").strip()
        if not item["imgs"] or not href or href.startswith(("#", "javascript:")):
            continue
        url = urllib.parse.urljoin(base, href).split("#")[0]
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or is_image(url) or url.rstrip("/") == page_url.rstrip("/"):
            continue
        parent = re.sub(r"\d+", "#", parts.path.rstrip("/").rpartition("/")[0])
        group = groups.setdefault((parts.hostname, parent), [])
        if url not in group:
            group.append(url)
    best = max(groups.values(), key=len, default=[])
    return best if len(best) >= 2 else []


def main_picture(parser, base):
    """The main picture of a photo page."""
    pictures = gallery_pictures(parser, base)
    if pictures:
        return pictures[0]
    og = parser.meta.get("og:image", "").strip()
    if og:
        return urllib.parse.urljoin(base, og)
    best, best_size = None, 299
    for img in parser.images:
        width = img.get("width", "")
        candidates = parse_srcset(img.get("srcset", "")) + [(img.get("src", ""), int(width) if width.isdigit() else 0)]
        for url, size in candidates:
            if url and size > best_size:
                best, best_size = urllib.parse.urljoin(base, url), size
    return best


def fetch(url, referer=None):
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
               "Accept-Language": "en-US,en;q=0.7", "Accept-Encoding": "gzip, deflate"}
    if referer:
        headers["Referer"] = referer
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
        data = response.read(10_000_000)
        encoding = response.headers.get("Content-Encoding", "").lower()
        if encoding == "gzip":
            data = gzip.decompress(data)
        elif encoding == "deflate":
            try:
                data = zlib.decompress(data)
            except zlib.error:
                data = zlib.decompress(data, -zlib.MAX_WBITS)
        charset = response.headers.get_content_charset()
        if not charset:
            m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", data[:4096], re.I)
            charset = m.group(1).decode() if m else "utf-8"
        try:
            text = data.decode(charset, "replace")
        except LookupError:
            text = data.decode("utf-8", "replace")
        return response.geturl(), text


def parse(text):
    parser = PageParser()
    parser.feed(text)
    parser.close()
    return parser


def find(page_url, get=fetch, pause=0.25, report=print):
    final_url, text = get(page_url)
    parser = parse(text)
    base = urllib.parse.urljoin(final_url, parser.base or "")
    meta = parser.meta
    result = {
        "pageurl": page_url,
        "title": (meta.get("og:title") or parser.title).strip(),
        "og_site_name": meta.get("og:site_name", "").strip(),
        "keywords": meta.get("keywords", "").strip(),
        "description": (meta.get("og:description") or meta.get("description") or "").strip(),
        "how": "thumbnail links",
        "pictures": gallery_pictures(parser, base),
    }
    if len(result["pictures"]) >= 2:
        return result
    pages = photo_page_links(parser, base, final_url)
    if not pages:
        return result
    host = urllib.parse.urlsplit(final_url).hostname
    if urllib.parse.urlsplit(pages[0]).hostname != host:
        result.update(how="image host pages", pictures=pages)  # gallery-dl knows most image hosts
        return result
    pictures = []
    pages = pages[:MAX_PHOTO_PAGES]
    for i, url in enumerate(pages, 1):
        report(f"progress {i} {len(pages)}", flush=True)
        try:
            sub_url, sub_text = get(url, final_url)
            picture = main_picture(parse(sub_text), urllib.parse.urljoin(sub_url, ""))
        except Exception:  # one broken photo page does not stop the rest
            picture = None
        if picture and picture not in pictures:
            pictures.append(picture)
        if pause:
            time.sleep(pause)
    result.update(how="photo pages", pictures=pictures)
    return result


def main(argv):
    url, out = argv[1], argv[2]
    try:
        result = find(url)
    except Exception as e:
        reason = getattr(e, "reason", None) or getattr(e, "code", None) or e
        print(f"error Could not read the page: {reason}", flush=True)
        return 2
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"found {len(result['pictures'])}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

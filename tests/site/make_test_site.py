"""Build a tiny local test site whose pages list their pictures in a known order.

  galleries/summer-2024/     3 pictures, page order zeta, alpha, mid (not alphabetical on purpose)
  photos/trip/page.html      2 pictures, page order b, a
  slow/set/                  4 pictures; test_site_server.py waits 3 s before each one (for Pause tests)
Usage: python3 make_test_site.py <folder>
"""
import os
import struct
import sys
import zlib


def png(path, rgb, size=48):
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def page(folder, filename, title, keywords, pictures):
    os.makedirs(os.path.join(folder, "img"), exist_ok=True)
    for name, rgb in pictures:
        png(os.path.join(folder, "img", name + ".png"), rgb)
    tags = "".join(f'<img src="img/{name}.png">' for name, _ in pictures)
    with open(os.path.join(folder, filename), "w") as f:
        f.write(f"""<!doctype html><html><head><meta charset="utf-8">
<title>{title}</title>
<meta name="keywords" content="{keywords}">
<meta property="og:site_name" content="Kollect Test Site">
</head><body><h1>{title}</h1>{tags}</body></html>""")


root = sys.argv[1]
page(os.path.join(root, "galleries", "summer-2024"), "index.html", "Summer 2024 at the lake", "summer, lake, test",
     [("zeta", (200, 40, 40)), ("alpha", (40, 160, 40)), ("mid", (40, 40, 200))])
page(os.path.join(root, "photos", "trip"), "page.html", "Trip photos", "trip, road",
     [("b", (220, 200, 40)), ("a", (40, 200, 200))])
page(os.path.join(root, "slow", "set"), "index.html", "Slow set", "slow",
     [(f"s{i}", (30 * i, 90, 120)) for i in range(1, 5)])
print("test site ready in", root)

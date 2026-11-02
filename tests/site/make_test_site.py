"""Build a tiny local test site whose pages list their pictures in a known order.

  galleries/summer-2024/     3 pictures, page order zeta, alpha, mid (not alphabetical on purpose)
  photos/trip/page.html      2 pictures, page order b, a
  slow/set/                  4 pictures; test_site_server.py waits 3 s before each one (for Pause tests)
  lightbox/                  3 thumbnails linking to large pictures in several sizes, plus other galleries and a logo
  photo-pages/               3 thumbnails, each opening a page with the large picture
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


def lightbox_gallery(folder):
    """Like a typical lightbox gallery: small thumbnails inside links to the large pictures; each link also lists
    the sizes the site has (data-srcset). Plus links to other galleries and a logo, which are not part of it.
    Sizes: _w400 thumbnail 24 px, _1200 48 px, _1800 72 px, _2400 96 px."""
    os.makedirs(os.path.join(folder, "img"), exist_ok=True)
    links = []
    for n, sizes in ((1, (1200,)), (2, (1200, 1800, 2400)), (3, (1200,))):
        name = f"0001-0{n}"
        for size, px in ((400, 24), (1200, 48), (1800, 72), (2400, 96)):
            if size == 400 or size in sizes:
                png(os.path.join(folder, "img", f"{name}_{'w' if size == 400 else ''}{size}.png"),
                    (60 * n, 100, 160), px)
        srcset = ", ".join(f"img/{name}_{s}.png {s}w" for s in sorted(sizes, reverse=True))
        href = f"img/{name}_{1800 if 1800 in sizes else 1200}.png"
        links.append(f'<a data-fancybox="images" data-srcset="{srcset}" href="{href}">'
                     f'<img src="img/{name}_w400.png" srcset="img/{name}_w400.png 400w"></a>')
    png(os.path.join(folder, "img", "logo.png"), (0, 0, 0), 16)
    for other in ("winter-2023", "spring-2025"):
        png(os.path.join(folder, "img", f"{other}_masonry_600.png"), (200, 200, 200), 20)
        links.append(f'<a href="/galleries/{other}/"><img src="img/{other}_masonry_600.png"></a>')
    with open(os.path.join(folder, "index.html"), "w") as f:
        f.write(f"""<!doctype html><html><head><meta charset="utf-8"><title>Lightbox test</title>
<meta name="keywords" content="lightbox, test"></head><body><img src="img/logo.png">{''.join(links)}</body></html>""")


def photo_pages_gallery(folder):
    """Thumbnails that each open a page of their own with the large picture."""
    os.makedirs(os.path.join(folder, "img"), exist_ok=True)
    os.makedirs(os.path.join(folder, "photo"), exist_ok=True)
    links = []
    for n in (1, 2, 3):
        png(os.path.join(folder, "img", f"t{n}.png"), (n * 70, 40, 90), 16)
        png(os.path.join(folder, "img", f"big{n}.png"), (n * 70, 40, 90), 80)
        links.append(f'<a href="photo/{n}.html"><img src="img/t{n}.png"></a>')
        with open(os.path.join(folder, "photo", f"{n}.html"), "w") as f:
            f.write(f"""<!doctype html><html><head><title>Photo {n}</title>
<meta property="og:image" content="/photo-pages/img/big{n}.png"></head>
<body><img src="../img/big{n}.png" width="800"><a href="{n % 3 + 1}.html"><img src="../img/t{n % 3 + 1}.png"></a></body></html>""")
    with open(os.path.join(folder, "index.html"), "w") as f:
        f.write(f"""<!doctype html><html><head><title>Photo pages test</title></head>
<body>{''.join(links)}</body></html>""")


root = sys.argv[1]
lightbox_gallery(os.path.join(root, "lightbox"))
photo_pages_gallery(os.path.join(root, "photo-pages"))
page(os.path.join(root, "galleries", "summer-2024"), "index.html", "Summer 2024 at the lake", "summer, lake, test",
     [("zeta", (200, 40, 40)), ("alpha", (40, 160, 40)), ("mid", (40, 40, 200))])
page(os.path.join(root, "photos", "trip"), "page.html", "Trip photos", "trip, road",
     [("b", (220, 200, 40)), ("a", (40, 200, 200))])
page(os.path.join(root, "slow", "set"), "index.html", "Slow set", "slow",
     [(f"s{i}", (30 * i, 90, 120)) for i in range(1, 5)])
print("test site ready in", root)

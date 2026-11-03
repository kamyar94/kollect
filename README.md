# Kollect

Kollect is a desktop window for [gallery-dl](https://github.com/mikf/gallery-dl) on Linux, made as a
Bulk Image Downloader replacement. Paste a list of gallery links, choose a folder, and Kollect
downloads the full-size pictures of every gallery, one after another, into folders named after
each page's address. It can number the pictures in page order and keeps each gallery's title,
tags, name and address in a small file and in one library spreadsheet.

Created by Kamyar Fatemifar.

![Kollect with three batches: one downloading, one waiting for its start time, one paused](docs/screenshots/main.png)

## What it does

- **A queue of links.** Paste many links at once, one per line. They download one after another
  in order, more can be added while it runs, and every link shows its own progress. The queue is
  saved after every change, so it is still there after closing Kollect.
- **Batches.** The links added together form a batch with its own folder, its own Start/Pause
  button and its own start time.
- **Folders from the page address.** `https://www.example.com/galleries/summer-2024/` is saved
  in `<your folder>/example.com/galleries/summer-2024/`. Page endings such as `.html` and
  tracking parameters such as `utm_source` are left out of the folder names, and the names work
  on Linux, Windows and exFAT drives.
- **Numbering in page order.** With *Number files* on, the pictures are named `001`, `002`,
  `003`... in the order the gallery shows them. With it off, they keep the site's names.
- **Title, tags, name and address.** Each gallery folder gets a `gallery-info.json` with the
  gallery's title, tags, name, address, the list of files and everything else gallery-dl reported
  about it. Each batch folder gets a `library.csv` with one row per gallery (date, site, title,
  name, tags, address, folder, number of pictures); downloading the same address again updates
  its row.
- **Any website, full size or everything.** gallery-dl knows a long list of sites and takes their
  full-size pictures. For a site it does not know, each batch has a choice:
  - **Full size** (the default): only the large picture each thumbnail opens, at the largest size
    the page offers. Kollect reads the page, follows each thumbnail's link (lightbox galleries
    often list several sizes there, and the largest is taken), leaves out logos, icons and links
    to other galleries, and hands gallery-dl that list in page order. When the thumbnails open a
    page per photo instead, Kollect visits those pages and takes each one's main picture; when
    they open pages on an image host, gallery-dl handles those pages.
  - **Everything**: every picture on the page, with gallery-dl's generic extractor. This also
    takes the thumbnails, every size the page offers, and the page's own logos and buttons.
- **Start times.** A batch can wait for a time of day. If Kollect is closed at that time, a
  systemd user timer opens it and the batch starts.
- **Galleries you already have.** Before downloading a gallery again, Kollect warns you and lets
  you skip or overwrite them all; for timed and queued runs that nobody watches, a fixed choice
  applies. See [Galleries already downloaded](#galleries-already-downloaded).
- **From your browser.** A browser add-on for Chrome, Brave, Chromium, Edge, Vivaldi and Firefox
  sends the addresses of your open tabs, or the links in highlighted text, straight into Kollect's
  links box and copies them to the clipboard. See [Send links from your browser](#send-links-from-your-browser).
- **Paper-simple.** One window. A link already on the clipboard is filled in when you switch to
  Kollect.

## Install

Kollect needs a Linux desktop with GTK 4.12 or newer and libadwaita 1.6 or newer (Ubuntu 24.10 or
newer, for example; it is tested on Ubuntu 26.04 with GNOME), Python 3 with PyGObject, and
gallery-dl, which is installed separately:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 pipx
```

```bash
pipx install gallery-dl
```

Then install Kollect for your user (no sudo):

```bash
git clone https://github.com/kamyar94/kollect.git && cd kollect && ./install.sh
```

Kollect then appears in the app list. `./install.sh` again updates it; `./install.sh --uninstall`
removes the program and leaves your downloads, the queue and the settings in place.

## Using it

1. Paste one or more gallery links into the box, one per line.
2. *Save in* is the folder for this batch. Type a path or press the folder button.
3. Choose **Full size** or **Everything** (this matters only for sites gallery-dl does not know).
4. Turn *Number files* on to name the pictures 001, 002, 003... in page order.
5. Press **Add and start** (or Ctrl+Enter in the links box). The arrow next to it adds the batch
   paused, or with a start time.

![The arrow next to Add and start: Add paused, or add with a start time](docs/screenshots/add-menu.png)

Each batch shows its folder, how many links are done, its choices (full size or everything,
numbered) and its state, with four buttons: remove the batch from the list, open its folder, the
clock (set or remove a start time) and Start/Pause. Each link shows its progress and has buttons
to open its gallery folder and to remove it from the list. *Clear finished* removes finished rows
from the list; the downloaded files stay.

![A batch's clock: set or remove its start time](docs/screenshots/start-time.png)

Closing Kollect while it downloads asks first; the batch is then paused and continues from where
it stopped when you press Start again. When Kollect opens, nothing starts by itself except
batches whose start time has come.

## Galleries already downloaded

A gallery counts as downloaded once Kollect has finished it in that folder: its `gallery-info.json`
is there and some of its pictures still are. (Without this check, a second download of the same
address goes into the same folder and adds only the pictures that are new on the page.)

With **Warn me before downloading a gallery again** ticked, adding links whose galleries are
already in the chosen folder shows them in a list, with three choices:

- **Skip all**: those links are left out; the other links are added.
- **Overwrite all**: the old pictures and their info file go to the Trash, and the galleries are
  downloaded again. Other files you put in the gallery folder are not touched.
- **Cancel**: nothing is added, and the links stay in the box.

![Kollect warns that two of three galleries are already downloaded](docs/screenshots/duplicates.png)

Nobody is asked when a link's turn comes, so every link checks again just before it starts: a
batch with a start time, links added from the command line, or a gallery that was downloaded after
you added the link. The two boxes after **When I'm not asked (timed or queued)** decide what
happens then, and each batch keeps the choice it was added with:

- **Skip it**: the link is marked *Skipped: already downloaded* (the default).
- **Download it again**: as Overwrite all.
- Neither ticked: only pictures that are new on the page since the last download are added.
  Pictures you deleted yourself do not come back, because the download archive remembers them.

## Send links from your browser

Kollect comes with a small browser add-on. Its toolbar button opens a panel with two choices:
**This window** sends the addresses of the tabs in the window you are in, and **All windows** sends
the tabs of every window of the same kind, so private (incognito) tabs never mix with normal ones.
Only the tab addresses are sent, not the links inside the pages.

![The add-on's panel after sending three tabs](docs/screenshots/browser-panel.png)

To send links from a page instead, highlight the part of the page that holds them, right-click
and choose **Send selected links to Kollect**: every link the highlight touches is sent, plus any
web address written in the highlighted text. Right-clicking a single link sends that link.

Either way the links are copied to the clipboard and added to Kollect's links box, and Kollect
opens if it was closed. You then choose the folder, Full size or Everything and numbering, and
press Add and start as usual.

To set it up, open Kollect and press **Add to browsers**. It lists the browsers on your computer
with the steps for each:

- **Chrome, Brave, Chromium, Edge, Vivaldi**: once, turn on Developer mode on the browser's add-on
  page and load the add-on folder with *Load unpacked*, then allow it in incognito or private
  windows in the add-on's details. Kollect copies the add-on page's address and the folder for you.
- **Firefox**: Firefox keeps only add-ons signed by Mozilla, so Kollect's add-on is loaded as a
  temporary add-on from Firefox's add-on test page, again after each Firefox restart, and allowed in
  private windows from the add-ons manager. With Firefox from Ubuntu's snap, the system asks once
  whether Firefox may start Kollect's helper.

![Add to browsers](docs/screenshots/add-to-browsers.png)

The add-on reaches Kollect through the browsers' native messaging: `install.sh` registers a small
helper, `io.github.kamyar94.kollect`, for every browser it finds (for example in
`~/.config/BraveSoftware/Brave-Browser/NativeMessagingHosts/` and
`~/.mozilla/native-messaging-hosts/`), and that helper hands the links to `kollect --paste`.
`./install.sh --uninstall` removes the registrations again. The add-on itself is in
`browser-addon/`.

## Where files go

```
Kollect Downloads/                     the folder chosen in "Save in"
├── library.csv                        one row per gallery in this folder
└── example.com/
    └── galleries/
        └── summer-2024/
            ├── 001.jpg  002.jpg  ...
            ├── gallery-info.json
            └── .kollect/              download archive (what was already downloaded)
```

The hidden `.kollect` folder lets a second run skip the pictures it already has, even after they
were renamed to numbers. To download a gallery again from scratch, delete its folder.

Numbers follow the position on the page. When a site lists the same picture twice, the copy is
skipped and its number stays unused, so a number can be missing.

Full size works with pages that put the large picture's address in the page itself, which is how
most lightbox and photo-page galleries are built. It cannot see pictures that a page loads only
with JavaScript after it opens, and gallery-dl downloads a listed picture only when its address
ends in a picture type such as `.jpg`, `.png` or `.webp`. In those cases try Everything.

## Settings

`~/.config/kollect/settings.json` holds the last folder, Full size/Everything, numbering and duplicate
choices, and two keys you can edit while Kollect is closed:

- `extra_args`: more gallery-dl options for every download. For sites that need you signed in,
  `["--cookies-from-browser", "firefox"]` uses the cookies of your Firefox profile (see
  gallery-dl's documentation for other browsers and options).
- `gallery_dl`: the full path of gallery-dl, if it is not on your `PATH` or in `~/.local/bin`.

The queue is in `~/.local/state/kollect/queue.json`, and gallery-dl's output is logged to
`~/.local/state/kollect/kollect.log`.

## Tests

```bash
python3 tests/test_core.py
```

checks folder names, numbering, metadata, the library file, start times, the saved queue and the
full-size picture finder.
`tests/atspi_drive.py` drives a running Kollect window through the accessibility bus, and
`tests/site/` has a small local test website with pictures in a known page order; its server file
explains how gallery-dl is pointed at it. `tests/browser_drive.mjs` (Chrome family) and
`tests/firefox_drive.mjs` (Firefox) load the add-on into a browser with a throwaway profile and
use its panel and right-click action (Node 22 or newer).
`tests/ui_duplicates.py` ticks the duplicate options in a separate window, since GTK 4's check boxes
cannot be clicked through the accessibility bus.

The window pictures above come from `python3 tools/screenshots.py docs/screenshots`, which opens a
separate Kollect window filled with example batches (nothing is downloaded and your own queue is not
touched) and saves it as pictures; the add-on panel picture comes from `tests/browser_drive.mjs` with
`KOLLECT_PANEL_SHOT` set.

## License

Kollect is published under the [KF Source-Available License 1.0](LICENSE.md). You may read it,
download it, fork it and run it privately for yourself; deploying it for others, changing it or
using it commercially needs written permission. It is not an open-source license.

Kollect does not include gallery-dl or any of its code: it runs the gallery-dl program that you
install yourself. gallery-dl is made by Mike Fährmann and contributors and is licensed under the
GNU General Public License version 2. GTK, libadwaita, PyGObject and the Adwaita icons that the
window uses come from your system and keep their own licenses.

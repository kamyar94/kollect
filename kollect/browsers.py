"""The browser add-on: which browsers are here, the bridge that lets the add-on reach Kollect, the add-on files.

Chrome-family browsers load the add-on folder once ("Load unpacked") and keep it. Firefox only keeps add-ons
signed by Mozilla, so it loads the add-on file as a temporary add-on, again after each Firefox restart.
  python3 browsers.py --register     set up the bridge for every browser found (install.sh runs this)
  python3 browsers.py --unregister   remove it again
"""
import json
import os
import shutil
import sys
import zipfile

HOST_NAME = "io.github.kamyar94.kollect"
CHROMIUM_ID = "agagcpfmeinibgehppcmehedifejihll"  # fixed by the "key" in browser-addon/manifest.json
FIREFOX_ID = "kollect@kamyar94.github.io"

PACKAGE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.join(os.path.dirname(PACKAGE), "browser-addon")


def data_dir():
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "kollect")


class Browser:
    def __init__(self, name, commands, config, page, kind):
        self.name, self.commands, self.config, self.page, self.kind = name, commands, config, page, kind

    @property
    def command(self):
        return next((path for c in self.commands if (path := shutil.which(c))), None)

    @property
    def hosts_dir(self):
        if self.kind == "firefox":
            return os.path.expanduser("~/.mozilla/native-messaging-hosts")
        return os.path.join(os.path.expanduser(self.config), "NativeMessagingHosts")

    @property
    def manifest_path(self):
        return os.path.join(self.hosts_dir, HOST_NAME + ".json")

    def present(self):
        return self.command is not None


BROWSERS = [
    Browser("Brave", ("brave-browser", "brave-browser-stable", "brave"), "~/.config/BraveSoftware/Brave-Browser",
            "brave://extensions/", "chromium"),
    Browser("Google Chrome", ("google-chrome", "google-chrome-stable"), "~/.config/google-chrome",
            "chrome://extensions/", "chromium"),
    Browser("Chromium", ("chromium", "chromium-browser"), "~/.config/chromium", "chrome://extensions/", "chromium"),
    Browser("Microsoft Edge", ("microsoft-edge", "microsoft-edge-stable"), "~/.config/microsoft-edge",
            "edge://extensions/", "chromium"),
    Browser("Vivaldi", ("vivaldi", "vivaldi-stable"), "~/.config/vivaldi", "vivaldi://extensions/", "chromium"),
    Browser("Firefox", ("firefox",), "~/.mozilla", "about:debugging#/runtime/this-firefox", "firefox"),
]


def found():
    return [b for b in BROWSERS if b.present()]


def host_program():
    """A small launcher for the bridge, so browsers start it with the Python that runs Kollect."""
    path = os.path.join(data_dir(), "kollect-browser-host")
    script = os.path.join(PACKAGE, "browser_host.py")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    os.chmod(path, 0o755)
    return path


def host_manifest(browser, program):
    data = {"name": HOST_NAME, "description": "Kollect browser bridge", "path": program, "type": "stdio"}
    if browser.kind == "firefox":
        data["allowed_extensions"] = [FIREFOX_ID]
    else:
        data["allowed_origins"] = [f"chrome-extension://{CHROMIUM_ID}/"]
    return data


def firefox_package():
    """The Firefox add-on as one file: browser-addon with manifest.firefox.json as its manifest."""
    path = os.path.join(data_dir(), "kollect-firefox.xpi")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path + ".part", "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(ADDON):
            for name in sorted(files):
                full = os.path.join(root, name)
                inside = os.path.relpath(full, ADDON).replace(os.sep, "/")
                if inside == "manifest.json":
                    continue
                z.write(full, "manifest.json" if inside == "manifest.firefox.json" else inside)
    os.replace(path + ".part", path)
    return path


def register(browsers=None):
    """Write the bridge registration for each browser. Returns the names that were set up."""
    browsers = found() if browsers is None else browsers
    program = host_program()
    for browser in browsers:
        os.makedirs(browser.hosts_dir, exist_ok=True)
        with open(browser.manifest_path, "w", encoding="utf-8") as f:
            json.dump(host_manifest(browser, program), f, indent=2)
    if any(b.kind == "firefox" for b in browsers):
        firefox_package()
    return [b.name for b in browsers]


def unregister():
    for browser in BROWSERS:
        try:
            os.remove(browser.manifest_path)
        except OSError:
            pass
    for name in ("kollect-browser-host", "kollect-firefox.xpi"):
        try:
            os.remove(os.path.join(data_dir(), name))
        except OSError:
            pass


def what_to_load(browser):
    """The folder (Chrome family) or file (Firefox) the browser's own dialog should open."""
    return firefox_package() if browser.kind == "firefox" else ADDON


if __name__ == "__main__":
    if "--unregister" in sys.argv:
        unregister()
        print("Kollect's browser bridge removed.")
    else:
        names = register()
        print("Kollect's browser bridge is set up for: " + (", ".join(names) or "no browser found"))

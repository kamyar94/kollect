#!/bin/sh
# Install Kollect for the current user (no sudo needed).
#   ./install.sh               install or update
#   ./install.sh --uninstall   remove the program (downloads, queue and settings stay)
set -eu

APP_ID=io.github.kamyar94.Kollect
SRC=$(cd "$(dirname "$0")" && pwd)
DATA=${XDG_DATA_HOME:-$HOME/.local/share}
BIN=$HOME/.local/bin

refresh() {
    update-desktop-database "$DATA/applications" >/dev/null 2>&1 || true
    gtk-update-icon-cache -q -t "$DATA/icons/hicolor" >/dev/null 2>&1 || true
}

if [ "${1:-}" = "--uninstall" ]; then
    rm -rf "$DATA/kollect"
    rm -f "$BIN/kollect" "$DATA/applications/$APP_ID.desktop" "$DATA/icons/hicolor/scalable/apps/$APP_ID.svg"
    refresh
    echo "Kollect removed. Your downloads, the queue (~/.local/state/kollect) and settings (~/.config/kollect) are still there."
    exit 0
fi

if ! python3 -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); from gi.repository import Gtk, Adw' 2>/dev/null; then
    echo "Kollect needs GTK 4 and libadwaita for Python. On Ubuntu or Debian:"
    echo "    sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1"
    exit 1
fi

mkdir -p "$DATA/kollect" "$BIN" "$DATA/applications" "$DATA/icons/hicolor/scalable/apps"
rm -rf "$DATA/kollect/kollect"
mkdir -p "$DATA/kollect/kollect"
cp "$SRC"/kollect/*.py "$DATA/kollect/kollect/"
install -m 755 "$SRC/bin/kollect" "$BIN/kollect"
sed "s|@BIN@|$BIN/kollect|" "$SRC/data/$APP_ID.desktop.in" > "$DATA/applications/$APP_ID.desktop"
install -m 644 "$SRC/data/$APP_ID.svg" "$DATA/icons/hicolor/scalable/apps/$APP_ID.svg"
refresh

echo "Kollect installed: look for it in your app list, or run: kollect"
if ! command -v gallery-dl >/dev/null 2>&1 && [ ! -x "$BIN/gallery-dl" ]; then
    echo
    echo "gallery-dl is not installed yet. Kollect needs it to download:"
    echo "    sudo apt install pipx && pipx install gallery-dl"
fi

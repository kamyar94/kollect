#!/usr/bin/env python3
"""Kollect's browser bridge. The browser add-on starts this program (native messaging) and sends it one message,
{"action": "paste", "links": [...]}; the links go into Kollect's links box, and Kollect opens if it is closed.
Standard library only; one message in, one answer out."""
import json
import os
import shutil
import struct
import subprocess
import sys

MAX_LINKS = 5000
MAX_MESSAGE = 8 * 1024 * 1024


def read_message(stream):
    head = stream.read(4)
    if len(head) < 4:
        return None
    (size,) = struct.unpack("=I", head)  # browsers use the machine's own byte order
    if size > MAX_MESSAGE:
        raise ValueError("message too large")
    return json.loads(stream.read(size).decode("utf-8"))


def write_message(stream, data):
    body = json.dumps(data).encode("utf-8")
    stream.write(struct.pack("=I", len(body)) + body)
    stream.flush()


def clean(links):
    """Web addresses only, each once, no whitespace inside, at most MAX_LINKS."""
    seen, out = set(), []
    for url in links if isinstance(links, list) else []:
        if (isinstance(url, str) and url.startswith(("http://", "https://")) and len(url) <= 8192
                and not any(c.isspace() or c == "\0" for c in url) and url not in seen):
            seen.add(url)
            out.append(url)
    return out[:MAX_LINKS]


def kollect_command():
    here = os.path.dirname(os.path.realpath(__file__))
    for launcher in (os.path.expanduser("~/.local/bin/kollect"), shutil.which("kollect"),
                     os.path.join(here, "..", "bin", "kollect")):
        if launcher and os.path.isfile(launcher):
            return [sys.executable, os.path.realpath(launcher)]
    return None


def answer(message):
    if not isinstance(message, dict) or message.get("action") != "paste":
        return {"ok": False, "error": "unknown request"}
    links = clean(message.get("links"))
    if not links:
        return {"ok": False, "error": "no web links"}
    command = kollect_command()
    if not command:
        return {"ok": False, "error": "Kollect is not installed"}
    # its own session, so the browser closing this helper does not close Kollect
    subprocess.Popen(command + ["--paste", *links], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    return {"ok": True, "count": len(links)}


def main():
    try:
        message = read_message(sys.stdin.buffer)
        reply = answer(message)
    except Exception as e:  # a broken message gets an answer, never a hanging browser
        reply = {"ok": False, "error": f"bad message: {e}"}
    write_message(sys.stdout.buffer, reply)
    return 0 if reply.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

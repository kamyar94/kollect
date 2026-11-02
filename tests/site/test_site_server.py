"""Serve the test site on 127.0.0.1:8765 as a proxy target.

gallery-dl's generic extractor ignores ports, so tests use a made-up host name
(www.kollect-test.example) and point gallery-dl's per-host proxy option here, in ~/.config/kollect/settings.json:
  "extra_args": ["-o", "extractor.generic.proxy={\"http://www.kollect-test.example\": \"http://127.0.0.1:8765\"}"]
Requests arrive as absolute URLs; only the path is served. Pictures under /slow/ wait 3 s each.
Usage: python3 test_site_server.py <folder>
"""
import functools
import http.server
import sys
import time
import urllib.parse


class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        return super().translate_path(urllib.parse.urlsplit(path).path or "/")

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/slow/") and path.endswith(".png"):
            time.sleep(3)
        super().do_GET()

    def log_message(self, *args):
        pass


http.server.ThreadingHTTPServer(("127.0.0.1", 8765),
                                functools.partial(Handler, directory=sys.argv[1])).serve_forever()

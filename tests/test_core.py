"""Checks for the parts of Kollect that need no window. Run: python3 -m unittest discover tests"""
import csv
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from kollect import core  # noqa: E402


class Links(unittest.TestCase):
    def test_find_links_keeps_order_and_drops_repeats(self):
        text = "see https://a.com/x, then\nhttps://b.org/y?id=1\nhttps://a.com/x\n(https://c.net/z)"
        self.assertEqual(core.find_links(text), ["https://a.com/x", "https://b.org/y?id=1", "https://c.net/z"])

    def test_brackets_inside_a_link_stay(self):
        self.assertEqual(core.find_links("https://en.wikipedia.org/wiki/Foo_(bar)"),
                         ["https://en.wikipedia.org/wiki/Foo_(bar)"])

    def test_generic_prefix(self):
        self.assertEqual(core.find_links("generic:https://x.com/a"), ["generic:https://x.com/a"])
        self.assertEqual(core.split_prefix("generic:https://x.com/a"), (True, "https://x.com/a"))
        self.assertEqual(core.split_prefix("https://x.com/a"), (False, "https://x.com/a"))


class Folders(unittest.TestCase):
    def test_example_from_the_request(self):
        self.assertEqual(core.folder_parts("https://www.example.com/galleries/summer-2024/"),
                         ["example.com", "galleries", "summer-2024"])

    def test_page_extension_index_query_and_tracking(self):
        self.assertEqual(core.folder_parts("https://site.com/gallery/12/nice-title.html"),
                         ["site.com", "gallery", "12", "nice-title"])
        self.assertEqual(core.folder_parts("https://site.com/albums/index.php"), ["site.com", "albums"])
        self.assertEqual(core.folder_parts("https://site.com/view?id=5&utm_source=x&fbclid=y"),
                         ["site.com", "view", "id=5"])

    def test_unsafe_names(self):
        parts = core.folder_parts("https://site.com/a%3Ab/..//c%20d./%2E%2E/" + "x" * 300)
        self.assertEqual(parts[:3], ["site.com", "a_b", "c d"])
        self.assertTrue(all(p not in ("", ".", "..") and "/" not in p for p in parts))
        self.assertLessEqual(len(parts[-1].encode()), 100)

    def test_port_and_generic(self):
        self.assertEqual(core.folder_parts("generic:http://host.lan:8080/p/"), ["host.lan_8080", "p"])


class Metadata(unittest.TestCase):
    def test_generic_page(self):
        meta = {"title": "Summer 2024 at the lake", "keywords": "summer, lake, test",
                "og_site_name": "Test Site", "category": "generic"}
        self.assertEqual(core.pick_meta(meta), ("Summer 2024 at the lake", "Test Site", ["summer", "lake", "test"]))

    def test_nested_and_lists(self):
        meta = {"album": {"title": "Trip", "id": 3}, "user": {"name": "anna"},
                "tags": [{"name": "sea"}, "Sun", "sun", ["x"]]}
        self.assertEqual(core.pick_meta(meta), ("Trip", "anna", ["sea", "Sun", "x"]))

    def test_booru_style_tags(self):
        self.assertEqual(core.pick_meta({"tag_string": "blue_sky cloud"})[2], ["blue_sky", "cloud"])


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = os.path.join(self.tmp.name, "site.com", "g")
        self.incoming = os.path.join(self.folder, core.WORK_DIR, "incoming")
        os.makedirs(self.incoming)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, data=b"x"):
        path = os.path.join(self.incoming, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_numbered_in_reported_order(self):
        names = [core.place_new(self.put(n), self.folder, i, True) for i, n in enumerate(["z.PNG", "a.jpg"], 1)]
        self.assertEqual(names, ["001.png", "002.jpg"])
        self.assertEqual(sorted(os.listdir(self.folder)), [core.WORK_DIR, "001.png", "002.jpg"])

    def test_numbered_name_taken_by_another_picture(self):
        with open(os.path.join(self.folder, "001.png"), "wb") as f:
            f.write(b"other")
        self.assertEqual(core.place_new(self.put("z.png"), self.folder, 1, True), "001 (2).png")

    def test_unnumbered_duplicate_is_dropped(self):
        with open(os.path.join(self.folder, "a.jpg"), "wb") as f:
            f.write(b"x")
        self.assertEqual(core.place_new(self.put("a.jpg"), self.folder, 1, False), "a.jpg")
        self.assertEqual(os.listdir(self.incoming), [])

    def test_old_file_gets_its_number(self):
        with open(os.path.join(self.folder, "a.jpg"), "wb") as f:
            f.write(b"x")
        self.assertEqual(core.place_old(os.path.join(self.incoming, "a.jpg"), self.folder, 4, True), "004.jpg")
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "004.jpg")))

    def test_tidy_removes_only_kollect_leftovers(self):
        base = self.tmp.name
        open(os.path.join(self.folder, core.WORK_DIR, "archive.sqlite3"), "w").close()
        core.tidy(self.folder, base)
        self.assertFalse(os.path.exists(os.path.join(base, "site.com")))
        self.assertTrue(os.path.isdir(base))

    def test_tidy_keeps_galleries(self):
        open(os.path.join(self.folder, "001.jpg"), "w").close()
        core.tidy(self.folder, self.tmp.name)
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "001.jpg")))
        self.assertFalse(os.path.exists(self.incoming))


class Library(unittest.TestCase):
    def test_info_file_and_csv_row_replaced_on_repeat(self):
        with tempfile.TemporaryDirectory() as base:
            folder = os.path.join(base, "site.com", "g")
            os.makedirs(folder)
            files = [{"n": 1, "file": "001.png", "original": "z.png"}]
            for title in ("First", "Second"):
                info = core.write_gallery_info(folder, "https://www.site.com/g/", {"title": title, "tags": ["a"]},
                                               files, True, False)
                core.update_library(base, info)
            with open(os.path.join(folder, core.INFO_FILE), encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual((saved["title"], saved["site"], saved["images"]), ("Second", "site.com", 1))
            with open(os.path.join(base, core.LIBRARY_FILE), newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 1)
            self.assertEqual((rows[0]["Title"], rows[0]["Folder"], rows[0]["Tags"]),
                             ("Second", os.path.join("site.com", "g"), "a"))


class Times(unittest.TestCase):
    def test_next_time_today_or_tomorrow(self):
        now = time.mktime((2026, 11, 1, 10, 0, 0, 0, 0, -1))
        self.assertEqual(time.localtime(core.next_time(23, 30, now))[:5], (2026, 11, 1, 23, 30))
        self.assertEqual(time.localtime(core.next_time(9, 0, now))[:5], (2026, 11, 2, 9, 0))
        self.assertEqual(core.describe_time(core.next_time(9, 0, now), now), "tomorrow 09:00")


class Queue(unittest.TestCase):
    def test_order_states_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = core.Store(os.path.join(tmp, "q.json"))
            a = core.new_batch(["https://a.com/1", "https://a.com/2"], tmp, False)
            b = core.new_batch(["https://b.com/1"], tmp, True)
            a["state"] = "paused"
            store.batches += [a, b]
            self.assertIs(store.next_link()[1], b["links"][0])
            b["links"][0]["status"] = "running"
            b["state"], b["start_at"] = "scheduled", time.time() - 1
            store.save()
            again = core.Store(store.path).load()
            self.assertEqual(again.batches[1]["links"][0]["status"], "waiting")
            self.assertEqual([x["id"] for x in again.due()], [b["id"]])


if __name__ == "__main__":
    unittest.main()

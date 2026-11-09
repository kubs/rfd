import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from click.testing import CliRunner
from rfd.bookmarks import bookmark_path, load_bookmarks, toggle_bookmark
from rfd.threads import Thread
from rfd.interactive import Browser, ThreadFeed
from rfd.cli import cli


class BookmarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"XDG_DATA_HOME": self.tmp.name})
        self.env.start()
        self.a = Thread(
            "Coffee", "Shop", 12, "https://forums.redflagdeals.com/coffee-12345/", 80
        )
        self.b = Thread(
            "Tea", None, 3, "https://forums.redflagdeals.com/tea-67890/", 10
        )

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def browser(self):
        with patch("rfd.interactive._init_colors", return_value={}):
            return Browser(Mock(), ThreadFeed([self.a, self.b]))

    def test_persist_and_remove_by_id(self):
        self.assertTrue(toggle_bookmark(self.a))
        self.assertTrue(toggle_bookmark(self.b))
        self.assertEqual([t.title for t in load_bookmarks()], ["Tea", "Coffee"])
        self.a.url = "https://forums.redflagdeals.com/renamed-12345/"
        self.assertFalse(toggle_bookmark(self.a))
        self.assertEqual([t.title for t in load_bookmarks()], ["Tea"])

    def test_cli_offline(self):
        toggle_bookmark(self.a)
        result = CliRunner().invoke(cli, ["bookmarks", "--output", "json"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.output)[0]["title"], "Coffee")

    def test_corrupt_file_preserved(self):
        path = bookmark_path()
        path.parent.mkdir(parents=True)
        path.write_text("{broken")
        with self.assertRaises(ValueError):
            toggle_bookmark(self.a)
        self.assertEqual(path.read_text(), "{broken")
        result = CliRunner().invoke(cli, ["bookmarks"])
        self.assertNotEqual(result.exit_code, 0)

    def test_keys_and_return_to_feed(self):
        browser = self.browser()
        original = browser.feed
        browser.handle_key(ord("b"))
        browser.handle_key(ord("B"))
        self.assertTrue(browser.bookmarks_mode)
        self.assertEqual(len(browser.threads), 1)
        browser.handle_key(ord("*"))
        self.assertEqual(len(browser.threads), 0)
        self.assertTrue(browser.handle_key(ord("q")))
        self.assertIs(browser.feed, original)
        self.assertFalse(browser.bookmarks_mode)

    def test_star_from_open_thread_and_new_session(self):
        browser = self.browser()
        browser.thread = self.b
        browser.handle_key(ord("b"))
        fresh = self.browser()
        fresh.handle_key(ord("B"))
        self.assertEqual(fresh.threads[0].url, self.b.url)
        with patch("rfd.interactive.PostLoader") as loader:
            fresh.handle_key(10)
            loader.assert_called_once_with(self.b.url)
            self.assertEqual(fresh.thread.title, "Tea")


if __name__ == "__main__":
    unittest.main()

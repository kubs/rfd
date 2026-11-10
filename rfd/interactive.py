"""An interactive, vim-style browser for threads and their posts."""

import base64
import curses
import re
import shutil
import subprocess
import textwrap
import threading
import webbrowser

from .api import get_posts_page, resolve_post_id
from .images import supported_terminal, show_images
from .threads import sort_threads
from .bookmarks import load_bookmarks, toggle_bookmark, identity

SORT_KEYS = {"s": "score", "v": "views", "t": "title", "n": None}

# Sensible direction per field: the best deals first, but titles a to z.
SORT_DESCENDING = {"score": True, "views": True, "title": False, None: True}

# Stop chasing pages that match nothing after this many empty-handed fetches.
MAX_PAGES_PER_LOAD = 5

HELP = [
    ("Movement", ""),
    ("  j / k, down / up", "move down / up"),
    ("  ctrl-d / ctrl-u", "half page down / up"),
    ("  ctrl-f / ctrl-b", "full page down / up"),
    ("  g / G", "jump to first / last"),
    ("  <number>G", "jump to a line"),
    ("", ""),
    ("Threads", ""),
    ("  enter / o / l", "open the thread"),
    ("  O", "open the thread in a web browser"),
    ("  i / enter on image", "preview image in terminal"),
    ("  d / D", "open the deal link, cleaned / exactly as the forum gave it"),
    ("  y / Y", "yank the thread / deal url to the clipboard"),
    ("  b / *", "star / unstar this deal thread"),
    ("  B", "show saved deals / return to feed"),
    ("  p", "load the next page of threads"),
    ("  r", "reload from the first page"),
    ("", ""),
    ("Sort", ""),
    ("  s then s / v / t", "sort by score / views / title"),
    ("  s then n", "back to forum order"),
    ("  s then r", "reverse the current sort"),
    ("", ""),
    ("Search", ""),
    ("  /", "search titles (or post text)"),
    ("  n / N", "next / previous match"),
    ("", ""),
    ("Other", ""),
    ("  ?", "toggle this help"),
    ("  q / h / esc", "back, or quit from the thread list"),
    ("  Q", "quit"),
    ("  ctrl-l", "redraw"),
]

# Attribute keys, mapped to curses attributes once colors are initialised.
PLAIN, DIM, BOLD, GREEN, RED, BLUE, YELLOW, CYAN, HEADER = range(9)


def _init_colors():
    attrs = {key: curses.A_NORMAL for key in range(9)}
    attrs[DIM] = curses.A_DIM
    attrs[BOLD] = curses.A_BOLD
    attrs[HEADER] = curses.A_REVERSE | curses.A_BOLD
    try:
        curses.start_color()
        curses.use_default_colors()
    except curses.error:
        return attrs
    pairs = (
        (1, curses.COLOR_GREEN, GREEN),
        (2, curses.COLOR_RED, RED),
        (3, curses.COLOR_BLUE, BLUE),
        (4, curses.COLOR_YELLOW, YELLOW),
        (5, curses.COLOR_CYAN, CYAN),
    )
    for index, color, key in pairs:
        try:
            curses.init_pair(index, color, -1)
            attrs[key] = curses.color_pair(index)
        except curses.error:
            pass
    return attrs


def _score_attr(score):
    if score > 0:
        return GREEN
    if score < 0:
        return RED
    return BLUE


def copy_to_clipboard(text):
    """Copy to the system clipboard, falling back to an OSC 52 escape."""
    for command in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(command[0]):
            try:
                subprocess.run(command, input=text.encode(), check=True)
                return True
            except (OSError, subprocess.SubprocessError):
                continue
    try:
        encoded = base64.b64encode(text.encode()).decode()
        with open("/dev/tty", "w", encoding="utf-8") as tty:
            tty.write(f"\033]52;c;{encoded}\a")
        return True
    except OSError:
        return False


class PostLoader:
    """Loads the pages of a thread in the background so the ui stays responsive."""

    def __init__(self, url):
        self.post_id = resolve_post_id(url)
        self.posts = []
        self.links = []
        self.total_pages = None
        self.pages_loaded = 0
        self.error = None
        self.done = False
        self._lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        page = 1
        try:
            while True:
                posts, total_pages = get_posts_page(self.post_id, page)
                with self._lock:
                    if page == 1 and posts:
                        # The deal usually sits in the first post when the
                        # thread has no "get deal at ..." button.
                        self.links = posts[0].links
                    self.posts.extend(posts)
                    self.total_pages = total_pages
                    self.pages_loaded = page
                if page >= total_pages:
                    break
                page += 1
        except Exception as err:  # pylint: disable=broad-except
            with self._lock:
                self.error = str(err)
        finally:
            with self._lock:
                self.done = True

    def snapshot(self):
        with self._lock:
            return (
                list(self.posts),
                self.total_pages,
                self.pages_loaded,
                self.done,
                self.error,
            )


class Pane:
    """A scrollable list of lines with a cursor, driven by vim motions."""

    def __init__(self, height=1):
        self.cursor = 0
        self.top = 0
        self.height = height

    def clamp(self, count):
        self.cursor = max(0, min(self.cursor, count - 1)) if count else 0
        page = max(1, self.height)
        self.top = min(self.top, max(0, count - 1))
        self.top = min(self.top, self.cursor)
        self.top = max(self.top, self.cursor - page + 1)
        self.top = max(0, min(self.top, max(0, count - page)))

    def move(self, delta, count):
        self.cursor += delta
        self.clamp(count)

    def scroll(self, delta, count):
        """Scroll the viewport and drag the cursor along with it."""
        self.top = max(0, min(self.top + delta, max(0, count - 1)))
        self.cursor = max(self.top, min(self.cursor + delta, count - 1 if count else 0))
        self.clamp(count)


class ThreadFeed:  # pylint: disable=too-many-instance-attributes
    """The thread list, plus the paging and sorting applied to it.

    Pages are fetched on a worker thread but only ever merged in by the main
    thread in poll(), so the list the ui draws is never mutated underneath it.
    """

    def __init__(
        self, threads, load_page=None, pages_loaded=1, sort_by=None, reverse=None
    ):
        self.load_page = load_page
        self.page = pages_loaded
        self.sort_by = sort_by
        self.reverse = True if reverse is None else reverse
        self.exhausted = load_page is None
        self.loading = False
        self.error = None
        self.threads = []
        self.urls = set()
        self.arrival = {}
        self._pending = None
        self._lock = threading.Lock()
        self.add(threads)
        if sort_by is not None:
            if reverse is None:
                self.reverse = SORT_DESCENDING.get(sort_by, True)
            self.sort()

    def add(self, threads):
        """Append threads that are not already listed. Returns how many stuck.

        The forum reshuffles as people post, so the same thread can turn up on
        two different pages.
        """
        added = 0
        for thread in threads:
            if thread.url in self.urls:
                continue
            self.urls.add(thread.url)
            self.arrival[thread.url] = len(self.arrival)
            self.threads.append(thread)
            added += 1
        return added

    def sort(self):
        self.threads = sort_threads(self.threads, self.sort_by, reverse=self.reverse)

    def set_sort(self, sort_by, reverse):
        self.sort_by = sort_by
        self.reverse = reverse
        if sort_by is None:
            # Forum order is the order the pages arrived in.
            self.threads = sorted(self.threads, key=lambda t: self.arrival[t.url])
        else:
            self.sort()

    def can_load(self):
        # A failed page stops the automatic fetching; p retries it.
        return (
            bool(self.load_page)
            and not self.exhausted
            and not self.loading
            and self.error is None
        )

    def load_more(self):
        if not self.can_load():
            return
        self.loading = True
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        """Fetch pages until something new turns up or the forum runs out."""
        found, done, exhausted, error = [], self.page, False, None
        try:
            for offset in range(1, MAX_PAGES_PER_LOAD + 1):
                page = self.page + offset
                threads, topics_on_page = self.load_page(page)
                if not topics_on_page:
                    exhausted = True  # nothing on it, so it does not count
                    break
                done = page
                found.extend(threads)
                if any(thread.url not in self.urls for thread in threads):
                    break
        except Exception as err:  # pylint: disable=broad-except
            # Leave the counter on the last good page so p retries the failure.
            error = str(err)
        with self._lock:
            self._pending = (found, done, exhausted, error)

    def poll(self):
        """Merge a finished fetch. Returns True when the list changed."""
        with self._lock:
            pending, self._pending = self._pending, None
        if pending is None:
            return False
        found, page, exhausted, error = pending
        self.page = page
        self.exhausted = exhausted
        self.error = error
        self.loading = False
        if not self.add(found):
            return False
        if self.sort_by is not None:
            self.sort()
        return True


class Browser:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    def __init__(self, stdscr, feed, bookmarks_mode=False):
        self.stdscr = stdscr
        self.feed = feed
        self.attrs = _init_colors()
        self.list_pane = Pane()
        self.post_pane = Pane()
        self.thread = None
        self.loaders = {}
        self.post_lines = []
        self.image_rows = {}
        self.post_cache_key = None
        self.status = ""
        self.pattern = None
        self.pending_count = ""
        self.showing_help = False
        self.bookmarks_mode = bookmarks_mode
        self.previous_feed = None
        self.saved_ids = set()
        self.refresh_bookmarks()

    def refresh_bookmarks(self):
        try:
            saved = load_bookmarks()
            self.saved_ids = {identity(item.url) for item in saved}
            return saved
        except (OSError, ValueError) as err:
            self.status = f" bookmarks error: {err} "
            return None

    def toggle_star(self):
        thread = self.thread or (
            self.threads[self.list_pane.cursor] if self.threads else None
        )
        if thread is None:
            self.status = " no thread selected "
            return
        try:
            added = toggle_bookmark(thread)
        except (OSError, ValueError) as err:
            self.status = f" could not save bookmark: {err} "
            return
        saved = self.refresh_bookmarks()
        if self.bookmarks_mode and saved is not None:
            self.feed = ThreadFeed(
                saved, sort_by=self.feed.sort_by, reverse=self.feed.reverse
            )
            self.list_pane.clamp(len(self.threads))
        self.status = (
            " starred; B opens saved deals " if added else " removed bookmark "
        )

    def toggle_saved(self):
        if self.bookmarks_mode and self.previous_feed is not None:
            self.close_thread()
            self.feed, self.list_pane = self.previous_feed
            self.previous_feed = None
            self.bookmarks_mode = False
            return
        saved = self.refresh_bookmarks()
        if saved is None:
            return
        self.close_thread()
        if not self.bookmarks_mode:
            self.previous_feed = (self.feed, self.list_pane)
        self.feed = ThreadFeed(saved)
        self.list_pane = Pane()
        self.bookmarks_mode = True
        self.status = (
            " saved deals; b removes a star "
            if saved
            else " no saved deals yet; b stars a deal in the feed "
        )

    @property
    def threads(self):
        return self.feed.threads

    # -- drawing ---------------------------------------------------------

    def _put(self, y, x, text, attr_key=PLAIN, extra=0):
        """Write clipped text and return the next column."""
        height, width = self.stdscr.getmaxyx()
        if y < 0 or y >= height or x >= width - 1:
            return x
        text = text[: width - 1 - x]
        try:
            self.stdscr.addstr(y, x, text, self.attrs[attr_key] | extra)
        except curses.error:
            pass
        return x + len(text)

    def _bar(self, y, text, attr_key=HEADER):
        height, width = self.stdscr.getmaxyx()
        if y < 0 or y >= height:
            return
        self._put(y, 0, text.ljust(width - 1), attr_key)

    def draw(self):
        self.stdscr.erase()
        height, width = self.stdscr.getmaxyx()
        if height < 3 or width < 20:
            self._put(0, 0, "terminal too small")
            self.stdscr.refresh()
            return
        if self.thread is None:
            self.draw_threads(height, width)
        else:
            self.draw_posts(height, width)
        if self.showing_help:
            self.draw_help(height, width)
        self.stdscr.refresh()

    def draw_threads(self, height, width):
        self.list_pane.height = height - 2
        self.list_pane.clamp(len(self.threads))
        self._bar(
            0,
            f" rfd  {'Saved deals' if self.bookmarks_mode else 'Threads'}: {len(self.threads)}  {self.sort_label()} ",
        )
        for row in range(self.list_pane.height):
            index = self.list_pane.top + row
            if index >= len(self.threads):
                break
            self.draw_thread_row(row + 1, index, width)
        self._bar(height - 1, *self.list_footer())

    def sort_label(self):
        if self.feed.sort_by is None:
            return "sort: forum order"
        return f"sort: {self.feed.sort_by} {'desc' if self.feed.reverse else 'asc'}"

    def list_footer(self):
        if self.status:
            return self.status, DIM
        if self.feed.error:
            return f" could not load more threads: {self.feed.error} ", RED
        if self.feed.loading:
            return f" loading page {self.feed.page + 1}... ", DIM
        position = (
            f"{self.list_pane.cursor + 1}/{len(self.threads)}"
            if self.threads
            else "0/0"
        )
        pages = f"{self.feed.page} page{'s' if self.feed.page != 1 else ''}"
        if self.bookmarks_mode:
            pages = "saved deals"
        elif self.feed.exhausted:
            pages += ", end of forum"
        return (
            f" {position}  {pages}   j/k move  enter open  b star  B saved  s sort  / search  ? help  q quit ",
            HEADER,
        )

    def draw_thread_row(self, y, index, width):
        thread = self.threads[index]
        selected = index == self.list_pane.cursor
        extra = curses.A_REVERSE if selected else 0
        if selected:
            self._put(y, 0, " " * (width - 1), PLAIN, extra)
        x = self._put(y, 0, f"{index + 1:>4}.", DIM, extra)
        x = self._put(
            y,
            x,
            " *" if identity(thread.url) in self.saved_ids else "  ",
            YELLOW,
            extra,
        )
        x = self._put(y, x, f" [{thread.score:+d}] ", _score_attr(thread.score), extra)
        if thread.dealer_name:
            x = self._put(y, x, f"[{thread.dealer_name}] ", CYAN, extra)
        x = self._put(y, x, thread.title, BOLD if selected else PLAIN, extra)
        self._put(y, x, f" ({thread.views} views)", YELLOW, extra)

    def draw_posts(self, height, width):
        # Links get their own rows under the title: the deal, then the thread.
        deal = self.deal_line()
        body_top = 3 if deal else 2
        self.post_pane.height = height - body_top - 1
        posts, total_pages, pages_loaded, done, error = self.loader.snapshot()
        self.render_posts(posts, width)
        self.post_pane.clamp(len(self.post_lines))
        self._bar(
            0,
            f" {'* ' if identity(self.thread.url) in self.saved_ids else ''}{self.thread.title} ",
        )
        row = 1
        if deal:
            label, url = deal
            x = self._put(row, 0, f" {label} ", BOLD)
            self._put(row, x + 1, url, BLUE)
            row += 1
        x = self._put(row, 0, " rfd  ", DIM)
        self._put(row, x + 1, self.thread.url, DIM)
        for row in range(self.post_pane.height):
            index = self.post_pane.top + row
            if index >= len(self.post_lines):
                break
            text, attr_key = self.post_lines[index]
            selected = index == self.post_pane.cursor
            extra = curses.A_REVERSE if selected else 0
            if selected:
                self._put(row + body_top, 0, " " * (width - 1), PLAIN, extra)
            self._put(row + body_top, 0, text, attr_key, extra)
        if self.status:
            footer, attr = self.status, DIM
        elif error:
            footer, attr = f" error: {error} ", RED
        elif not done:
            loading = f"{pages_loaded}/{total_pages}" if total_pages else "..."
            footer, attr = f" loading page {loading}   {len(posts)} posts so far ", DIM
        else:
            hint = "  d deal" if deal else ""
            footer = f" {len(posts)} posts   j/k scroll{hint}  i image  O rfd page  b star  B saved  q back "
            attr = HEADER
        self._bar(height - 1, footer, attr)

    def draw_help(self, height, width):
        box_width = min(width - 4, 64)
        box_height = min(height - 2, len(HELP) + 2)
        top = max(0, (height - box_height) // 2)
        left = max(0, (width - box_width) // 2)
        for row in range(box_height):
            self._put(top + row, left, " " * box_width, PLAIN, curses.A_REVERSE)
        self._put(top, left + 2, " keys ", BOLD, curses.A_REVERSE)
        for row, (keys, description) in enumerate(HELP[: box_height - 2], start=1):
            self._put(top + row, left + 2, keys, BOLD, curses.A_REVERSE)
            self._put(top + row, left + 2 + 22, description, PLAIN, curses.A_REVERSE)

    def render_posts(self, posts, width):
        """Wrap posts into display lines, recomputing only when needed."""
        key = (len(posts), width)
        if key == self.post_cache_key:
            return
        self.post_cache_key = key
        indent = "      "
        wrap_width = max(20, width - len(indent) - 2)
        lines = []
        self.image_rows = {}
        for count, post in enumerate(posts, 1):
            lines.append(
                (f"{count:>4}. [{post.score:+d}] {post.user}", _score_attr(post.score))
            )
            for paragraph in post.body.splitlines():
                paragraph = paragraph.rstrip()
                if not paragraph.strip():
                    lines.append(("", PLAIN))
                    continue
                for line in textwrap.wrap(paragraph, wrap_width):
                    lines.append((indent + line, PLAIN))
            for number, url in enumerate(post.images, 1):
                for line in textwrap.wrap(f"[image {number}] {url} (enter / i to view)", wrap_width):
                    self.image_rows[len(lines)] = url
                    lines.append((indent + line, CYAN))
            lines.append((indent + "-" * wrap_width, DIM))
        self.post_lines = lines

    # -- state -----------------------------------------------------------

    @property
    def loader(self):
        return self.loaders[self.thread.url]

    @property
    def pane(self):
        return self.list_pane if self.thread is None else self.post_pane

    @property
    def count(self):
        return len(self.threads) if self.thread is None else len(self.post_lines)

    def haystack(self, index):
        if self.thread is None:
            thread = self.threads[index]
            return f"{thread.dealer_name or ''} {thread.title}"
        return self.post_lines[index][0]

    def open_thread(self):
        if not self.threads:
            return
        self.thread = self.threads[self.list_pane.cursor]
        if self.thread.url not in self.loaders:
            try:
                self.loaders[self.thread.url] = PostLoader(self.thread.url)
            except ValueError:
                self.thread = None
                self.status = " cannot parse a thread id from that url "
                return
        self.post_pane = Pane()
        self.post_cache_key = None
        self.post_lines = []
        self.image_rows = {}

    def close_thread(self):
        self.thread = None
        self.post_lines = []
        self.image_rows = {}
        self.post_cache_key = None

    def anchor(self):
        """Remember the thread under the cursor, so a re-sort can keep it there."""
        if self.threads and self.list_pane.cursor < len(self.threads):
            return self.threads[self.list_pane.cursor].url
        return None

    def restore(self, url):
        if url is not None:
            for index, thread in enumerate(self.threads):
                if thread.url == url:
                    self.list_pane.cursor = index
                    break
        self.list_pane.clamp(len(self.threads))

    def choose_sort(self):
        """s, then one key. Beats cycling once there is more than one axis."""
        self._bar(
            self.stdscr.getmaxyx()[0] - 1,
            " sort by  [s]core  [v]iews  [t]itle  [n]one     [r]everse ",
            PLAIN,
        )
        self.stdscr.refresh()
        key = self.stdscr.getch()
        if key in (-1, 27):
            return
        char = chr(key) if 0 <= key < 256 else ""
        anchored = self.anchor()
        if char == "r":
            self.feed.set_sort(self.feed.sort_by, not self.feed.reverse)
        elif char in SORT_KEYS:
            sort_by = SORT_KEYS[char]
            self.feed.set_sort(sort_by, SORT_DESCENDING[sort_by])
        else:
            return
        self.restore(anchored)
        self.status = f" {self.sort_label()} "

    def load_more(self, announce=False):
        if self.feed.loading:
            return
        if self.feed.exhausted:
            if announce:
                self.status = " already at the end of the forum "
            return
        if not self.feed.load_page:
            if announce:
                self.status = " nothing more to load "
            return
        if announce:
            self.feed.error = None  # an explicit p retries a failed page
        self.feed.load_more()

    def maybe_load_more(self):
        """Fetch ahead once the cursor reaches the last screenful."""
        if (
            self.thread is None
            and self.list_pane.cursor >= len(self.threads) - self.list_pane.height
        ):
            self.load_more()

    def reload(self):
        if not self.feed.load_page:
            self.status = " nothing to reload "
            return
        self._bar(self.stdscr.getmaxyx()[0] - 1, " reloading... ", DIM)
        self.stdscr.refresh()
        anchored = self.anchor()
        try:
            threads, _ = self.feed.load_page(1)
        except Exception as err:  # pylint: disable=broad-except
            self.status = f" reload failed: {err} "
            return
        self.feed = ThreadFeed(
            threads,
            load_page=self.feed.load_page,
            pages_loaded=1,
            sort_by=self.feed.sort_by,
            reverse=self.feed.reverse,
        )
        self.restore(anchored)
        self.status = f" reloaded {len(self.threads)} threads "

    # -- search ----------------------------------------------------------

    def prompt(self, prefix):
        height, _ = self.stdscr.getmaxyx()
        buffer = ""
        while True:
            self._bar(height - 1, prefix + buffer, PLAIN)
            self.stdscr.refresh()
            key = self.stdscr.getch()
            if key in (curses.KEY_ENTER, 10, 13):
                return buffer
            if key == 27:
                return None
            if key in (curses.KEY_BACKSPACE, 127, 8):
                if not buffer:
                    return None
                buffer = buffer[:-1]
            elif key == -1:
                continue
            elif 32 <= key < 127:
                buffer += chr(key)

    def search(self):
        pattern = self.prompt("/")
        if not pattern:
            return
        try:
            self.pattern = re.compile(pattern, re.IGNORECASE)
        except re.error as err:
            self.status = f" bad pattern: {err} "
            return
        self.jump_to_match(1)

    def jump_to_match(self, direction):
        if self.pattern is None:
            self.status = " no previous search "
            return
        count = self.count
        if not count:
            return
        start = self.pane.cursor
        for offset in range(1, count + 1):
            index = (start + direction * offset) % count
            if self.pattern.search(self.haystack(index)):
                self.pane.cursor = index
                self.pane.clamp(count)
                self.status = ""
                return
        self.status = f" pattern not found: {self.pattern.pattern} "

    # -- input -----------------------------------------------------------

    def deal_line(self):
        """(label, url) for the deal link, or None when the thread has none.

        A thread either has a "get deal at ..." button, or the deal is a link
        in the first post; both arrive wrapped in affiliate hops.
        """
        thread = self.thread
        if thread is None and self.threads:
            thread = self.threads[self.list_pane.cursor]
        if thread is None:
            return None
        if thread.deal_url:
            return "deal", thread.deal_url
        if self.thread is not None and self.loader.links:
            return "link", self.loader.links[0]
        return None

    def current_deal(self):
        line = self.deal_line()
        return line[1] if line else ""

    def open_deal(self, original=False):
        """d opens the cleaned link, D the one the forum handed out."""
        line = self.deal_line()
        if not line:
            self.status = " no deal link on this thread "
            return
        url = line[1]
        if original:
            thread = self.thread or self.threads[self.list_pane.cursor]
            url = thread.raw_deal_url or url
        webbrowser.open(url)
        self.status = f" opened {url} "

    def open_image(self):
        url = self.image_rows.get(self.post_pane.cursor) if self.thread else None
        if not url:
            self.status = " select an image row with j/k, then press i or enter "
            return
        if supported_terminal():
            urls = list(dict.fromkeys(self.image_rows.values()))
            try:
                show_images(self.stdscr, urls, urls.index(url))
            except (OSError, ValueError) as err:
                self.status = f" could not preview image: {err} "
            return
        try:
            opened = webbrowser.open(url)
            self.status = f" opened {url} " if opened else " could not open a web browser "
        except (OSError, webbrowser.Error) as err:
            self.status = f" could not open image: {err} "

    def current_url(self):
        if self.thread is not None:
            return self.thread.url
        if self.threads:
            return self.threads[self.list_pane.cursor].url
        return None

    def handle_key(self, key):  # pylint: disable=too-many-branches
        """Return False to exit."""
        if self.showing_help:
            self.showing_help = False
            return True

        self.status = ""
        pane, count = self.pane, self.count
        half = max(1, pane.height // 2)

        if ord("0") <= key <= ord("9") and not (
            key == ord("0") and not self.pending_count
        ):
            self.pending_count += chr(key)
            return True
        repeat = int(self.pending_count) if self.pending_count else None
        self.pending_count = ""

        if key in (ord("j"), curses.KEY_DOWN):
            pane.move(repeat or 1, count)
        elif key in (ord("k"), curses.KEY_UP):
            pane.move(-(repeat or 1), count)
        elif key == 4:  # ctrl-d
            pane.scroll(half, count)
        elif key == 21:  # ctrl-u
            pane.scroll(-half, count)
        elif key in (6, curses.KEY_NPAGE):  # ctrl-f
            pane.scroll(max(1, pane.height - 2), count)
        elif key in (2, curses.KEY_PPAGE):  # ctrl-b
            pane.scroll(-max(1, pane.height - 2), count)
        elif key == ord("g"):
            pane.cursor = (repeat - 1) if repeat else 0
            pane.clamp(count)
        elif key == ord("G"):
            pane.cursor = (repeat - 1) if repeat else max(0, count - 1)
            pane.clamp(count)
        elif key in (curses.KEY_HOME,):
            pane.cursor = 0
            pane.clamp(count)
        elif key in (curses.KEY_END,):
            pane.cursor = max(0, count - 1)
            pane.clamp(count)
        elif key == ord("/"):
            self.search()
        elif key == ord("?"):
            self.showing_help = True
        elif key == ord("n"):
            self.jump_to_match(1)
        elif key == ord("N"):
            self.jump_to_match(-1)
        elif key in (curses.KEY_ENTER, 10, 13, ord("o"), ord("l"), curses.KEY_RIGHT):
            if self.thread is None:
                self.open_thread()
            else:
                self.open_image()
        elif key == ord("i"):
            self.open_image()
        elif key == ord("O"):
            url = self.current_url()
            if url:
                webbrowser.open(url)
                self.status = f" opened {url} "
        elif key == ord("d"):
            self.open_deal()
        elif key == ord("D"):
            self.open_deal(original=True)
        elif key == ord("Y"):
            deal = self.current_deal()
            if not deal:
                self.status = " no deal link on this thread "
            else:
                self.status = (
                    f" yanked {deal} "
                    if copy_to_clipboard(deal)
                    else " could not reach a clipboard "
                )
        elif key == ord("y"):
            url = self.current_url()
            if url:
                self.status = (
                    f" yanked {url} "
                    if copy_to_clipboard(url)
                    else " could not reach a clipboard "
                )
        elif key in (ord("b"), ord("*")):
            self.toggle_star()
        elif key == ord("B"):
            self.toggle_saved()
        elif key == ord("s") and self.thread is None:
            self.choose_sort()
        elif key == ord("p") and self.thread is None:
            self.load_more(announce=True)
        elif key == ord("r") and self.thread is None:
            self.reload()
        elif key == 12:  # ctrl-l
            self.stdscr.clearok(True)
        elif key == ord("Q"):
            return False
        elif key in (ord("q"), ord("h"), 27, curses.KEY_LEFT):
            if self.thread is None:
                if self.previous_feed is not None:
                    self.toggle_saved()
                    return True
                return False
            self.close_thread()
        elif key == curses.KEY_RESIZE:
            self.on_resize()
        self.maybe_load_more()
        return True

    def on_resize(self):
        try:
            curses.update_lines_cols()
        except (AttributeError, curses.error):
            pass
        self.post_cache_key = None
        self.stdscr.clearok(True)

    def run(self):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self.stdscr.keypad(True)
        while True:
            if self.feed.poll():
                self.list_pane.clamp(len(self.threads))
                # Keep filling until the list is at least a screen deep.
                self.maybe_load_more()
            self.draw()
            # Poll so background post pages appear as they arrive.
            busy = self.feed.loading or (
                self.thread is not None and not self.loader.done
            )
            self.stdscr.timeout(200 if busy else -1)
            key = self.stdscr.getch()
            if key == -1:
                continue
            if not self.handle_key(key):
                return


def browse(threads, sort_by=None, load_page=None, pages_loaded=1, bookmarks_mode=False):
    """Browse threads interactively. Blocks until the user quits.

    load_page(page) should return (threads, topics_on_page); the second value
    is what tells the feed the forum has run out, since the api reports a
    total_pages of -1.
    """
    feed = ThreadFeed(
        threads, load_page=load_page, pages_loaded=pages_loaded, sort_by=sort_by
    )
    curses.wrapper(
        lambda stdscr: Browser(stdscr, feed, bookmarks_mode=bookmarks_mode).run()
    )

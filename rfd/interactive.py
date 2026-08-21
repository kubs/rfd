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
from .threads import sort_threads

SORT_ORDER = [None, "score", "views", "title"]

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
    ("  y", "yank the thread url to the clipboard"),
    ("  s", "cycle sort (none, score, views, title)"),
    ("  r", "reload threads"),
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


class Browser:  # pylint: disable=too-many-instance-attributes
    def __init__(self, stdscr, threads, sort_by=None, reload_threads=None):
        self.stdscr = stdscr
        self.threads = list(threads)
        self.sort_by = sort_by
        self.reload_threads = reload_threads
        self.attrs = _init_colors()
        self.list_pane = Pane()
        self.post_pane = Pane()
        self.thread = None
        self.loaders = {}
        self.post_lines = []
        self.post_cache_key = None
        self.status = ""
        self.pattern = None
        self.pending_count = ""
        self.showing_help = False

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
        sort_by = self.sort_by or "none"
        self._bar(
            0,
            f" rfd  {len(self.threads)} threads  sort: {sort_by} ",
        )
        for row in range(self.list_pane.height):
            index = self.list_pane.top + row
            if index >= len(self.threads):
                break
            self.draw_thread_row(row + 1, index, width)
        position = (
            f"{self.list_pane.cursor + 1}/{len(self.threads)}"
            if self.threads
            else "0/0"
        )
        self._bar(
            height - 1,
            self.status
            or f" {position}   j/k move  enter open  O browser  / search  ? help  q quit ",
            DIM if self.status else HEADER,
        )

    def draw_thread_row(self, y, index, width):
        thread = self.threads[index]
        selected = index == self.list_pane.cursor
        extra = curses.A_REVERSE if selected else 0
        if selected:
            self._put(y, 0, " " * (width - 1), PLAIN, extra)
        x = self._put(y, 0, f"{index + 1:>4}.", DIM, extra)
        x = self._put(y, x, f" [{thread.score:+d}] ", _score_attr(thread.score), extra)
        if thread.dealer_name:
            x = self._put(y, x, f"[{thread.dealer_name}] ", CYAN, extra)
        x = self._put(y, x, thread.title, BOLD if selected else PLAIN, extra)
        self._put(y, x, f" ({thread.views} views)", YELLOW, extra)

    def draw_posts(self, height, width):
        self.post_pane.height = height - 2
        posts, total_pages, pages_loaded, done, error = self.loader.snapshot()
        self.render_posts(posts, width)
        self.post_pane.clamp(len(self.post_lines))
        self._bar(0, f" {self.thread.title} ")
        for row in range(self.post_pane.height):
            index = self.post_pane.top + row
            if index >= len(self.post_lines):
                break
            text, attr_key = self.post_lines[index]
            selected = index == self.post_pane.cursor
            extra = curses.A_REVERSE if selected else 0
            if selected:
                self._put(row + 1, 0, " " * (width - 1), PLAIN, extra)
            self._put(row + 1, 0, text, attr_key, extra)
        if error:
            footer, attr = f" error: {error} ", RED
        elif not done:
            loading = f"{pages_loaded}/{total_pages}" if total_pages else "..."
            footer, attr = f" loading page {loading}   {len(posts)} posts so far ", DIM
        else:
            footer = f" {len(posts)} posts   j/k scroll  O browser  y yank  q back "
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

    def close_thread(self):
        self.thread = None
        self.post_lines = []
        self.post_cache_key = None

    def cycle_sort(self):
        self.sort_by = SORT_ORDER[
            (SORT_ORDER.index(self.sort_by) + 1) % len(SORT_ORDER)
        ]
        if self.sort_by is not None:
            self.threads = sort_threads(self.threads, sort_by=self.sort_by)
        elif self.reload_threads:
            self.threads = list(self.reload_threads())
        self.list_pane = Pane()

    def reload(self):
        if not self.reload_threads:
            self.status = " nothing to reload "
            return
        self._bar(self.stdscr.getmaxyx()[0] - 1, " reloading... ", DIM)
        self.stdscr.refresh()
        try:
            self.threads = sort_threads(
                list(self.reload_threads()), sort_by=self.sort_by
            )
        except Exception as err:  # pylint: disable=broad-except
            self.status = f" reload failed: {err} "
            return
        self.list_pane.clamp(len(self.threads))
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
        elif key == ord("O"):
            url = self.current_url()
            if url:
                webbrowser.open(url)
                self.status = f" opened {url} "
        elif key == ord("y"):
            url = self.current_url()
            if url:
                self.status = (
                    f" yanked {url} "
                    if copy_to_clipboard(url)
                    else " could not reach a clipboard "
                )
        elif key == ord("s") and self.thread is None:
            self.cycle_sort()
        elif key == ord("r") and self.thread is None:
            self.reload()
        elif key == 12:  # ctrl-l
            self.stdscr.clearok(True)
        elif key == ord("Q"):
            return False
        elif key in (ord("q"), ord("h"), 27, curses.KEY_LEFT):
            if self.thread is None:
                return False
            self.close_thread()
        elif key == curses.KEY_RESIZE:
            self.on_resize()
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
            self.draw()
            # Poll so background post pages appear as they arrive.
            self.stdscr.timeout(
                200 if self.thread is not None and not self.loader.done else -1
            )
            key = self.stdscr.getch()
            if key == -1:
                continue
            if not self.handle_key(key):
                return


def browse(threads, sort_by=None, reload_threads=None):
    """Browse threads interactively. Blocks until the user quits."""
    curses.wrapper(
        lambda stdscr: Browser(stdscr, threads, sort_by, reload_threads).run()
    )

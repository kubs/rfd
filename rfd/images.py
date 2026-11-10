"""On-demand image previews using the Kitty terminal graphics protocol."""

import base64
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
import curses
import io
import os
import struct
import sys
import webbrowser

from PIL import Image, ImageOps

from .api import get_session

IMAGE_ID = 73421
MAX_BYTES = 20 * 1024 * 1024


def supported_terminal():
    """Conservatively fall back for terminals/multiplexers we cannot render in."""
    return (
        sys.stdout.isatty()
        and not os.environ.get("TMUX")
        and not os.environ.get("STY")
        and (os.environ.get("TERM_PROGRAM") in ("ghostty", "kitty")
             or os.environ.get("TERM") in ("xterm-ghostty", "xterm-kitty"))
    )


def load_image(url):
    """Download and decode one image with bounded network and pixel sizes."""
    data = bytearray()
    with get_session().get(url, stream=True, timeout=(5, 15)) as response:
        response.raise_for_status()
        for chunk in response.iter_content(65536):
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise ValueError("image exceeds 20 MB; use O to open in browser")
    with Image.open(io.BytesIO(data)) as source:
        if source.width * source.height > 25_000_000:
            raise ValueError("image is too large to preview; use O to open in browser")
        # Animated images use the first frame.
        result = ImageOps.exif_transpose(source).convert("RGBA")
        result.thumbnail((2560, 2560))
        return result


def cell_size():
    try:
        import fcntl
        import termios
        rows, cols, width, height = struct.unpack(
            "HHHH", fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
        )
        if rows and cols and width and height:
            return width / cols, height / rows
    except (ImportError, OSError, ValueError):
        pass
    return 10, 20


def image_commands(image, columns, rows):
    """Fit to the viewport, then transmit PNG in protocol-sized chunks."""
    cell_width, cell_height = cell_size()
    fitted = image.copy()
    fitted.thumbnail((max(1, int(columns * cell_width)), max(1, int(rows * cell_height))))
    buffer = io.BytesIO()
    fitted.save(buffer, format="PNG")
    payload = base64.b64encode(buffer.getvalue())
    for offset in range(0, len(payload), 4096):
        chunk = payload[offset:offset + 4096]
        more = int(offset + 4096 < len(payload))
        params = f"a=T,f=100,t=d,i={IMAGE_ID},q=2,C=1," if offset == 0 else ""
        yield f"\033_G{params}m={more};".encode() + chunk + b"\033\\"


def clear_image():
    sys.stdout.write(f"\033_Ga=d,d=I,i={IMAGE_ID},q=2\033\\")
    sys.stdout.flush()


def show_images(screen, urls, selected):
    """Modal preview; leave the caller's scroll position untouched."""
    cache = OrderedDict()
    executor = ThreadPoolExecutor(max_workers=1)
    pending = None
    current = None
    dirty = True
    message = ""
    try:
        screen.timeout(100)
        while True:
            url = urls[selected]
            if current != url:
                if pending is not None:
                    pending.cancel()
                current = url
                pending = None if url in cache else executor.submit(load_image, url)
                message = "" if url in cache else "Loading image..."
                dirty = True
            if pending is not None and pending.done():
                try:
                    cache[url] = pending.result()
                    while len(cache) > 3:
                        cache.popitem(last=False)
                    message = ""
                except Exception as err:  # pylint: disable=broad-except
                    message = f"Cannot preview: {err}"
                pending = None
                dirty = True
            if dirty:
                clear_image()
                screen.erase()
                height, width = screen.getmaxyx()
                def put(row, text):
                    try:
                        screen.addnstr(row, 0, text, max(0, width - 1))
                    except curses.error:
                        pass
                put(0, f" Image {selected + 1}/{len(urls)}  {url}")
                if message:
                    put(2, message)
                put(height - 1, " h/l previous/next   O browser   q/esc back")
                screen.refresh()
                if url in cache and height > 3 and width > 2:
                    sys.stdout.write("\033[2;1H")
                    sys.stdout.flush()
                    for command in image_commands(cache[url], width - 1, height - 2):
                        sys.stdout.buffer.write(command)
                    sys.stdout.buffer.flush()
                dirty = False
            key = screen.getch()
            if key in (ord("q"), ord("Q"), 27):
                break
            if key in (ord("l"), ord("j"), curses.KEY_RIGHT, curses.KEY_DOWN):
                selected = (selected + 1) % len(urls)
            elif key in (ord("h"), ord("k"), curses.KEY_LEFT, curses.KEY_UP):
                selected = (selected - 1) % len(urls)
            elif key == ord("O"):
                try:
                    message = "Opened in browser" if webbrowser.open(url) else "Could not open browser"
                except (OSError, webbrowser.Error) as err:
                    message = str(err)
                dirty = True
            elif key in (curses.KEY_RESIZE, 12):
                dirty = True
    finally:
        if pending is not None:
            pending.cancel()
        executor.shutdown(wait=False)
        clear_image()
        screen.timeout(-1)
        screen.clearok(True)
        screen.touchwin()

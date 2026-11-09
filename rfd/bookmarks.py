"""Persistent local thread bookmarks, shared by the CLI and browser."""

import json
import os
import re
import tempfile
from pathlib import Path
from contextlib import contextmanager

from .threads import Thread


def bookmark_path():
    return (
        Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
        / "rfd/bookmarks.json"
    )


def identity(url):
    match = re.search(r"-(\d+)/?$", url.split("?")[0].split("#")[0])
    return match.group(1) if match else url.rstrip("/")


def load_bookmarks():
    path = bookmark_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("expected a list")
        result = [Thread(**item) for item in data]
        for item in result:
            if not isinstance(item.url, str) or not isinstance(item.title, str):
                raise ValueError("invalid title or URL")
            if not isinstance(item.score, int) or not isinstance(item.views, int):
                raise ValueError("invalid score or views")
        return result
    except FileNotFoundError:
        return []
    except (ValueError, TypeError) as err:
        raise ValueError(f"Cannot read bookmarks in {path}: {err}") from err


@contextmanager
def _locked(path):
    # Serialize read-modify-write across simultaneous terminal sessions.
    import fcntl  # pylint: disable=import-outside-toplevel

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def toggle_bookmark(thread):
    path = bookmark_path()
    with _locked(path):
        saved = load_bookmarks()
        key = identity(thread.url)
        exists = any(identity(item.url) == key for item in saved)
        saved = [item for item in saved if identity(item.url) != key]
        if not exists:
            saved.insert(0, thread)
        name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False
            ) as out:
                name = out.name
                json.dump(
                    [vars(item) for item in saved], out, ensure_ascii=False, indent=2
                )
                out.write("\n")
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, path)
        finally:
            if name and os.path.exists(name):
                os.unlink(name)
    return not exists

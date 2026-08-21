import pytest

from rfd.api import resolve_post_id
from rfd.interactive import Pane


def test_resolve_post_id():
    assert (
        resolve_post_id("https://forums.redflagdeals.com/kobo-vs-kindle-2396227/")
        == "2396227"
    )
    assert resolve_post_id("2396227") == "2396227"


def test_resolve_post_id_invalid():
    with pytest.raises(ValueError):
        resolve_post_id("not-a-thread")


def test_pane_moves_within_bounds():
    pane = Pane(height=10)
    pane.move(5, 20)
    assert pane.cursor == 5
    pane.move(-100, 20)
    assert pane.cursor == 0
    pane.move(100, 20)
    assert pane.cursor == 19


def test_pane_scrolls_the_viewport_with_the_cursor():
    pane = Pane(height=10)
    pane.scroll(5, 40)
    assert (pane.top, pane.cursor) == (5, 5)
    pane.scroll(-100, 40)
    assert (pane.top, pane.cursor) == (0, 0)


def test_pane_keeps_the_cursor_visible():
    pane = Pane(height=10)
    pane.cursor = 30
    pane.clamp(40)
    assert pane.top <= pane.cursor < pane.top + pane.height


def test_pane_handles_an_empty_list():
    pane = Pane(height=10)
    pane.move(3, 0)
    assert (pane.top, pane.cursor) == (0, 0)

import pytest

from rfd.api import resolve_post_id
from rfd.interactive import SORT_DESCENDING, Pane, ThreadFeed
from rfd.threads import Thread


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


def make_thread(title, score=0, views=0, url=None):
    return Thread(
        title=title,
        dealer_name=None,
        score=score,
        url=url or f"https://forums.redflagdeals.com/{title}/",
        views=views,
    )


def test_feed_ignores_threads_it_already_lists():
    feed = ThreadFeed([make_thread("a"), make_thread("b")])
    assert feed.add([make_thread("b"), make_thread("c")]) == 1
    assert [t.title for t in feed.threads] == ["a", "b", "c"]


def test_feed_sorts_titles_ascending_but_scores_descending():
    feed = ThreadFeed([make_thread("b", score=1), make_thread("a", score=9)])
    feed.set_sort("title", SORT_DESCENDING["title"])
    assert [t.title for t in feed.threads] == ["a", "b"]
    feed.set_sort("score", SORT_DESCENDING["score"])
    assert [t.score for t in feed.threads] == [9, 1]


def test_feed_returns_to_the_order_pages_arrived_in():
    feed = ThreadFeed([make_thread("b", score=1), make_thread("a", score=9)])
    feed.set_sort("score", True)
    feed.set_sort(None, True)
    assert [t.title for t in feed.threads] == ["b", "a"]


def test_feed_pages_past_pages_that_match_nothing():
    def load_page(page):
        if page > 4:
            return [], 0
        if page == 4:
            return [make_thread(f"page{page}")], 1
        return [], 1  # the page had topics, none of them matched

    feed = ThreadFeed([make_thread("first")], load_page=load_page)
    feed._fetch()  # pylint: disable=protected-access
    assert feed.poll() is True
    assert [t.title for t in feed.threads] == ["first", "page4"]
    assert feed.page == 4


def test_feed_stops_at_the_end_of_the_forum():
    feed = ThreadFeed([make_thread("first")], load_page=lambda page: ([], 0))
    feed._fetch()  # pylint: disable=protected-access
    feed.poll()
    assert feed.exhausted is True
    assert feed.page == 1


def test_feed_keeps_its_page_when_a_fetch_fails():
    def load_page(page):
        raise RuntimeError("connection reset")

    feed = ThreadFeed([make_thread("first")], load_page=load_page)
    feed._fetch()  # pylint: disable=protected-access
    feed.poll()
    assert feed.error == "connection reset"
    assert feed.page == 1
    assert feed.can_load() is False  # p retries, movement does not

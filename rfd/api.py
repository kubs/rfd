"""RFD API."""

try:
    from json.decoder import JSONDecodeError
except ImportError:
    JSONDecodeError = ValueError
import logging
import threading
import requests
from . import API_BASE_URL
from .posts import Post
from .scores import calculate_score
from .utils import is_int, strip_html, is_valid_url

USER_AGENT = "rfd (+https://github.com/davegallant/rfd)"

# The forums reject the default requests user agent with a 406. A session per
# thread keeps connections warm without sharing one across the background
# loader used by interactive mode.
_local = threading.local()


def get_session():
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        _local.session = session
    return session


def extract_post_id(url):
    return url.split("/")[3].split("-")[-1]


def get_safe_per_page(limit):
    """Ensure that per page limit is between 5-40"""
    if limit < 5:
        return 5
    if limit > 40:
        return 40
    return limit


def create_user_map(users):
    """Create a map of user ids to usernames."""
    m = {}
    for user in users:
        m[user.get("user_id")] = user.get("username")
    return m


def get_threads(forum_id, pages):
    """Get threads from rfd api

    Arguments:
        forum_id {int} -- forum id
        pages {int} -- the number of pages of threads to collect

    Returns:
        dict -- api response
    """
    threads = []
    try:
        for page in range(1, pages + 1):
            response = get_session().get(
                f"{API_BASE_URL}/api/topics?forum_id={forum_id}&per_page=40&page={page}"
            )
            if response.status_code != 200:
                raise Exception(
                    f"When collecting threads, received a status code: {response.status_code}"
                )
            threads += response.json().get("topics")
    except JSONDecodeError as err:
        logging.error("Unable to decode threads. %s", err)
    return threads


def resolve_post_id(post):
    """Accept either a full url or a post id and return the post id."""
    if is_valid_url(post):
        return extract_post_id(post)
    if is_int(post):
        return post
    raise ValueError()


def get_posts_page(post, page):
    """Retrieve a single page of posts from a thread.

    Args:
        post (str): either full url or postid
        page (int): the page to retrieve (1 indexed)

    Returns:
        (list(Post), int): the posts on that page and the total page count
    """
    post_id = resolve_post_id(post)

    response = get_session().get(
        f"{API_BASE_URL}/api/topics/{post_id}/posts?per_page=40&page={page}"
    )
    if response.status_code != 200:
        raise Exception(
            f"When collecting posts, received a status code: {response.status_code}"
        )

    body = response.json()
    users = create_user_map(body.get("users") or [])
    total_pages = (body.get("pager") or {}).get("total_pages") or 1

    posts = []
    for _post in body.get("posts") or []:
        # Sometimes votes is null
        if _post.get("votes") is not None:
            calculated_score = calculate_score(_post)
        else:
            calculated_score = 0
        posts.append(
            Post(
                body=strip_html(_post.get("body")),
                score=calculated_score,
                user=users.get(_post.get("author_id")) or "unknown",
            )
        )

    return posts, total_pages


def get_posts(post):
    """Retrieve posts from a thread.

    Args:
        post (str): either full url or postid

    Returns:
        list(Post): Posts
    """
    post_id = resolve_post_id(post)

    posts, total_pages = get_posts_page(post_id, 1)

    for page in range(2, total_pages + 1):
        current_posts, _ = get_posts_page(post_id, page)
        posts += current_posts

    return posts

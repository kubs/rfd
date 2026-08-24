"""This module provides utility functions that are used within rfd"""

import re

try:
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse  # python 2
except ImportError:
    from urlparse import parse_qsl, urlencode, urlparse, urlunparse  # python 1
from bs4 import BeautifulSoup

# Affiliate hops carry the real destination in one of these, url encoded.
# Skimlinks uses url, LinkSynergy murl, Impact Radius u.
REDIRECT_PARAMS = ("url", "murl", "u", "ued", "destination", "target", "redirect")

# Params that only exist to track who sent you.
TRACKING_PARAMS = {
    "_encoding",
    "ascsubtag",
    "camp",
    "content-id",
    "creative",
    "creativeasin",
    "crid",
    "dib",
    "dib_tag",
    "cjevent",
    "dclid",
    "epik",
    "fbclid",
    "gclid",
    "gclsrc",
    "igshid",
    "gbraid",
    "irclickid",
    "linkcode",
    "mcid",
    "linkid",
    "msclkid",
    "qid",
    "ref",
    "ref_",
    "referrer",
    "refrid",
    "sbo",
    "social_share",
    "sprefix",
    "sr",
    "siteid",
    "subid",
    "subid1",
    "tag",
    "ttclid",
    "u1",
    "wbraid",
    "yclid",
}
TRACKING_PREFIXES = ("utm_", "pf_rd_", "pd_rd_", "mc_", "sc_", "gad_", "_bta_")

# Amazon hangs a tracking crumb off the end of the path: /dp/B01ABC/ref=sr_1_3
TRACKING_PATH = re.compile(r"/ref=[^/]*/?$")

MAX_UNWRAP = 4


def is_tracking_param(name):
    name = name.lower()
    return name in TRACKING_PARAMS or name.startswith(TRACKING_PREFIXES)


def clean_url(url, _depth=0):
    """Unwrap affiliate redirects and drop tracking params from a deal url.

    Redirects that keep the destination server side (doubleclick ad clicks,
    for instance) cannot be unwrapped without following them, so they are
    returned untouched rather than guessed at.
    """
    if not url:
        return ""

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return url

    # Some urls arrive html escaped, which turns &sr= into &amp;sr=.
    query = parsed.query.replace("&amp;", "&")
    params = parse_qsl(query, keep_blank_values=True)

    if _depth < MAX_UNWRAP:
        for name, value in params:
            if name.lower() in REDIRECT_PARAMS and value[:4] == "http":
                return clean_url(value, _depth + 1)

    kept = [(name, value) for name, value in params if not is_tracking_param(name)]
    path = TRACKING_PATH.sub("", parsed.path)

    if len(kept) == len(params) and path == parsed.path and query == parsed.query:
        return url  # nothing to strip, so hand back exactly what we were given

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            path,
            parsed.params,
            urlencode(kept),
            parsed.fragment,
        )
    )


# The forum shortens link text to "https://www.staples.ca/produ ... er-20-pack",
# so the visible text is unusable and the href is the only real copy.
TRUNCATED = " ... "

FORUM_HOST = "forums.redflagdeals.com"


def is_truncated_url(text):
    return text.startswith(("http://", "https://")) and TRUNCATED in text


def html_to_text(html):
    """Post body to plain text, putting the real url back into truncated links."""
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a"):
        href = anchor.get("href") or ""
        cleaned = clean_url(href)
        if not cleaned:
            continue
        text = anchor.get_text().strip()
        if not text or text == href or is_truncated_url(text):
            anchor.replace_with(cleaned)
        else:
            anchor.replace_with(f"{text} <{cleaned}>")
    return soup.get_text()


def extract_links(html):
    """Cleaned, deduped links out of a post body, forum links last.

    A thread with no deal button usually has the deal sitting in the first
    post instead, and the forum rewrites those hrefs into affiliate hops.
    """
    external, internal, seen = [], [], set()
    for anchor in BeautifulSoup(html, "html.parser").find_all("a"):
        url = clean_url(anchor.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        if urlparse(url).netloc.endswith(FORUM_HOST):
            internal.append(url)
        else:
            external.append(url)
    return external + internal


def is_valid_url(url):
    result = urlparse(url)
    return all([result.scheme, result.netloc, result.path])


def is_int(number):
    try:
        int(number)
        return True
    except ValueError:
        return False

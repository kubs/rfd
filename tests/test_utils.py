from rfd.utils import clean_url, extract_links, html_to_text


def test_clean_url_unwraps_skimlinks():
    assert (
        clean_url(
            "http://go.redirectingat.com?id=130832X1694315&xs=1"
            "&url=https%3A%2F%2Fwww.mlb.com%2Fbluejays%2Ftickets"
        )
        == "https://www.mlb.com/bluejays/tickets"
    )


def test_clean_url_unwraps_linksynergy():
    assert (
        clean_url(
            "http://click.linksynergy.com/deeplink?id=CAqD7bLWUPI&mid=36751"
            "&murl=https%3A%2F%2Fwww.walmart.ca%2Fen%2Fip%2Fbacon%2F6000193606707&u1=rfdcb"
        )
        == "https://www.walmart.ca/en/ip/bacon/6000193606707"
    )


def test_clean_url_unwraps_impact_radius_and_cleans_the_destination():
    assert (
        clean_url(
            "https://bestbuyca.o93x.net/c/1/2/3?u=https%3A%2F%2Fwww.bestbuy.ca"
            "%2Fen-ca%2Fproduct%2Fx%2F17240403%3Futm_source%3Dredflagdeals"
        )
        == "https://www.bestbuy.ca/en-ca/product/x/17240403"
    )


def test_clean_url_strips_amazon_affiliate_and_referral_crumbs():
    assert (
        clean_url(
            "https://www.amazon.ca/dp/B08XYZ/ref=sr_1_3?tag=rfd0e-20&psc=1&th=1"
            "&pf_rd_r=ABC&utm_source=redflagdeals"
        )
        == "https://www.amazon.ca/dp/B08XYZ?psc=1&th=1"
    )


def test_clean_url_keeps_params_the_page_needs():
    """Over stripping is worse than a stray tracker: it can change the offer."""
    url = "https://www.costco.ca/kirkland.product.100334756.html?langId=-24"
    assert clean_url(url) == url


def test_clean_url_copes_with_html_escaped_separators():
    assert (
        clean_url("https://a.ca/p?ie=UTF8&amp;qid=152797&amp;keywords=cling+film")
        == "https://a.ca/p?ie=UTF8&keywords=cling+film"
    )


def test_clean_url_leaves_redirects_it_cannot_resolve_alone():
    """Doubleclick keeps the destination server side, so there is nothing to unwrap."""
    url = "https://pubads.g.doubleclick.net/gampad/clk?id=7400271981&iu=/1030735/redflagdeals"
    assert clean_url(url) == url


def test_clean_url_handles_missing_and_odd_urls():
    assert clean_url("") == ""
    assert clean_url(None) == ""
    assert clean_url("mailto:deals@example.com") == "mailto:deals@example.com"


TRUNCATED_LINK = (
    'Deal:<br>\n<a href="https://staplescanada.4u8mqw.net/c/1/2/3'
    '?u=https://www.staples.ca/products/3125825?utm_source=rfd" class="postlink">'
    "https://www.staples.ca/products/3125 ... er-20-pack</a>"
)


def test_html_to_text_puts_the_real_url_back():
    """The forum truncates link text, so get_text() alone loses the url."""
    assert html_to_text(TRUNCATED_LINK) == (
        "Deal:\nhttps://www.staples.ca/products/3125825"
    )


def test_html_to_text_keeps_wording_and_adds_the_url():
    assert (
        html_to_text('see <a href="https://a.ca/p?utm_source=rfd">the deal</a>')
        == "see the deal <https://a.ca/p>"
    )


def test_extract_links_dedupes_and_puts_forum_links_last():
    html = (
        '<a href="https://forums.redflagdeals.com/t-123/">thread</a>'
        '<a href="https://a.ca/p?tag=rfd-20">deal</a>'
        '<a href="https://a.ca/p">deal again</a>'
    )
    assert extract_links(html) == [
        "https://a.ca/p",
        "https://forums.redflagdeals.com/t-123/",
    ]


def test_extract_links_on_a_post_with_none():
    assert extract_links("just some text") == []

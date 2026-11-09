from __future__ import unicode_literals


import logging
import sys
import json
import click

try:
    from importlib import metadata
except ImportError:  # for Python<3.8
    import importlib_metadata as metadata
from colorama import init
from .api import get_threads, get_threads_page, get_posts
from .threads import (
    parse_threads,
    search_threads,
    sort_threads,
    generate_thread_output,
    ThreadEncoder,
)
from .posts import generate_posts_output, PostEncoder
from .bookmarks import load_bookmarks

init()

logging.getLogger()
logging.getLogger().setLevel(logging.INFO)
logging.getLogger().addHandler(logging.StreamHandler())


def load_browser():
    """Return the curses browser, failing early and clearly when it cannot run."""
    try:
        from .interactive import browse  # pylint: disable=import-outside-toplevel
    except ImportError:
        click.echo(
            "Interactive mode needs the curses module. "
            "On Windows, install it with: pip install windows-curses"
        )
        sys.exit(1)
    if not sys.stdout.isatty():
        click.echo("Interactive mode requires a terminal.")
        sys.exit(1)
    return browse


def make_page_loader(forum_id, regex=None):
    """Build the load_page(page) callable the interactive feed pages with.

    Returns (threads, topics_on_page): the second value is how the browser
    tells "this page matched nothing" apart from "the forum ended".
    """

    def load_page(page):
        topics = get_threads_page(forum_id, page)
        parsed = parse_threads(topics)
        if regex is not None:
            parsed = list(search_threads(threads=parsed, regex=regex))
        return parsed, len(topics)

    return load_page


def get_version():
    return "rfd v" + metadata.version("rfd")


def print_version(ctx, _, value):
    if not value or ctx.resilient_parsing:
        return
    click.echo(get_version(), nl=True)
    ctx.exit()


@click.group(invoke_without_command=True)
@click.option(
    "-v",
    "--version",
    is_flag=True,
    callback=print_version,
    expose_value=False,
    is_eager=True,
)
@click.pass_context
def cli(ctx):
    """CLI for https://forums.redflagdeals.com"""
    if not ctx.invoked_subcommand:
        click.echo(ctx.get_help())


@cli.command(short_help="Display all posts in a thread.")
@click.argument("post_id")
@click.option(
    "--output", default=None, help="Defaults to custom formatting. Other options: json"
)
def posts(post_id, output):
    """Iterate all pages and display all posts in a thread.

    post_id can be a full url or post id only

    Example:

    \b
    rfd posts https://forums.redflagdeals.com/koodo-targeted-public-mobile-12-120-koodo-5gb-40-no-referrals-2173603
    """

    try:
        if output == "json":
            click.echo_via_pager(
                json.dumps(
                    get_posts(post=post_id),
                    cls=PostEncoder,
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            click.echo_via_pager(generate_posts_output(get_posts(post=post_id)))

    except ValueError:
        click.echo("Invalid post id.")
        sys.exit(1)
    except AttributeError as err:
        click.echo("The RFD API did not return the expected data. %s", err)
        sys.exit(1)


@cli.command(short_help="Displays threads in the forum. Defaults to hot deals.")
@click.option("--forum-id", default=9, help="The forum id number")
@click.option("--pages", default=1, help="Number of pages to show. Defaults to 1.")
@click.option("--sort-by", default=None, help="Sort threads by")
@click.option(
    "--output", default=None, help="Defaults to custom formatting. Other options: json"
)
@click.option(
    "-i",
    "--interactive",
    is_flag=True,
    help="Browse threads and posts with vim key bindings.",
)
def threads(forum_id, pages, sort_by, output, interactive):
    """Display threads in the specified forum id. Defaults to 9 (hot deals).

    Popular forum ids:

    \b
    9 \t hot deals
    14 \t computer and electronics
    15 \t offtopic
    17 \t entertainment
    18 \t food and drink
    40 \t automotive
    53 \t home and garden
    67 \t fashion and apparel
    74 \t shopping discussion
    88 \t cell phones
    """
    browse = load_browser() if interactive else None

    _threads = sort_threads(
        parse_threads(get_threads(forum_id, pages)), sort_by=sort_by
    )
    if interactive:
        browse(
            _threads,
            sort_by=sort_by,
            load_page=make_page_loader(forum_id),
            pages_loaded=pages,
        )
    elif output == "json":

        click.echo_via_pager(
            json.dumps(
                sort_threads(_threads, sort_by=sort_by),
                cls=ThreadEncoder,
                indent=2,
                sort_keys=True,
            )
        )
    else:
        click.echo_via_pager(generate_thread_output(_threads))


@cli.command(short_help="Search deals based on a regular expression.")
@click.option("--pages", default=5, help="Number of pages to search.")
@click.option(
    "--forum-id", default=9, help="The forum id number. Defaults to 9 (hot deals)."
)
@click.option("--sort-by", default=None, help="Sort threads by")
@click.option(
    "--output", default=None, help="Defaults to custom formatting. Other options: json"
)
@click.option(
    "-i",
    "--interactive",
    is_flag=True,
    help="Browse matching threads and posts with vim key bindings.",
)
@click.argument("regex")
def search(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    pages, forum_id, sort_by, output, regex, interactive
):
    """Search deals based on regex.

    Popular forum ids:

    \b
    9 \t hot deals
    14 \t computer and electronics
    15 \t offtopic
    17 \t entertainment
    18 \t food and drink
    40 \t automotive
    53 \t home and garden
    67 \t fashion and apparel
    74 \t shopping discussion
    88 \t cell phones
    """

    browse = load_browser() if interactive else None

    _threads = parse_threads(get_threads(forum_id, pages=pages))
    matched_threads = sort_threads(
        list(search_threads(threads=_threads, regex=regex)), sort_by=sort_by
    )

    if interactive:
        browse(
            matched_threads,
            sort_by=sort_by,
            load_page=make_page_loader(forum_id, regex=regex),
            pages_loaded=pages,
        )
    elif output == "json":
        click.echo_via_pager(
            json.dumps(
                matched_threads,
                indent=2,
                sort_keys=True,
                cls=ThreadEncoder,
            )
        )
    else:
        click.echo_via_pager(generate_thread_output(matched_threads))


@cli.command(short_help="Retrieve your locally starred deal threads.")
@click.option(
    "-i", "--interactive", is_flag=True, help="Browse saved deals; b removes a star."
)
@click.option("--output", type=click.Choice(["json"]), default=None)
def bookmarks(interactive, output):
    """List saved deals without fetching the forum. Star deals with b in interactive mode."""
    try:
        saved = load_bookmarks()
    except (OSError, ValueError) as err:
        raise click.ClickException(str(err)) from err
    if interactive:
        load_browser()(saved, bookmarks_mode=True)
    elif output == "json":
        click.echo(json.dumps(saved, cls=ThreadEncoder, indent=2, ensure_ascii=False))
    elif saved:
        click.echo_via_pager(generate_thread_output(saved))
    else:
        click.echo("No saved deals yet. Run rfd threads -i and press b to star a deal.")

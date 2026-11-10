# pylint: disable=old-style-class
import json
import shutil
from colorama import Fore, Style
from .scores import get_vote_color


class Post:
    def __init__(self, body, score, user, links=None, images=None):
        self.body = body
        self.links = links or []
        self.images = images or []
        self.score = score
        self.user = user


class PostEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Post):
            return dict(
                body=o.body,
                links=o.links,
                images=o.images,
                score=o.score,
                user=o.user,
            )
        return json.JSONEncoder.default(self, o)


def get_terminal_width():
    return shutil.get_terminal_size().columns


def generate_posts_output(posts):
    output = ""
    output += "-" * get_terminal_width()
    for post in posts:
        output += (
            " -"
            + get_vote_color(post.score)
            + Fore.RESET
            + post.body
            + Fore.YELLOW
            + f" ({post.user})"
        )
        for index, url in enumerate(post.images, 1):
            output += f"\n      [image {index}] {url}"
        output += Style.RESET_ALL
        output += "\n"
        output += "-" * get_terminal_width()
        output += "\n"
    return output

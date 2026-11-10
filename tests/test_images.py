import json
from types import SimpleNamespace

from rfd import api
from rfd.interactive import Browser, Pane
from rfd.posts import Post, PostEncoder, generate_posts_output
from rfd.utils import extract_images


def test_extract_images():
    html = '''<img src="/photo.jpg?size=full&amp;token=a">
    <img src="/photo.jpg?size=full&amp;token=a">
    <a href="https://shop.example"><img data-src="//cdn.example/full.png" src="placeholder.gif"></a>
    <img data-original="https://cdn.example/lazy.jpg">
    <img class="smilies" src="/smile.gif"><img src="/images/smilies/happy.gif">
    <img src="data:image/png;base64,abc"><img src="javascript:alert(1)"><img>'''
    assert extract_images(html) == [
        'https://forums.redflagdeals.com/photo.jpg?size=full&token=a',
        'https://cdn.example/full.png',
        'https://cdn.example/lazy.jpg',
    ]


def test_api_and_output_preserve_images(monkeypatch):
    response = SimpleNamespace(status_code=200, json=lambda: {
        'posts': [{'body': 'Deal<img src="/image.png">', 'author_id': 1}],
        'users': [{'user_id': 1, 'username': 'user'}],
    })
    monkeypatch.setattr(api, 'get_session', lambda: SimpleNamespace(get=lambda _: response))
    posts, pages = api.get_posts_page('123', 1)
    assert pages == 1
    assert posts[0].images == ['https://forums.redflagdeals.com/image.png']
    assert posts[0].images[0] in generate_posts_output(posts)
    assert json.loads(json.dumps(posts[0], cls=PostEncoder))['images'] == posts[0].images


def test_image_rows_open_and_reset_on_resize(monkeypatch):
    browser = Browser.__new__(Browser)
    browser.post_cache_key = None
    browser.post_pane = Pane()
    browser.thread = object()
    posts = [Post('Deal', 1, 'user', images=['https://example.com/' + 'a' * 100 + '.png'])]
    monkeypatch.setattr("rfd.interactive.supported_terminal", lambda: False)
    opened = []
    monkeypatch.setattr('rfd.interactive.webbrowser.open', lambda url: opened.append(url) or True)
    browser.render_posts(posts, 40)
    assert len(browser.image_rows) > 1
    for row in browser.image_rows:
        browser.post_pane.cursor = row
        browser.open_image()
        assert opened[-1] == posts[0].images[0]
    browser.post_pane.cursor = 0
    before = len(opened)
    browser.open_image()
    assert len(opened) == before
    browser.render_posts(posts, 200)
    assert len(browser.image_rows) == 1
    browser.close_thread()
    assert browser.image_rows == {}


def test_terminal_image_encoding_fits_and_preserves_ratio(monkeypatch):
    import base64
    import io
    from PIL import Image
    from rfd import images
    monkeypatch.setattr(images, 'cell_size', lambda: (10, 20))
    commands = list(images.image_commands(Image.new('RGB', (800, 400)), 20, 5))
    assert b'a=T,f=100' in commands[0]
    assert b'm=0;' in commands[-1]
    payload = b''.join(cmd.split(b';', 1)[1][:-2] for cmd in commands)
    decoded = Image.open(io.BytesIO(base64.b64decode(payload)))
    assert decoded.size == (200, 100)


def test_terminal_viewer_navigation_resize_and_cleanup(monkeypatch):
    import io
    import curses
    from concurrent.futures import Future
    from PIL import Image
    from rfd import images
    loaded = []
    deleted = []
    class Executor:
        def __init__(self, **kwargs):
            pass
        def submit(self, function, url):
            loaded.append(url)
            future = Future()
            future.set_result(Image.new('RGB', (20, 20)))
            return future
        def shutdown(self, **kwargs):
            pass
    class Screen:
        keys = iter([ord('l'), curses.KEY_RESIZE, ord('h'), ord('q')])
        restored = False
        def timeout(self, value):
            self.delay = value
        def erase(self):
            pass
        def getmaxyx(self):
            return (24, 80)
        def addnstr(self, *args):
            pass
        def refresh(self):
            pass
        def getch(self):
            return next(self.keys)
        def clearok(self, value):
            self.restored = value
        def touchwin(self):
            pass
    output = io.TextIOWrapper(io.BytesIO(), encoding='utf8')
    monkeypatch.setattr(images.sys, 'stdout', output)
    monkeypatch.setattr(images, 'ThreadPoolExecutor', Executor)
    monkeypatch.setattr(images, 'clear_image', lambda: deleted.append(True))
    screen = Screen()
    images.show_images(screen, ['https://example.com/a', 'https://example.com/b'], 0)
    assert loaded == ['https://example.com/a', 'https://example.com/b']
    assert len(deleted) == 5
    assert screen.restored and screen.delay == -1


def test_image_download_rejects_oversize_and_invalid_data(monkeypatch):
    import pytest
    from rfd import images
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def raise_for_status(self):
            pass
        def iter_content(self, size):
            yield b'not an image'
    monkeypatch.setattr(images, 'get_session', lambda: SimpleNamespace(get=lambda *a, **kw: Response()))
    monkeypatch.setattr(images, 'MAX_BYTES', 4)
    with pytest.raises(ValueError, match='20 MB'):
        images.load_image('https://example.com/a')
    monkeypatch.setattr(images, 'MAX_BYTES', 100)
    with pytest.raises(OSError):
        images.load_image('https://example.com/a')

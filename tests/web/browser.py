import pytest


@pytest.fixture(scope="module")
def chromium():
    try:
        from playwright import sync_api

        pw = sync_api.sync_playwright().start()
    except Exception as e:
        pytest.skip(f"playwright unavailable: {e}")
    try:
        browser = pw.chromium.launch()
    except Exception as e:
        pw.stop()
        pytest.skip(f"chromium is not installed (run `playwright install chromium`): {str(e).splitlines()[0]}")
    yield browser
    browser.close()
    pw.stop()


class Page:
    def __init__(self, browser, server, width=1280, height=900, tour=False, **ctx):
        self.ctx = browser.new_context(viewport={"width": width, "height": height}, **ctx)
        self.page = self.ctx.new_page()
        self.errors = []
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.expose_binding("__csp", lambda _src, d: self.errors.append("csp violation: " + d))
        self.page.add_init_script(
            "document.addEventListener('securitypolicyviolation', "
            "e => window.__csp(e.violatedDirective + ' ' + e.blockedURI))"
        )
        if not tour:
            self.page.add_init_script(
                "const _get = Storage.prototype.getItem;"
                "Storage.prototype.getItem = function (k) {"
                " return String(k).startsWith('rl.tour.') ? 'off' : _get.call(this, k); };"
            )
        self.server = server

    def open(self, path="/", theme="plain", panels=(), storage=None):
        if theme or panels or storage:
            self.page.goto(self.server.url + "/")
            script = "".join(f"localStorage.setItem('rl.open.{p}','true');" for p in panels)
            if theme:
                script += f"localStorage.setItem('rl.theme','{theme}');"
            for key, value in (storage or {}).items():
                script += f"localStorage.setItem('{key}','{value}');"
            self.page.evaluate(script)
        self.page.goto(self.server.url + path)
        return self.page

    def close(self):
        self.ctx.close()


@pytest.fixture
def browse(chromium, server):
    pages = []

    def make(**kw):
        p = Page(chromium, server, **kw)
        pages.append(p)
        return p

    yield make
    for p in pages:
        p.close()


def no_hscroll(page):
    return page.evaluate("""() => {
      const d = document.documentElement;
      const wide = [...document.querySelectorAll(
          'header, nav, section, main, .lane, .card, .guild, .hero, .proj, .panel')]
        .filter(e => e.offsetParent !== null && e.getBoundingClientRect().right > innerWidth + 1)
        .map(e => e.id || e.className);
      return {scroll: d.scrollWidth, client: d.clientWidth, wide};
    }""")


def settle(page, selector, ms=300):
    page.wait_for_selector(selector)
    page.evaluate("document.getElementById('soundBanner').hidden = true")
    page.wait_for_timeout(ms)
    return page


def open_view(browse, path, selector, theme="raid", width=1280, storage=None, **ctx):
    p = browse(width=width, height=900, **ctx)
    page = p.open(path, theme=theme, storage=storage)
    settle(page, selector)
    return p, page


def assert_fits(p, page):
    r = no_hscroll(page)
    assert r["scroll"] <= r["client"], r
    assert r["wide"] == [], r
    assert page.locator("#viewPane").bounding_box()["width"] <= page.viewport_size["width"]
    assert p.errors == []


def running_animations(page):
    return page.evaluate("document.getAnimations().filter(a => a.playState === 'running').length")


def call_cli(capsys, board, *args):
    import json

    from raidlock import cli

    code = cli.main([*args, "--board", str(board.dir), "--json"])
    out = capsys.readouterr().out
    return code, json.loads(out)

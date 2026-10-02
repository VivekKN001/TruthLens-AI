import requests

from utils import fetcher
from utils.fetcher import _fallback_extract, extract_main_text, fetch_page_text, trim_to_boundary

ARTICLE_HTML = """
<html><head><title>Fusion news</title><script>var tracking = 1;</script></head>
<body>
  <nav><a href="/">Home</a><a href="/about">About us and our very long navigation label</a></nav>
  <article>
    <h1>Fusion reactor reaches a new milestone</h1>
    <p>Researchers at the national laboratory reported that the experimental reactor produced more
    energy than was delivered to its fuel capsule, a result scientists have pursued for decades.</p>
    <p>The team said the next step is improving the repetition rate so that the process could one day
    run continuously, which remains the major engineering hurdle for practical power plants.</p>
  </article>
  <footer>Copyright notice and a long list of footer links that should never be extracted</footer>
</body></html>
"""


class FakeResponse:
    def __init__(self, body, content_type="text/html; charset=utf-8", status=200):
        self._body = body.encode("utf-8")
        self.headers = {"Content-Type": content_type}
        self.encoding = "utf-8"
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(f"{self.status}")

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_extract_main_text_keeps_article_and_drops_chrome():
    text = extract_main_text(ARTICLE_HTML, "https://lab.example/fusion")
    assert "produced more energy" in text
    assert "tracking" not in text
    assert "footer links" not in text


def test_fallback_extractor_works_without_trafilatura():
    text = _fallback_extract(ARTICLE_HTML)
    assert "repetition rate" in text
    assert "var tracking" not in text
    assert "footer links" not in text


def test_fetch_page_text_returns_extracted_text(monkeypatch):
    monkeypatch.setattr(fetcher.requests, "get", lambda *a, **k: FakeResponse(ARTICLE_HTML))
    assert "produced more energy" in fetch_page_text("https://lab.example/fusion")


def test_fetch_page_text_skips_non_html(monkeypatch):
    monkeypatch.setattr(fetcher.requests, "get", lambda *a, **k: FakeResponse("%PDF-1.7", "application/pdf"))
    assert fetch_page_text("https://lab.example/paper.pdf") == ""


def test_fetch_page_text_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(fetcher.requests, "get", lambda *a, **k: FakeResponse("nope", status=403))
    try:
        fetch_page_text("https://lab.example/blocked")
    except requests.HTTPError:
        return
    raise AssertionError("expected HTTPError")


def test_trim_to_boundary_prefers_sentence_end():
    text = "First sentence is here. Second sentence is here. Third sentence runs on and on."
    trimmed = trim_to_boundary(text, 55)
    assert trimmed == "First sentence is here. Second sentence is here."


def test_trim_to_boundary_leaves_short_text_alone():
    assert trim_to_boundary("short", 100) == "short"

"""
Page Fetcher - downloads a web page and extracts its main article text.

Search results only carry a 1-2 sentence snippet, which is too little for the
writer to work from without filling gaps with invented specifics. Reading the
actual page gives the pipeline real material to synthesize and cite.

Uses trafilatura (boilerplate-aware article extraction) when installed, and
falls back to a crude stdlib HTML-to-text pass otherwise.
"""

import re
from html.parser import HTMLParser

import requests

from utils.logger import get_logger

logger = get_logger(__name__)

# Some sites reject the default python-requests user agent outright.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# Cap how much HTML we read per page - article text is never this large, and a
# runaway response shouldn't stall research.
MAX_HTML_BYTES = 3_000_000


class _TextExtractor(HTMLParser):
    """Fallback extractor: keeps visible text, drops scripts/styles/navigation chrome."""

    SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form", "iframe"}
    BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr", "section", "article", "blockquote"}

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def _fallback_extract(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts)
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n"))
    # Short lines are almost always menu items, buttons and captions, not prose.
    return "\n".join(line for line in lines if len(line) > 40)


def extract_main_text(html: str, url: str = "") -> str:
    """
    Extract the main readable text from an HTML document.

    Args:
        html: Raw HTML
        url: Page URL (helps trafilatura with some site-specific heuristics)

    Returns:
        Extracted text, or "" if nothing readable was found
    """
    try:
        import trafilatura

        text = trafilatura.extract(
            html,
            url=url or None,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
        if text:
            return text.strip()
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"trafilatura failed on {url}: {e}")

    return _fallback_extract(html).strip()


def fetch_page_text(url: str, timeout: float = 8.0) -> str:
    """
    Download a page and return its main text.

    Raises on network/HTTP errors so the caller can decide to fall back to the
    search snippet. Returns "" for non-HTML responses (PDFs, images, etc).

    Args:
        url: Page URL
        timeout: Connect/read timeout in seconds

    Returns:
        Extracted article text
    """
    with requests.get(
        url,
        timeout=timeout,
        stream=True,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    ) as resp:
        resp.raise_for_status()
        if "html" not in resp.headers.get("Content-Type", "").lower():
            return ""

        chunks, size = [], 0
        for chunk in resp.iter_content(chunk_size=65536):
            chunks.append(chunk)
            size += len(chunk)
            if size >= MAX_HTML_BYTES:
                break
        html = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")

    return extract_main_text(html, url)


def trim_to_boundary(text: str, max_chars: int) -> str:
    """Cut text to at most max_chars, preferring to end on a paragraph or sentence boundary."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    for sep in ("\n", ". "):
        idx = cut.rfind(sep)
        if idx > max_chars * 0.6:
            return cut[: idx + 1].rstrip()
    return cut.rstrip() + "…"

"""
Citation helpers - keep the blog's inline [n] markers honest.

The researcher numbers its sources [1]..[N] (matching state.research_sources),
and the writer/editor are asked to cite facts with those markers. A local model
will still sometimes invent a number that doesn't exist, or tack on its own
"Sources" section (often with made-up URLs) even when told not to. These
helpers clean both up, so every marker in the final post points at a real
source and the only source list is the real one appended on export.
"""

import re
from dataclasses import dataclass, field

# [3] or [1, 4] - but not a markdown link like [3](https://...) or a
# reference-style definition like [3]: https://...
CITATION_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\](?![(:])")

# A heading (or bold line) introducing a model-written source list.
SOURCES_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*|\*\*)?\s*(?:sources|references|bibliography|works cited|citations)\s*:?\s*(?:\*\*)?\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class CitationCleanup:
    text: str
    invalid_numbers: list[int] = field(default_factory=list)
    removed_sources_section: bool = False


def find_citations(text: str) -> list[int]:
    """All source numbers cited in the text, in order of appearance (with repeats)."""
    numbers = []
    for match in CITATION_RE.finditer(text or ""):
        numbers.extend(int(n) for n in re.split(r"\s*,\s*", match.group(1)))
    return numbers


def _strip_invalid_markers(text: str, num_sources: int) -> tuple[str, list[int]]:
    invalid: list[int] = []

    def replace(match: re.Match) -> str:
        numbers = [int(n) for n in re.split(r"\s*,\s*", match.group(1))]
        valid = [n for n in numbers if 1 <= n <= num_sources]
        invalid.extend(n for n in numbers if n not in valid)
        if not valid:
            return ""
        return "[" + ", ".join(str(n) for n in valid) + "]"

    cleaned = CITATION_RE.sub(replace, text)
    # Removing a marker can leave "fact ." or "fact  and" behind.
    cleaned = re.sub(r"[ \t]+([.,;:!?])", r"\1", cleaned) if invalid else cleaned
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned) if invalid else cleaned
    return cleaned, invalid


def _strip_trailing_sources_section(text: str) -> tuple[str, bool]:
    """
    Drop a model-written "Sources"/"References" section at the end of the post.

    Only removes it when it's in the last third of the text and nothing after it
    looks like more article (another heading), so a legitimate mid-article
    section that happens to be titled "Sources of funding" survives.
    """
    matches = list(SOURCES_HEADING_RE.finditer(text))
    if not matches:
        return text, False
    last = matches[-1]
    if last.start() < len(text) * 0.66:
        return text, False
    tail = text[last.end():]
    if re.search(r"^\s*#{1,6}\s+\S", tail, re.MULTILINE):
        return text, False
    trimmed = text[: last.start()].rstrip()
    # Also drop a horizontal rule that only existed to separate the list.
    trimmed = re.sub(r"\n\s*(?:-{3,}|\*{3,}|_{3,})\s*$", "", trimmed).rstrip()
    return trimmed, True


def clean_citations(text: str, num_sources: int) -> CitationCleanup:
    """
    Remove citation markers that don't point at a real source, and any
    model-written source list at the end of the post.

    Args:
        text: Blog post markdown
        num_sources: How many sources exist (valid markers are 1..num_sources)

    Returns:
        CitationCleanup with the cleaned text and what was removed
    """
    if not text:
        return CitationCleanup(text=text or "")
    cleaned, invalid = _strip_invalid_markers(text, num_sources)
    cleaned, removed_section = _strip_trailing_sources_section(cleaned)
    return CitationCleanup(text=cleaned, invalid_numbers=invalid, removed_sources_section=removed_section)

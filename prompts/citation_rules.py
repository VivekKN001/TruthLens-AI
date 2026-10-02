"""
Citation rules shared by the writer and editor prompts.

The researcher numbers its sources [1]..[N] (matching state.research_sources),
so every [n] marker in the post maps to a real URL. utils/citations.py strips
any marker that doesn't, and any model-written "Sources" section.
"""

CITATION_RULES = """- Cite your sources. The research data marks facts with numbered sources like [3]. When you use a
  specific fact (a number, date, name, quote, or finding), put that same marker right after it,
  e.g. "The trial enrolled 400 patients [3]." Use only numbers that appear in the research data.
  Never invent a source number, and never attach a citation to a claim the research doesn't contain.
- A citation backs up your explanation, it never replaces it: explain the information in your own
  words, then cite it. Don't cite general background that needs no source.
- Do NOT write a "Sources" or "References" section - the numbered source list is appended automatically."""

KEEP_CITATIONS_RULE = """- Keep the [n] citation markers attached to the facts they support. If you remove a fact, remove its
  marker too. Never invent new citation numbers, and do NOT add a "Sources" or "References" section."""

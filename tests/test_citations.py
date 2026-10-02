from utils.citations import clean_citations, find_citations


def test_find_citations_handles_single_and_grouped_markers():
    text = "Fusion hit net gain [1]. Costs remain high [2, 3]. Again [1]."
    assert find_citations(text) == [1, 2, 3, 1]


def test_find_citations_ignores_markdown_links_and_reference_definitions():
    text = "See [2](https://example.com) and\n[3]: https://example.com\nbut cite [4]."
    assert find_citations(text) == [4]


def test_clean_citations_keeps_valid_markers_untouched():
    text = "The trial enrolled 400 patients [3]. Results were mixed [1, 2]."
    result = clean_citations(text, num_sources=3)
    assert result.text == text
    assert result.invalid_numbers == []
    assert not result.removed_sources_section


def test_clean_citations_drops_markers_beyond_the_source_count():
    result = clean_citations("Claim one [2]. Claim two [9].", num_sources=3)
    assert result.text == "Claim one [2]. Claim two."
    assert result.invalid_numbers == [9]


def test_clean_citations_keeps_valid_part_of_a_grouped_marker():
    result = clean_citations("Mixed evidence [2, 7, 3].", num_sources=3)
    assert result.text == "Mixed evidence [2, 3]."
    assert result.invalid_numbers == [7]


def test_clean_citations_with_no_sources_removes_every_marker():
    result = clean_citations("Something [1] happened.", num_sources=0)
    assert result.text == "Something happened."
    assert result.invalid_numbers == [1]


def test_clean_citations_strips_trailing_model_written_sources_section():
    body = "# Title\n\n" + "A long paragraph about the topic [1]. " * 20
    text = body + "\n\n---\n\n## Sources\n\n1. https://made-up.example/a\n2. https://made-up.example/b\n"
    result = clean_citations(text, num_sources=2)
    assert result.removed_sources_section
    assert "made-up.example" not in result.text
    assert result.text.endswith("[1].")


def test_clean_citations_handles_bold_sources_label():
    body = "Paragraph text here [1]. " * 30
    result = clean_citations(body + "\n\n**Sources:**\n- https://x.example\n", num_sources=1)
    assert result.removed_sources_section
    assert "x.example" not in result.text


def test_clean_citations_keeps_a_sources_heading_that_isnt_at_the_end():
    text = "# Title\n\n## Sources of funding\n\nGrants matter [1].\n\n## References\n\nMore article text.\n\n## Conclusion\n\nDone."
    result = clean_citations(text, num_sources=1)
    assert not result.removed_sources_section
    assert result.text == text

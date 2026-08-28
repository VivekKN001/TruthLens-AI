from utils.parsers import analyze_feedback, determine_revision_target, parse_editor_response


def test_parse_editor_response_standard_format():
    body = "word " * 30
    response = f"---EDITED BLOG POST---\n{body}\n---EDITORIAL NOTES---\nFixed a typo."

    result = parse_editor_response(response)

    assert result.parse_success
    assert "word" in result.blog_content
    assert result.editorial_notes == "Fixed a typo."


def test_parse_editor_response_bracket_format():
    body = "content " * 30
    response = f"[EDITED BLOG POST]\n{body}\n[EDITORIAL NOTES]\nLooks good."

    result = parse_editor_response(response)

    assert result.parse_success
    assert result.editorial_notes == "Looks good."


def test_parse_editor_response_fallback_when_unstructured():
    response = "just a plain response with no markers at all."

    result = parse_editor_response(response)

    assert not result.parse_success
    assert result.blog_content == response.strip()


def test_analyze_feedback_detects_writing_keywords():
    analysis = analyze_feedback("This is too boring, make it more engaging")

    assert analysis.targets_writing
    assert not analysis.targets_editing


def test_analyze_feedback_detects_editing_keywords():
    analysis = analyze_feedback("Fix the grammar and typos please")

    assert analysis.targets_editing
    assert not analysis.targets_writing


def test_determine_revision_target_writing_only():
    assert determine_revision_target("make it more engaging and less boring") == "writer_revise"


def test_determine_revision_target_editing_only():
    assert determine_revision_target("fix the typos and grammar") == "editor_revise"


def test_determine_revision_target_defaults_to_editor_when_ambiguous():
    assert determine_revision_target("this is fine I guess") == "editor_revise"


def test_determine_revision_target_defaults_to_editor_when_both_match():
    assert determine_revision_target("boring and full of typos") == "editor_revise"

"""The self-fixing grounding check: parsing its output, and the check -> fix -> re-check loop."""

from agents.editor import EditorAgent
from config import LLMConfig, ProviderConfig, Settings
from state import AgentState
from tests.fakes import ScriptedLLM
from utils.parsers import (
    format_grounding_notes,
    format_grounding_section,
    parse_grounding_issues,
    strip_response_preamble,
)

FLAGGED = '- CLAIM: "Fusion is 50% efficient [1]" | PROBLEM: research says 1.5% | FIX: Fusion is 1.5% efficient [1]'
ALL_CLEAR = "ALL CLAIMS SUPPORTED"
BLOG = "# Fusion\n\n" + "Fusion research keeps moving forward with new results [1]. " * 8 + "\n\nFusion is 50% efficient [1]."
FIXED_BLOG = BLOG.replace("50%", "1.5%")


# ---------- Parsing ----------

def test_parse_structured_issue():
    [issue] = parse_grounding_issues(FLAGGED)
    assert issue.claim == "Fusion is 50% efficient [1]"
    assert issue.problem == "research says 1.5%"
    assert issue.fix == "Fusion is 1.5% efficient [1]"


def test_parse_plain_bullets_still_count_as_issues():
    issues = parse_grounding_issues("Some issues:\n- The 2024 date isn't in the research\n* \"millions\" should be thousands")
    assert [i.claim for i in issues] == ["The 2024 date isn't in the research", '"millions" should be thousands']


def test_parse_ignores_all_clear_and_none_bullets():
    assert parse_grounding_issues(ALL_CLEAR) == []
    assert parse_grounding_issues("- None\n- No unsupported claims found.") == []


def test_format_notes_all_clear_and_failure():
    assert format_grounding_notes(ALL_CLEAR, []) == "All specific claims are supported by the research."
    # A failed check must not read as an all-clear.
    failed = "Grounding check unavailable (LLM call failed)."
    assert format_grounding_notes(failed, []) == failed


def test_format_notes_lists_issue_with_supported_wording():
    notes = format_grounding_notes(FLAGGED, parse_grounding_issues(FLAGGED))
    assert notes.startswith('- "Fusion is 50% efficient [1]" — research says 1.5%')
    assert "research supports: Fusion is 1.5% efficient [1]" in notes


def test_strip_response_preamble():
    assert strip_response_preamble("Here is the corrected blog post:\n\n# Title\nBody") == "# Title\nBody"
    assert strip_response_preamble("# Title\nHere is a sentence: fine") == "# Title\nHere is a sentence: fine"


def test_format_grounding_section():
    assert format_grounding_section("", []) == ""
    section = format_grounding_section("All good.", ["claim A — wrong number"])
    assert "## Grounding Check" in section
    assert "**Auto-corrected before review:**" in section
    assert "- claim A — wrong number" in section
    assert section.rstrip().endswith("All good.")


# ---------- verify_and_fix loop ----------

def _editor(responses, max_fix_rounds=1):
    settings = Settings()
    settings.provider = ProviderConfig(provider="ollama")
    settings.workflow.max_fix_rounds = max_fix_rounds
    llm = ScriptedLLM(responses)
    return EditorAgent(llm=llm, llm_config=LLMConfig(model="fake"), app_settings=settings), llm


def _state():
    return AgentState(category="Science", topic="Fusion", research_content="[1] Fusion is 1.5% efficient.",
                      research_sources=["https://a.example"], blog_final=BLOG)


def test_verify_and_fix_corrects_flagged_claim_and_rechecks():
    editor, llm = _editor([FLAGGED, FIXED_BLOG, ALL_CLEAR])
    streamed = []

    result = editor.verify_and_fix(_state(), on_token=streamed.append)

    assert llm.calls == 3  # check, fix, re-check
    assert "1.5% efficient" in result.blog_final and "50%" not in result.blog_final
    assert result.grounding_fixes == ['"Fusion is 50% efficient [1]" — research says 1.5%']
    assert result.grounding_notes == "All specific claims are supported by the research."
    assert any("Fixing 1 flagged claim" in t for t in streamed)
    assert any("Re-checking" in t for t in streamed)
    # The fix prompt carries the flagged claim and the research
    assert "Fusion is 50% efficient" in llm.prompts[1] and "1.5% efficient" in llm.prompts[1]


def test_verify_and_fix_does_nothing_extra_when_all_clear():
    editor, llm = _editor([ALL_CLEAR])

    result = editor.verify_and_fix(_state())

    assert llm.calls == 1
    assert result.blog_final == BLOG
    assert result.grounding_fixes == []


def test_verify_and_fix_rejects_truncated_fix():
    editor, llm = _editor([FLAGGED, "Fusion is 1.5% efficient [1]."])

    result = editor.verify_and_fix(_state())

    assert llm.calls == 2  # no re-check after a rejected fix
    assert result.blog_final == BLOG
    assert result.grounding_fixes == []
    assert "50%" in result.grounding_notes  # still flagged for the human
    assert any("truncated" in m for m in result.messages)


def test_verify_and_fix_stops_when_fix_changes_nothing():
    editor, llm = _editor([FLAGGED, BLOG])

    result = editor.verify_and_fix(_state())

    assert llm.calls == 2
    assert result.grounding_fixes == []


def test_verify_and_fix_with_zero_rounds_only_reports():
    editor, llm = _editor([FLAGGED], max_fix_rounds=0)

    result = editor.verify_and_fix(_state())

    assert llm.calls == 1
    assert result.blog_final == BLOG
    assert "50%" in result.grounding_notes


def test_verify_and_fix_reports_claims_still_flagged_after_fixing():
    still_flagged = '- CLAIM: "new results" | PROBLEM: vague, not in research | FIX: remove'
    editor, llm = _editor([FLAGGED, FIXED_BLOG, still_flagged])

    result = editor.verify_and_fix(_state())

    assert llm.calls == 3  # one round only (default max_fix_rounds=1)
    assert len(result.grounding_fixes) == 1
    assert '"new results"' in result.grounding_notes


def test_verify_and_fix_strips_invented_citations_from_fix():
    editor, _ = _editor([FLAGGED, FIXED_BLOG + " Extra claim [7].", ALL_CLEAR])

    result = editor.verify_and_fix(_state())

    assert "[7]" not in result.blog_final


def test_verify_and_fix_resets_previous_fixes():
    editor, _ = _editor([ALL_CLEAR])
    state = _state()
    state.grounding_fixes = ["from an earlier revision"]

    result = editor.verify_and_fix(state)

    assert result.grounding_fixes == []

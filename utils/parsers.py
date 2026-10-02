"""
Response Parsers - Parse structured responses from LLM outputs
"""

import re
from dataclasses import dataclass
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class EditorResponse:
    """Parsed response from the editor agent"""
    blog_content: str
    editorial_notes: str
    parse_success: bool = True


def parse_editor_response(response: str) -> EditorResponse:
    """
    Parse the editor's response into blog content and editorial notes.

    Handles multiple format variations:
    - Standard format: ---EDITED BLOG POST--- ... ---EDITORIAL NOTES---
    - Alternative markers: [EDITED BLOG POST] ... [EDITORIAL NOTES]
    - Fallback: treats entire response as blog content

    Args:
        response: Raw response string from the editor LLM

    Returns:
        EditorResponse with parsed content
    """
    # Try multiple marker patterns
    patterns = [
        # Standard format
        (r"---EDITED BLOG POST---\s*(.*?)\s*---EDITORIAL NOTES---\s*(.*)", re.DOTALL),
        # Alternative with brackets
        (r"\[EDITED BLOG POST\]\s*(.*?)\s*\[EDITORIAL NOTES\]\s*(.*)", re.DOTALL),
        # Markdown headers
        (r"#+\s*EDITED BLOG POST\s*(.*?)\s*#+\s*EDITORIAL NOTES\s*(.*)", re.DOTALL),
        # Just editorial notes at end
        (r"(.*?)---EDITORIAL NOTES---\s*(.*)", re.DOTALL),
        (r"(.*?)\[EDITORIAL NOTES\]\s*(.*)", re.DOTALL),
    ]

    for pattern, flags in patterns:
        match = re.search(pattern, response, flags)
        if match:
            blog_content = match.group(1).strip()
            editorial_notes = match.group(2).strip() if len(match.groups()) > 1 else "Editorial review completed"

            # Validate we got meaningful content
            if len(blog_content) > 100:  # Reasonable minimum for a blog post
                logger.debug(f"Parsed editor response using pattern: {pattern[:30]}...")
                return EditorResponse(
                    blog_content=blog_content,
                    editorial_notes=editorial_notes,
                    parse_success=True,
                )

    # Fallback: treat entire response as blog content
    logger.warning("Could not parse structured editor response, using full response as blog content")
    return EditorResponse(
        blog_content=response.strip(),
        editorial_notes="Editorial review completed (parsing fallback)",
        parse_success=False,
    )


@dataclass
class FeedbackAnalysis:
    """Analysis of human feedback for routing"""
    targets_writing: bool
    targets_editing: bool
    keywords_found: list[str]


def analyze_feedback(feedback: str) -> FeedbackAnalysis:
    """
    Analyze human feedback to determine routing.

    Args:
        feedback: Human feedback string

    Returns:
        FeedbackAnalysis with routing recommendations
    """
    feedback_lower = feedback.lower()
    keywords_found = []

    # Writing-related keywords
    writing_keywords = [
        "writing", "content", "structure", "rewrite", "tone", "style",
        "voice", "narrative", "storytelling", "flow", "organization",
        "boring", "interesting", "engaging", "dull", "expand", "shorten",
        "more detail", "less detail", "add more", "remove", "focus on",
    ]

    # Editing-related keywords
    editing_keywords = [
        "edit", "spelling", "grammar", "format", "formatting", "typo",
        "punctuation", "capitalization", "layout", "bullet", "heading",
        "polish", "proofread", "fix", "correct", "error", "mistake",
        "clarify", "clearer", "confusing", "unclear",
    ]

    targets_writing = False
    targets_editing = False

    for kw in writing_keywords:
        if kw in feedback_lower:
            targets_writing = True
            keywords_found.append(kw)

    for kw in editing_keywords:
        if kw in feedback_lower:
            targets_editing = True
            keywords_found.append(kw)

    return FeedbackAnalysis(
        targets_writing=targets_writing,
        targets_editing=targets_editing,
        keywords_found=keywords_found,
    )


def determine_revision_target(feedback: str) -> str:
    """
    Determine which agent should handle the revision.

    Args:
        feedback: Human feedback string

    Returns:
        "writer_revise" or "editor_revise"
    """
    analysis = analyze_feedback(feedback)

    # If explicitly targets writing, route to writer
    if analysis.targets_writing and not analysis.targets_editing:
        return "writer_revise"

    # If explicitly targets editing, route to editor
    if analysis.targets_editing and not analysis.targets_writing:
        return "editor_revise"

    # If both or neither, prefer editor for general improvements
    # (editor is better at polishing)
    return "editor_revise"


@dataclass
class GroundingIssue:
    """One claim the grounding check says the research doesn't support"""
    claim: str
    problem: str = ""
    fix: str = ""

    def describe(self) -> str:
        """Human-readable one-liner, used for notes and the auto-fix log"""
        text = f'"{self.claim}"' if self.problem else self.claim
        if self.problem:
            text += f" — {self.problem}"
        return text


# A bullet that just says "nothing to report" rather than flagging a claim.
_NO_ISSUE_RE = re.compile(
    r"^(none|n/?a|no (unsupported|issues|problems)|all (specific )?claims (are )?supported)\b",
    re.IGNORECASE,
)
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)\s*$")
_STRUCTURED_RE = re.compile(
    r'CLAIM:\s*"?(?P<claim>.*?)"?\s*\|\s*PROBLEM:\s*(?P<problem>.*?)\s*(?:\|\s*FIX:\s*(?P<fix>.*?))?\s*$',
    re.IGNORECASE,
)


def parse_grounding_issues(response: str) -> list[GroundingIssue]:
    """
    Parse the grounding check's response into individual flagged claims.

    The prompt asks for one bullet per claim as
    `- CLAIM: "..." | PROBLEM: ... | FIX: ...`, or the line
    `ALL CLAIMS SUPPORTED`. Small local models don't always follow that, so a
    plain bullet is still treated as one flagged claim, and bullets that just
    say "none" are ignored.

    Args:
        response: Raw grounding-check LLM output

    Returns:
        Flagged claims, in order (empty if everything is supported)
    """
    issues = []
    for line in (response or "").splitlines():
        bullet = _BULLET_RE.match(line)
        if not bullet:
            continue
        body = bullet.group(1).strip()
        structured = _STRUCTURED_RE.search(body)
        if structured:
            claim = structured.group("claim").strip().strip('"“”')
            if claim:
                issues.append(GroundingIssue(
                    claim=claim,
                    problem=structured.group("problem").strip(),
                    fix=(structured.group("fix") or "").strip(),
                ))
        elif not _NO_ISSUE_RE.match(body.strip("*_ ")):
            issues.append(GroundingIssue(claim=body))
    return issues


def format_grounding_notes(response: str, issues: list[GroundingIssue]) -> str:
    """
    Turn the raw grounding response into the readable notes shown to the user.

    Args:
        response: Raw grounding-check LLM output
        issues: What parse_grounding_issues() found in it

    Returns:
        Markdown bullet list of flagged claims, or a one-line all-clear
    """
    if issues:
        lines = []
        for issue in issues:
            line = f"- {issue.describe()}"
            if issue.fix and issue.fix.lower().rstrip(".") != "remove":
                line += f" (research supports: {issue.fix})"
            lines.append(line)
        return "\n".join(lines)
    text = (response or "").strip()
    if not text or "ALL CLAIMS SUPPORTED" in text.upper():
        return "All specific claims are supported by the research."
    return text


_PREAMBLE_RE = re.compile(r"^\s*(here(?:'s| is)|below is|sure|okay|certainly)\b[^\n]*:\s*\n+", re.IGNORECASE)


def strip_response_preamble(response: str) -> str:
    """Drop a chatty "Here is the corrected post:" opener a model sometimes adds."""
    return _PREAMBLE_RE.sub("", response or "", count=1).strip()


def format_grounding_section(grounding_notes: str, grounding_fixes: list[str]) -> str:
    """
    The "Grounding Check" section appended to an exported post (CLI save and
    web /download), listing what was auto-corrected and anything still flagged.

    Args:
        grounding_notes: Notes from the last grounding check
        grounding_fixes: Claims auto-corrected before review

    Returns:
        Markdown section (with leading blank lines), or "" if there's nothing to report
    """
    if not grounding_notes and not grounding_fixes:
        return ""
    section = "\n\n## Grounding Check\n\n"
    if grounding_fixes:
        section += "**Auto-corrected before review:**\n\n"
        section += "\n".join(f"- {fix}" for fix in grounding_fixes) + "\n\n"
        if grounding_notes:
            section += "**After correction:**\n\n"
    return section + (grounding_notes or "") + "\n"

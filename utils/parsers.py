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

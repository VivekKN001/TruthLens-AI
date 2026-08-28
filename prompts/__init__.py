"""
Prompts Package - Category-specific system prompts for agents
"""

from prompts.researcher_prompts import (
    RESEARCHER_SCIENCE,
    RESEARCHER_POLITICS,
    RESEARCHER_GAMING,
    RESEARCHER_DEFAULT,
)
from prompts.writer_prompts import (
    WRITER_SCIENCE,
    WRITER_POLITICS,
    WRITER_GAMING,
    WRITER_DEFAULT,
)
from prompts.editor_prompts import (
    EDITOR_SCIENCE,
    EDITOR_POLITICS,
    EDITOR_GAMING,
    EDITOR_DEFAULT,
)

__all__ = [
    # Researcher prompts
    "RESEARCHER_SCIENCE",
    "RESEARCHER_POLITICS",
    "RESEARCHER_GAMING",
    "RESEARCHER_DEFAULT",
    # Writer prompts
    "WRITER_SCIENCE",
    "WRITER_POLITICS",
    "WRITER_GAMING",
    "WRITER_DEFAULT",
    # Editor prompts
    "EDITOR_SCIENCE",
    "EDITOR_POLITICS",
    "EDITOR_GAMING",
    "EDITOR_DEFAULT",
]

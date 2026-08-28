"""
Agents Package - Specialized AI agents for content creation

Available agents:
- ResearcherAgent: Conducts web research using DuckDuckGo (free, no API key)
- WriterAgent: Creates blog posts from research
- EditorAgent: Reviews and polishes content
"""

from agents.base import BaseAgent
from agents.researcher import ResearcherAgent
from agents.writer import WriterAgent
from agents.editor import EditorAgent

__all__ = [
    "BaseAgent",
    "ResearcherAgent",
    "WriterAgent",
    "EditorAgent",
]

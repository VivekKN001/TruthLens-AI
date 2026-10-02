"""
State Management - Pydantic models for workflow state
"""

from typing import Optional
from pydantic import BaseModel, Field

from config import Category


class AgentState(BaseModel):
    """
    State object for the multi-agent workflow.

    This state is passed through all workflow nodes and accumulates
    results from each agent.
    """

    # User inputs
    category: str = Field(
        default="",
        description="Category: Science, Politics, or Gaming"
    )
    topic: str = Field(
        default="",
        description="User-provided topic for research and writing"
    )

    # Research phase
    research_content: str = Field(
        default="",
        description="Synthesized research data from web sources"
    )
    research_sources: list[str] = Field(
        default_factory=list,
        description="URLs and sources used in research"
    )

    # Writing phase
    blog_draft: str = Field(
        default="",
        description="Initial blog post draft from writer"
    )

    # Editing phase
    blog_final: str = Field(
        default="",
        description="Final polished blog post from editor"
    )

    # Grounding check (post-edit)
    grounding_notes: str = Field(
        default="",
        description="LLM assessment of blog claims not clearly supported by research_content"
    )
    grounding_fixes: list[str] = Field(
        default_factory=list,
        description="Claims the editor auto-corrected after the grounding check flagged them (latest verify pass)"
    )

    # Human feedback
    human_feedback: str = Field(
        default="",
        description="Human reviewer's feedback and change requests"
    )
    human_approved: bool = Field(
        default=False,
        description="Whether human approved the final content"
    )

    # Iteration tracking
    iteration_count: int = Field(
        default=0,
        description="Number of revision cycles completed"
    )
    max_iterations: int = Field(
        default=3,
        description="Maximum allowed revision iterations"
    )

    # Metadata
    messages: list[str] = Field(
        default_factory=list,
        description="Audit log of all agent actions"
    )

    class Config:
        """Pydantic configuration"""
        # Allow mutation of state (needed for workflow)
        frozen = False

    def is_valid_category(self) -> bool:
        """Check if the category is valid"""
        try:
            Category(self.category)
            return True
        except ValueError:
            return self.category.lower() in ["science", "politics", "gaming"]

    def can_iterate(self) -> bool:
        """Check if another iteration is allowed"""
        return self.iteration_count < self.max_iterations

    def add_message(self, message: str) -> None:
        """Add a message to the audit log"""
        self.messages.append(message)


class ResearcherOutput(BaseModel):
    """Output from researcher agent"""
    research: str = Field(description="Synthesized research content")
    sources: list[str] = Field(description="Source URLs")


class WriterOutput(BaseModel):
    """Output from writer agent"""
    blog_draft: str = Field(description="Draft blog post content")


class EditorOutput(BaseModel):
    """Output from editor agent"""
    blog_final: str = Field(description="Final polished blog post")
    suggestions: str = Field(default="", description="Editorial suggestions")

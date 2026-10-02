"""Writer Agent - Transforms research into blog content"""

from typing import Callable, Optional, Dict
from langchain_ollama import ChatOllama

from agents.base import BaseAgent
from config import Settings, settings, LLMConfig
from state import AgentState
from prompts.citation_rules import CITATION_RULES, KEEP_CITATIONS_RULE
from prompts.writer_prompts import (
    WRITER_SCIENCE,
    WRITER_POLITICS,
    WRITER_GAMING,
    WRITER_DEFAULT,
)


class WriterAgent(BaseAgent):
    """Agent responsible for writing blog posts from research"""

    AGENT_NAME = "writer"
    AGENT_ICON = "✍️"

    # Target audience descriptions by category
    AUDIENCES = {
        "science": "Students, academics, and curious learners",
        "politics": "Engaged citizens and voters seeking truth",
        "gaming": "Gamers of all levels and gaming enthusiasts",
    }
    DEFAULT_AUDIENCE = "General readers"

    def __init__(
        self,
        llm: Optional[ChatOllama] = None,
        llm_config: Optional[LLMConfig] = None,
        app_settings: Optional[Settings] = None,
    ):
        super().__init__(llm, llm_config, app_settings)

    def _get_default_llm_config(self) -> LLMConfig:
        return settings.llm.writer

    def _load_prompts(self) -> Dict[str, str]:
        return {
            "science": WRITER_SCIENCE,
            "politics": WRITER_POLITICS,
            "gaming": WRITER_GAMING,
            "default": WRITER_DEFAULT,
        }

    def _get_audience(self, category: str) -> str:
        """
        Get audience description for a category.

        Args:
            category: Content category

        Returns:
            Audience description string
        """
        return self.AUDIENCES.get(category.lower(), self.DEFAULT_AUDIENCE)

    def _build_writing_prompt(self, state: AgentState) -> str:
        """
        Build the prompt for writing a blog post.

        Args:
            state: Current agent state

        Returns:
            Writing prompt string
        """
        system_prompt = self.get_system_prompt(state.category)
        audience = self._get_audience(state.category)

        return f"""
{system_prompt}

CRITICAL: You are writing ONLY about this specific topic:
TOPIC: {state.topic}
CATEGORY: {state.category}

---RESEARCH DATA---
{state.research_content}

---TASK---
Write an engaging, well-structured blog post about ONLY: "{state.topic}"

STRICT REQUIREMENTS:
- Every paragraph must be directly about "{state.topic}"
- Do NOT include unrelated topics or tangents
- Do NOT write about other subjects, even if mentioned in research
- Stay 100% focused on "{state.topic}"
- NEVER repeat the same point, fact, or sentence in more than one section. Before writing each
  new section, mentally check it doesn't restate something already covered earlier in the post.
- Do NOT pad length by rephrasing earlier paragraphs. Only write as much as the research actually
  supports - a well-argued shorter post beats a repetitive longer one.
- Only write about what the research data actually supports. If the research doesn't cover something,
  leave it out rather than inventing specifics (numbers, quotes, names, dates) that aren't in the
  research.
{CITATION_RULES}

Guidelines:
- Target audience: {audience}
- Make it informative but accessible
- Use the research data provided as your factual grounding
- Length: as long as the research genuinely supports, up to about 1500-2000 words. Do not stretch
  thin research to hit a word count.
- Make it compelling and keep readers engaged

Write the blog post now (ONLY about "{state.topic}"):
"""

    def _build_revision_prompt(self, state: AgentState) -> str:
        """
        Build the prompt for revising a blog post based on feedback.

        Args:
            state: Current agent state

        Returns:
            Revision prompt string
        """
        system_prompt = self.get_system_prompt(state.category)

        return f"""
{system_prompt}

CRITICAL: This blog is ONLY about: "{state.topic}" (Category: {state.category})
Do NOT add content about other topics.

---ORIGINAL BLOG POST---
{state.blog_draft}

---HUMAN FEEDBACK---
{state.human_feedback}

---TASK---
Revise the blog post based on the human feedback provided above.
- Incorporate their suggestions and make improvements
- Keep the focus ONLY on "{state.topic}"
- Do NOT add unrelated content
{KEEP_CITATIONS_RULE}

Revised blog post (staying focused on "{state.topic}"):
"""

    def write_blog(self, state: AgentState, on_token: Optional[Callable[[str], None]] = None) -> AgentState:
        """
        Write a blog post based on research.

        Args:
            state: Current agent state
            on_token: Optional callback invoked with each chunk of the
                response as it streams in (e.g. for a live UI)

        Returns:
            Updated agent state with blog draft
        """
        self.logger.info(f"Creating blog post on '{state.topic}'")

        # No re-truncation here: the researcher already sized research_content
        # to fit the model's context window (settings.search.max_content_chars).
        # Truncating it a second time here was starving the writer of material
        # it was still being asked to write 1500-2500 words about - the main
        # cause of hallucination and repeated filler content.
        writing_prompt = self._build_writing_prompt(state)

        fallback_content = f"# {state.topic}\n\n{state.research_content[:2000]}"
        blog_draft = self._invoke_llm_stream_safe(writing_prompt, on_token=on_token, fallback=fallback_content)

        # Update state
        state.blog_draft = self._clean_citations(state, blog_draft)
        state.messages.append(f"Writer: Created blog draft ({len(blog_draft)} characters)")

        self.logger.result("Blog draft created", f"{len(blog_draft)} characters")

        return state

    def rewrite_with_feedback(self, state: AgentState, on_token: Optional[Callable[[str], None]] = None) -> AgentState:
        """
        Rewrite blog post based on human feedback.

        Args:
            state: Current agent state
            on_token: Optional callback invoked with each chunk of the
                response as it streams in (e.g. for a live UI)

        Returns:
            Updated agent state with revised blog draft
        """
        self.logger.info("Revising based on feedback")

        # Build and send revision prompt
        revision_prompt = self._build_revision_prompt(state)
        revised_blog = self._invoke_llm_stream_safe(revision_prompt, on_token=on_token, fallback=state.blog_draft)

        # Update state
        state.blog_draft = self._clean_citations(state, revised_blog)
        state.iteration_count += 1
        state.messages.append(
            f"Writer: Revised blog draft based on feedback (iteration {state.iteration_count})"
        )

        self.logger.result("Blog revised", f"iteration {state.iteration_count}")

        return state

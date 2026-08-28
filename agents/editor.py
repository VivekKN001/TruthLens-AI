"""Editor Agent - Reviews and polishes blog content"""

from typing import Callable, Optional, Dict, Tuple
from langchain_ollama import ChatOllama

from agents.base import BaseAgent
from config import Settings, settings, LLMConfig
from state import AgentState
from utils.parsers import parse_editor_response
from prompts.editor_prompts import (
    EDITOR_SCIENCE,
    EDITOR_POLITICS,
    EDITOR_GAMING,
    EDITOR_DEFAULT,
)


class EditorAgent(BaseAgent):
    """Agent responsible for editing and polishing blog posts"""

    AGENT_NAME = "editor"
    AGENT_ICON = "✏️"

    def __init__(
        self,
        llm: Optional[ChatOllama] = None,
        llm_config: Optional[LLMConfig] = None,
        app_settings: Optional[Settings] = None,
    ):
        super().__init__(llm, llm_config, app_settings)

    def _get_default_llm_config(self) -> LLMConfig:
        return settings.llm.editor

    def _load_prompts(self) -> Dict[str, str]:
        return {
            "science": EDITOR_SCIENCE,
            "politics": EDITOR_POLITICS,
            "gaming": EDITOR_GAMING,
            "default": EDITOR_DEFAULT,
        }

    def _build_edit_prompt(self, state: AgentState) -> str:
        """
        Build the prompt for editing a blog post.

        Args:
            state: Current agent state

        Returns:
            Edit prompt string
        """
        system_prompt = self.get_system_prompt(state.category)

        return f"""
{system_prompt}

CRITICAL: This blog is ONLY about: "{state.topic}" (Category: {state.category})
Remove any content that is NOT about "{state.topic}".

---RESEARCH DATA (for fact-checking)---
{state.research_content}

---BLOG DRAFT TO EDIT---
{state.blog_draft}

---TASK---
1. REMOVE any content not directly about "{state.topic}"
2. Fact-check against the research data
3. Improve clarity and readability
4. Correct any errors or inaccuracies
5. Enhance structure and flow
6. Polish language and tone
7. Ensure it meets the {state.category} category standards
8. Find and MERGE OR DELETE any paragraphs/sections that repeat the same point, fact, or
   sentence made elsewhere in the draft - keep only the strongest version of each point
9. Where the draft uses a link/citation instead of actually explaining something, rewrite
   that section to explain it in prose; keep at most 3-4 inline source mentions total

Provide:
1. The fully edited and polished blog post (ONLY about "{state.topic}")
2. A summary of all changes and corrections made

Format your response as:
---EDITED BLOG POST---
[full polished blog post here - ONLY about "{state.topic}"]

---EDITORIAL NOTES---
[summary of changes and corrections]
"""

    def _build_revision_prompt(self, state: AgentState) -> str:
        """
        Build the prompt for revising based on human feedback.

        Args:
            state: Current agent state

        Returns:
            Revision prompt string
        """
        system_prompt = self.get_system_prompt(state.category)

        return f"""
{system_prompt}

---CURRENT BLOG POST---
{state.blog_final}

---HUMAN FEEDBACK---
{state.human_feedback}

---RESEARCH DATA (for reference)---
{state.research_content}

---TASK---
Revise the blog post based on the human feedback while:
1. Maintaining factual accuracy
2. Improving the areas mentioned in feedback
3. Keeping the overall structure and quality
4. Ensuring all changes are aligned with the category standards

Provide the revised blog post:
"""

    def _build_final_review_prompt(self, state: AgentState) -> str:
        """
        Build the prompt for final quality review.

        Args:
            state: Current agent state

        Returns:
            Final review prompt string
        """
        system_prompt = self.get_system_prompt(state.category)
        sources_preview = ", ".join(state.research_sources[:5])

        return f"""
{system_prompt}

---FINAL BLOG POST---
{state.blog_final}

---RESEARCH SOURCES---
{sources_preview}

---TASK---
Provide a final quality check:

1. Is the content accurate and well-researched?
2. Is it appropriate for the target audience?
3. Are there any logical gaps or unclear sections?
4. Is the tone consistent with the category?
5. Are sources properly cited?
6. Any final suggestions for improvement?

Provide a brief final assessment with recommendations.
"""

    def edit_blog(self, state: AgentState, on_token: Optional[Callable[[str], None]] = None) -> AgentState:
        """
        Edit and polish the blog post.

        Args:
            state: Current agent state
            on_token: Optional callback invoked with each chunk of the raw
                response as it streams in (e.g. for a live UI). Note this is
                the raw, unparsed response including the section markers.

        Returns:
            Updated agent state with polished blog
        """
        self.logger.info("Reviewing and polishing blog post")

        # IMPORTANT: previously this truncated blog_draft (and the research used
        # to fact-check it) to 2500 characters before editing - meaning the editor
        # only ever saw roughly the first 20% of a full-length draft, then had to
        # invent/reconstruct the rest of the "fully polished blog post" from
        # nothing. That was the main source of duplicated and hallucinated
        # content in the final output. With a local model and a larger context
        # window there's no need to truncate - the editor now sees the whole
        # draft and the whole research.
        edit_prompt = self._build_edit_prompt(state)

        editor_response = self._invoke_llm_stream_safe(edit_prompt, on_token=on_token, fallback=state.blog_draft)

        # Parse the response using robust parser
        parsed = parse_editor_response(editor_response)

        if not parsed.parse_success:
            self.logger.warning("Used fallback parsing for editor response")

        # Update state
        state.blog_final = parsed.blog_content
        state.messages.append(
            f"Editor: Polished blog post. Notes: {len(parsed.editorial_notes)} chars"
        )

        self.logger.result("Blog edited and polished", "complete")
        self.logger.result("Editorial notes", f"{len(parsed.editorial_notes)} characters")

        return state

    def _build_grounding_prompt(self, state: AgentState) -> str:
        """
        Build the prompt for checking whether the final blog's specific
        claims are actually supported by the research data.

        Args:
            state: Current agent state

        Returns:
            Grounding-check prompt string
        """
        return f"""You are fact-checking a blog post against the research it was supposed to be based on.

---RESEARCH DATA---
{state.research_content}

---BLOG POST---
{state.blog_final or state.blog_draft}

---TASK---
List any specific factual claims in the blog post - numbers, statistics, dates, names, direct quotes -
that are NOT clearly supported by the research data above. For each one, quote the claim and briefly
say why it isn't supported (e.g. "not mentioned in research" or "research gives a different figure").

If every specific claim in the blog is supported by the research, say so in one line instead of
inventing issues. Be concise - a short bullet list, not an essay. Do not comment on writing style,
only on factual grounding.
"""

    def check_grounding(self, state: AgentState, on_token: Optional[Callable[[str], None]] = None) -> AgentState:
        """
        Check the final blog's claims against the research data, flagging
        anything the model asserted that the research doesn't actually
        support. This is a lightweight, LLM-based check - not a guarantee -
        but it surfaces the kind of specific-number/quote hallucination a
        small local model is prone to, before a human reviews the post.

        Args:
            state: Current agent state
            on_token: Optional callback invoked with each chunk of the
                response as it streams in (e.g. for a live UI)

        Returns:
            Updated agent state with grounding_notes populated
        """
        self.logger.info("Checking blog claims against research data")

        grounding_prompt = self._build_grounding_prompt(state)
        notes = self._invoke_llm_stream_safe(
            grounding_prompt,
            on_token=on_token,
            fallback="Grounding check unavailable (LLM call failed).",
        )

        state.grounding_notes = notes.strip()
        state.messages.append(f"Editor: Grounding check completed ({len(state.grounding_notes)} chars)")

        self.logger.result("Grounding check", "complete")

        return state

    def final_review(self, state: AgentState) -> Tuple[str, Dict[str, any]]:
        """
        Perform final review before human approval.

        Args:
            state: Current agent state

        Returns:
            Tuple of (final blog content, review metadata)
        """
        self.logger.info("Performing final review")

        # Build and send final review prompt
        review_prompt = self._build_final_review_prompt(state)
        assessment = self._invoke_llm_safe(
            review_prompt, fallback="Final review completed without issues"
        )

        self.logger.result("Final review", "completed")

        return state.blog_final, {
            "assessment": assessment,
            "sources": state.research_sources,
        }

    def rewrite_with_feedback(self, state: AgentState, on_token: Optional[Callable[[str], None]] = None) -> AgentState:
        """
        Rewrite blog post based on human feedback.

        Args:
            state: Current agent state
            on_token: Optional callback invoked with each chunk of the
                response as it streams in (e.g. for a live UI)

        Returns:
            Updated agent state with revised blog
        """
        self.logger.info("Revising based on feedback")

        # Build and send revision prompt
        revision_prompt = self._build_revision_prompt(state)
        revised_blog = self._invoke_llm_stream_safe(revision_prompt, on_token=on_token, fallback=state.blog_final)

        # Update state
        state.blog_final = revised_blog
        state.iteration_count += 1
        state.messages.append(
            f"Editor: Revised blog based on feedback (iteration {state.iteration_count})"
        )

        self.logger.result("Blog revised", f"iteration {state.iteration_count}")

        return state

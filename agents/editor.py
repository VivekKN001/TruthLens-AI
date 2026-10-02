"""Editor Agent - Reviews and polishes blog content"""

from typing import Callable, Optional, Dict, Tuple
from langchain_ollama import ChatOllama

from agents.base import BaseAgent
from config import Settings, settings, LLMConfig
from state import AgentState
from utils.parsers import (
    GroundingIssue,
    format_grounding_notes,
    parse_editor_response,
    parse_grounding_issues,
    strip_response_preamble,
)
from prompts.citation_rules import KEEP_CITATIONS_RULE
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
   that section to explain it in prose
10. Check the [n] citation markers: each must sit on a fact that the numbered research source
   actually contains. Remove claims whose cited source doesn't support them.
{KEEP_CITATIONS_RULE}

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
{KEEP_CITATIONS_RULE}

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
        state.blog_final = self._clean_citations(state, parsed.blog_content)
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
Find specific factual claims in the blog post - numbers, statistics, dates, names, direct quotes -
that are NOT clearly supported by the research data above.

Claims in the post carry [n] markers pointing at the numbered research sources. If a claim cites a
source number that doesn't actually contain that information, flag it as well.

Output one line per unsupported claim, in exactly this format:
- CLAIM: "<the exact sentence or phrase from the blog post>" | PROBLEM: <why it isn't supported, e.g. not in research / research gives a different figure> | FIX: <remove, or the wording the research actually supports>

If every specific claim is supported, output exactly this one line and nothing else:
ALL CLAIMS SUPPORTED

Do not invent issues, and do not comment on writing style - only on factual grounding.
"""

    def _build_fix_prompt(self, state: AgentState, issues: list[GroundingIssue]) -> str:
        """
        Build the prompt for correcting the claims the grounding check flagged.

        Args:
            state: Current agent state
            issues: Claims flagged by the grounding check

        Returns:
            Fix prompt string
        """
        flagged = "\n".join(
            f"{i}. CLAIM: {issue.claim}\n   PROBLEM: {issue.problem or 'not supported by the research'}"
            + (f"\n   SUGGESTED FIX: {issue.fix}" if issue.fix else "")
            for i, issue in enumerate(issues, 1)
        )
        return f"""You are correcting factual errors in a blog post before it is published.

---RESEARCH DATA (the only facts you may rely on)---
{state.research_content}

---BLOG POST---
{state.blog_final}

---FLAGGED CLAIMS---
A fact-checker found these claims are not supported by the research data:
{flagged}

---TASK---
Fix ONLY the flagged claims. For each one, either:
- correct it to say exactly what the research data supports (with the right [n] citation), or
- soften it to what the research actually establishes, or
- remove it, if the research doesn't support it at all.

Leave every other sentence, heading and citation exactly as it is - do not rewrite, shorten or restyle
the rest of the post. Do not add new facts. Do not add a "Sources" section.

Output ONLY the complete corrected blog post, from its first line to its last, with no commentary.
"""

    def _run_grounding_check(
        self, state: AgentState, on_token: Optional[Callable[[str], None]] = None
    ) -> list[GroundingIssue]:
        """One grounding-check LLM call: sets state.grounding_notes, returns the parsed issues."""
        grounding_prompt = self._build_grounding_prompt(state)
        response = self._invoke_llm_stream_safe(
            grounding_prompt,
            on_token=on_token,
            fallback="Grounding check unavailable (LLM call failed).",
        )
        issues = parse_grounding_issues(response)
        state.grounding_notes = format_grounding_notes(response, issues)
        state.messages.append(
            f"Editor: Grounding check completed ({len(issues)} claim(s) flagged)"
        )
        self.logger.result("Grounding check", f"{len(issues)} claim(s) flagged")
        return issues

    def fix_grounding_issues(
        self,
        state: AgentState,
        issues: list[GroundingIssue],
        on_token: Optional[Callable[[str], None]] = None,
    ) -> bool:
        """
        Ask the editor to correct only the flagged claims in blog_final.

        The result is rejected (blog_final left untouched) if the call fails,
        comes back unchanged, or is suspiciously short - a small local model
        asked to "return the whole post" sometimes returns a fragment, and
        silently publishing half a post is worse than leaving a flagged claim
        for the human to see.

        Args:
            state: Current agent state
            issues: Claims flagged by the grounding check
            on_token: Optional streaming callback

        Returns:
            True if blog_final was updated
        """
        original = state.blog_final
        response = self._invoke_llm_stream_safe(
            self._build_fix_prompt(state, issues), on_token=on_token, fallback=original
        )
        corrected = strip_response_preamble(response)

        if not corrected or corrected.strip() == original.strip():
            self.logger.warning("Fix pass made no changes")
            return False
        if len(corrected) < len(original) * 0.6:
            self.logger.warning(
                f"Rejected fix pass: output was {len(corrected)} chars vs {len(original)} original (likely truncated)"
            )
            state.messages.append("Editor: Discarded an auto-fix attempt that came back truncated")
            return False

        state.blog_final = self._clean_citations(state, corrected)
        state.messages.append(f"Editor: Auto-corrected {len(issues)} flagged claim(s)")
        return True

    def verify_and_fix(self, state: AgentState, on_token: Optional[Callable[[str], None]] = None) -> AgentState:
        """
        Grounding check that corrects what it finds.

        Runs the grounding check; if it flags claims, has the editor fix just
        those claims and checks again, for up to settings.workflow.max_fix_rounds
        rounds. Whatever is still flagged after that is left in grounding_notes
        for the human reviewer, and everything that was auto-corrected is
        recorded in grounding_fixes so the correction is visible, not silent.

        Args:
            state: Current agent state
            on_token: Optional streaming callback (receives all check/fix
                passes, separated by short "--" status lines)

        Returns:
            Updated agent state with blog_final, grounding_notes and grounding_fixes
        """
        self.logger.info("Verifying blog claims against research data")
        emit = on_token or (lambda text: None)
        state.grounding_fixes = []

        issues = self._run_grounding_check(state, on_token)
        for round_num in range(1, self._settings.workflow.max_fix_rounds + 1):
            if not issues:
                break
            emit(f"\n\n── Fixing {len(issues)} flagged claim(s) ──\n\n")
            if not self.fix_grounding_issues(state, issues, on_token):
                break
            state.grounding_fixes.extend(issue.describe() for issue in issues)
            emit("\n\n── Re-checking the corrected post ──\n\n")
            issues = self._run_grounding_check(state, on_token)

        if state.grounding_fixes:
            self.logger.result("Auto-corrected claims", len(state.grounding_fixes))
        return state

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
        self._run_grounding_check(state, on_token)
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
        state.blog_final = self._clean_citations(state, revised_blog)
        state.iteration_count += 1
        state.messages.append(
            f"Editor: Revised blog based on feedback (iteration {state.iteration_count})"
        )

        self.logger.result("Blog revised", f"iteration {state.iteration_count}")

        return state

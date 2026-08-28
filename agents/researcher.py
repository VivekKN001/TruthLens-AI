"""Researcher Agent - Conducts web research using DuckDuckGo (free, no API key)"""

import time
from typing import Callable, Optional, Dict, Set
from langchain_ollama import ChatOllama
from ddgs import DDGS

from agents.base import BaseAgent
from config import Settings, settings, LLMConfig
from state import AgentState
from prompts.researcher_prompts import (
    RESEARCHER_SCIENCE,
    RESEARCHER_POLITICS,
    RESEARCHER_GAMING,
    RESEARCHER_DEFAULT,
)


class ResearcherAgent(BaseAgent):
    """Agent responsible for researching topics using web search"""

    AGENT_NAME = "researcher"
    AGENT_ICON = "🔍"

    def __init__(
        self,
        llm: Optional[ChatOllama] = None,
        llm_config: Optional[LLMConfig] = None,
        search_client: Optional[DDGS] = None,
        app_settings: Optional[Settings] = None,
    ):
        super().__init__(llm, llm_config, app_settings)
        self._search_client = search_client or DDGS()

    def _get_default_llm_config(self) -> LLMConfig:
        return settings.llm.researcher

    def _load_prompts(self) -> Dict[str, str]:
        return {
            "science": RESEARCHER_SCIENCE,
            "politics": RESEARCHER_POLITICS,
            "gaming": RESEARCHER_GAMING,
            "default": RESEARCHER_DEFAULT,
        }

    def _search_topic(self, query: str) -> dict:
        """
        Search for information about a topic using DuckDuckGo.
        Free, no API key, no quota - unlike Tavily this never "runs out".

        DuckDuckGo scraping (ddgs isn't a real API) is prone to transient
        rate-limiting/network errors, so each query gets a few retries with
        backoff before it's treated as a genuine failure.

        Args:
            query: Search query string

        Returns:
            Search results dictionary in a Tavily-shaped format so the rest
            of the pipeline doesn't need to change.
        """
        max_attempts = self._settings.search.max_retries + 1
        last_exception: Optional[Exception] = None

        for attempt in range(1, max_attempts + 1):
            try:
                raw_results = self._search_client.text(
                    query, max_results=self._settings.search.max_results
                )
                results = [
                    {
                        "title": r.get("title", ""),
                        "url": r.get("href", ""),
                        "content": r.get("body", ""),
                    }
                    for r in raw_results
                ]
                return {"results": results, "answer": ""}
            except Exception as e:
                last_exception = e
                if attempt < max_attempts:
                    delay = self._settings.search.retry_delay * attempt
                    self.logger.warning(
                        f"Search attempt {attempt}/{max_attempts} failed for '{query}': {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)

        self.logger.error(f"Search failed for '{query}' after {max_attempts} attempts: {last_exception}")
        return {"results": [], "answer": ""}

    def _process_search_results(self, search_results: dict) -> tuple[str, list[str]]:
        """
        Process search results into structured research content.

        Args:
            search_results: Search results dict with "results" list

        Returns:
            Tuple of (processed content, list of source URLs)
        """
        content_parts = []
        sources = []

        for result in search_results.get("results", []):
            title = result.get("title", "")
            url = result.get("url", "")
            content_snippet = result.get("content", "")

            if title and content_snippet:
                content_parts.append(f"Source: {title}")
                if url:
                    content_parts.append(f"URL: {url}")
                    sources.append(url)
                content_parts.append(content_snippet)
                content_parts.append("-" * 80)

        return "\n".join(content_parts), sources

    def _generate_search_queries(self, topic: str, category: str) -> list[str]:
        """
        Generate search queries for comprehensive research.

        Args:
            topic: Research topic
            category: Content category

        Returns:
            List of search queries
        """
        return [
            f"{topic} {category}",
            topic,
            f"recent research {topic}",
            f"latest news {topic}",
        ]

    def _deduplicate_results(self, results: list[dict]) -> list[dict]:
        """
        Remove duplicate results based on URL.

        Args:
            results: List of search result dictionaries

        Returns:
            Deduplicated list of results
        """
        seen_urls: Set[str] = set()
        unique_results = []

        for result in results:
            url = result.get("url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_results.append(result)

        return unique_results

    def _build_synthesis_prompt(self, topic: str, category: str, research_content: str) -> str:
        """
        Build the prompt for synthesizing research.

        Args:
            topic: Research topic
            category: Content category
            research_content: Raw research content

        Returns:
            Synthesis prompt string
        """
        return f"""You are synthesizing research ONLY about this specific topic:

TOPIC: {topic}
CATEGORY: {category}

CRITICAL INSTRUCTIONS:
- ONLY include information directly related to "{topic}"
- Do NOT include information about other topics, even if present in the raw data
- Stay strictly focused on the given topic
- If the raw data contains irrelevant information, ignore it completely

RAW RESEARCH DATA:
{research_content}

Create a well-structured research summary about ONLY "{topic}".

Organize into these sections (only include sections relevant to the topic):
1. Topic Overview - What is {topic}?
2. Key Facts and Findings - Specific to {topic}
3. Important Concepts and Terms - Related to {topic}
4. Recent Developments - Latest news about {topic}
5. Multiple Perspectives - Different viewpoints on {topic}
6. Impact and Significance - Why {topic} matters
7. Areas of Uncertainty - What's still unknown about {topic}

REMEMBER: Every sentence must be about "{topic}". Do not deviate."""

    def research(self, state: AgentState, on_token: Optional[Callable[[str], None]] = None) -> AgentState:
        """
        Execute research on the given topic.

        Args:
            state: Current agent state
            on_token: Optional callback invoked with each chunk of the
                synthesis LLM response as it streams in (e.g. for a live UI)

        Returns:
            Updated agent state with research content
        """
        self.logger.info(f"Starting research on '{state.topic}' in category '{state.category}'")

        # Generate search queries
        search_queries = self._generate_search_queries(state.topic, state.category)

        # Collect all search results
        all_results = []
        all_sources: Set[str] = set()
        failed_queries = 0

        for i, query in enumerate(search_queries):
            self.logger.step(f"Searching: {query}")
            results = self._search_topic(query)

            if results.get("results"):
                all_results.extend(results["results"])
                all_sources.update(
                    r.get("url") for r in results["results"] if r.get("url")
                )
            else:
                failed_queries += 1

            # Small courtesy delay between queries - reduces the odds of
            # tripping DuckDuckGo's rate-limiting mid-research.
            if i < len(search_queries) - 1:
                time.sleep(self._settings.search.query_delay)

        if failed_queries == len(search_queries):
            self.logger.warning("All web searches failed or returned nothing - research may be thin")
            state.messages.append(
                "Researcher: Web search appears to be degraded (all queries failed) - "
                "continuing with whatever the model already knows"
            )

        # Deduplicate results
        unique_results = self._deduplicate_results(all_results)

        # Process results into structured format
        search_data = {"results": unique_results, "answer": ""}
        research_content, sources = self._process_search_results(search_data)

        # Truncate research content to fit in LLM context window
        max_chars = self._settings.search.max_content_chars
        if len(research_content) > max_chars:
            research_content = research_content[:max_chars] + "\n\n[Content truncated for processing...]"
            self.logger.step(f"Truncated research to {max_chars} chars for LLM")

        # Synthesize research using LLM
        synthesis_prompt = self._build_synthesis_prompt(state.topic, state.category, research_content)
        synthesized_research = self._invoke_llm_stream_safe(
            synthesis_prompt, on_token=on_token, fallback=research_content
        )

        # Update state
        state.research_content = synthesized_research
        state.research_sources = list(sources)[: self._settings.search.max_sources]
        state.messages.append(
            f"Researcher: Completed research on '{state.topic}' with {len(state.research_sources)} sources"
        )

        self.logger.result("Sources found", len(state.research_sources))
        self.logger.result("Research length", f"{len(synthesized_research)} characters")

        return state

"""Researcher Agent - Conducts web research using DuckDuckGo (free, no API key)"""

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional, Dict, Set
from langchain_ollama import ChatOllama
from ddgs import DDGS

from agents.base import BaseAgent
from config import Settings, settings, LLMConfig
from state import AgentState
from utils.fetcher import fetch_page_text, trim_to_boundary
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
        page_fetcher: Optional[Callable[[str], str]] = None,
    ):
        super().__init__(llm, llm_config, app_settings)
        self._search_client = search_client or DDGS()
        # url -> extracted article text. May raise (or return "") on failure.
        self._page_fetcher = page_fetcher or (
            lambda url: fetch_page_text(url, timeout=self._settings.search.fetch_timeout)
        )

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
        Process search results into numbered research content.

        Each source is labelled [1], [2], ... in the same order as the returned
        URL list, so a citation marker [n] anywhere downstream (synthesis,
        blog, grounding check) maps to research_sources[n - 1].

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

            if title and content_snippet and url:
                sources.append(url)
                kind = "full article text" if result.get("full_text") else "search snippet"
                content_parts.append(f"[{len(sources)}] {title}")
                content_parts.append(f"URL: {url} ({kind})")
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

    def _interleave_results(self, per_query_results: list[list[dict]]) -> list[dict]:
        """
        Round-robin across queries (1st result of each query, then 2nd, ...).

        Only the top max_sources results are kept, so taking them query by
        query would fill every slot from the first query or two and drop the
        "recent research" / "latest news" angles entirely.

        Args:
            per_query_results: One result list per search query

        Returns:
            Flat, interleaved list of results
        """
        interleaved = []
        longest = max((len(r) for r in per_query_results), default=0)
        for i in range(longest):
            for results in per_query_results:
                if i < len(results):
                    interleaved.append(results[i])
        return interleaved

    def _fetch_full_text(self, results: list[dict]) -> int:
        """
        Replace the snippet of up to fetch_pages results with the page's real
        article text. Many sites refuse non-browser clients outright (403), so
        every selected result is tried in parallel and the first fetch_pages
        that succeed, in ranking order, are used. A page that fails to
        download, isn't HTML, or yields less text than its snippet keeps the
        snippet.

        Args:
            results: Selected results to enrich in place

        Returns:
            Number of pages whose full text was used
        """
        search_cfg = self._settings.search
        targets = results if search_cfg.fetch_pages > 0 else []
        if not targets:
            return 0

        def fetch(result: dict) -> str:
            try:
                return self._page_fetcher(result["url"]) or ""
            except Exception as e:
                self.logger.debug(f"Could not read {result['url']}: {e}")
                return ""

        with ThreadPoolExecutor(max_workers=len(targets)) as pool:
            texts = list(pool.map(fetch, targets))

        fetched = 0
        for result, text in zip(targets, texts):
            if fetched >= search_cfg.fetch_pages:
                break
            if len(text) > max(200, len(result.get("content", ""))):
                result["content"] = trim_to_boundary(text, search_cfg.max_page_chars)
                result["full_text"] = True
                fetched += 1
        return fetched

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

CITATIONS:
- Each source in the raw data is numbered, like [3].
- After every specific fact you include (a number, date, name, quote, or finding), keep the
  number of the source it came from in brackets, e.g. "The trial enrolled 400 patients [3]."
- Only use source numbers that appear in the raw data. Never invent a number.
- Do not add a list of sources at the end - the numbered list is kept separately.

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

        # Collect search results, one list per query
        per_query_results: list[list[dict]] = []
        failed_queries = 0

        for i, query in enumerate(search_queries):
            self.logger.step(f"Searching: {query}")
            results = self._search_topic(query)

            if results.get("results"):
                per_query_results.append(results["results"])
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

        # Deduplicate, keeping a mix of all query angles, and cap at
        # max_sources so the numbered sources match research_sources exactly.
        unique_results = self._deduplicate_results(self._interleave_results(per_query_results))
        selected = [r for r in unique_results if r.get("title") and r.get("content")][
            : self._settings.search.max_sources
        ]

        # Read the actual pages behind the top results
        if selected and self._settings.search.fetch_pages > 0:
            self.logger.step(f"Reading up to {self._settings.search.fetch_pages} of {len(selected)} source pages")
            fetched = self._fetch_full_text(selected)
            self.logger.result("Full pages read", fetched)
            state.messages.append(
                f"Researcher: Read the full text of {fetched} of {len(selected)} sources "
                f"(the rest use search snippets)"
            )

        # Process results into numbered, structured format
        search_data = {"results": selected, "answer": ""}
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
        state.research_sources = sources
        state.messages.append(
            f"Researcher: Completed research on '{state.topic}' with {len(state.research_sources)} sources"
        )

        self.logger.result("Sources found", len(state.research_sources))
        self.logger.result("Research length", f"{len(synthesized_research)} characters")

        return state

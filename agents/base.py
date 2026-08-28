"""
Base Agent - Abstract base class for all agents
Provides common functionality and dependency injection support
"""

import time
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Callable, List

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

from config import Settings, settings, LLMConfig
from utils.logger import AgentLogger
from utils.retry import with_retry, RetryConfig


class BaseAgent(ABC):
    """
    Abstract base class for all TruthLens agents.

    Provides:
    - LLM initialization with dependency injection
    - Common prompt handling
    - Logging infrastructure
    - Retry logic for API calls
    """

    # Override in subclasses
    AGENT_NAME: str = "base"
    AGENT_ICON: str = "🤖"

    def __init__(
        self,
        llm: Optional[ChatOllama] = None,
        llm_config: Optional[LLMConfig] = None,
        app_settings: Optional[Settings] = None,
    ):
        """
        Initialize the agent.

        Args:
            llm: Optional pre-configured LLM (for testing/injection)
            llm_config: Optional LLM configuration override
            app_settings: Optional settings override
        """
        self._settings = app_settings or settings
        self._llm_config = llm_config or self._get_default_llm_config()
        self._llm = llm or self._create_llm()
        self._logger = AgentLogger(self.AGENT_NAME, self.AGENT_ICON)
        self._prompts = self._load_prompts()

    @abstractmethod
    def _get_default_llm_config(self) -> LLMConfig:
        """Get the default LLM configuration for this agent type"""
        pass

    @abstractmethod
    def _load_prompts(self) -> Dict[str, str]:
        """Load category-specific prompts for this agent"""
        pass

    def _create_llm(self) -> ChatOllama:
        """Create a local Ollama LLM - no API key, no cost, no rate limit"""
        return ChatOllama(
            model=self._llm_config.model,
            base_url=self._settings.ollama_base_url,
            temperature=self._llm_config.temperature,
            num_predict=self._llm_config.max_tokens,
            # Ollama's default context window is only 2048 tokens, which was
            # the root cause of silently cut-off research/drafts. Ask for more.
            num_ctx=self._llm_config.context_window,
        )

    def get_system_prompt(self, category: str) -> str:
        """
        Get the appropriate system prompt for a category.

        Args:
            category: Content category (Science, Politics, Gaming)

        Returns:
            System prompt string
        """
        category_lower = category.lower()
        default_prompt = self._prompts.get("default", "")
        return self._prompts.get(category_lower, default_prompt)

    @with_retry(config=RetryConfig(max_attempts=3, base_delay=1.0))
    def _invoke_llm(self, prompt: str, system_prompt: str = "") -> str:
        """
        Invoke the LLM with retry logic.

        Args:
            prompt: The user prompt to send to the LLM
            system_prompt: Optional system prompt

        Returns:
            LLM response content
        """
        messages = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=prompt))

        response = self._llm.invoke(messages)
        return response.content

    def _invoke_llm_safe(self, prompt: str, system_prompt: str = "", fallback: str = "") -> str:
        """
        Safely invoke the LLM with fallback on failure.

        Args:
            prompt: The user prompt to send to the LLM
            system_prompt: Optional system prompt
            fallback: Fallback value if invocation fails

        Returns:
            LLM response or fallback
        """
        try:
            return self._invoke_llm(prompt, system_prompt)
        except Exception as e:
            self._logger.error(f"LLM invocation failed: {e}")
            return fallback

    def _invoke_llm_stream(
        self,
        prompt: str,
        system_prompt: str = "",
        on_token: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
        Invoke the LLM token-by-token, with retry on failure.

        Unlike _invoke_llm, this can't use the @with_retry decorator directly
        since a failed stream must discard whatever partial text it produced
        and start the generation over - the decorator only knows about the
        return value, not the in-flight callback side effects.

        Args:
            prompt: The user prompt to send to the LLM
            system_prompt: Optional system prompt
            on_token: Optional callback invoked with each text chunk as it
                arrives (e.g. to push it to a UI). Not called with partial
                text from a failed attempt that gets retried.

        Returns:
            Full concatenated response content

        Raises:
            The last exception if every attempt fails
        """
        messages: List[Any] = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=prompt))

        retry_config = RetryConfig(max_attempts=3, base_delay=1.0)
        last_exception: Optional[Exception] = None

        for attempt in range(1, retry_config.max_attempts + 1):
            try:
                chunks: List[str] = []
                for chunk in self._llm.stream(messages):
                    text = chunk.content
                    if text:
                        chunks.append(text)
                        if on_token:
                            on_token(text)
                return "".join(chunks)
            except Exception as e:
                last_exception = e
                if attempt < retry_config.max_attempts:
                    delay = min(
                        retry_config.base_delay * (retry_config.exponential_base ** (attempt - 1)),
                        retry_config.max_delay,
                    )
                    self._logger.warning(
                        f"Stream attempt {attempt}/{retry_config.max_attempts} failed: {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)

        self._logger.error(f"All {retry_config.max_attempts} streaming attempts failed: {last_exception}")
        raise last_exception

    def _invoke_llm_stream_safe(
        self,
        prompt: str,
        system_prompt: str = "",
        on_token: Optional[Callable[[str], None]] = None,
        fallback: str = "",
    ) -> str:
        """Safely invoke the LLM as a stream, with fallback on failure."""
        try:
            return self._invoke_llm_stream(prompt, system_prompt, on_token)
        except Exception as e:
            self._logger.error(f"LLM stream invocation failed: {e}")
            return fallback

    @property
    def logger(self) -> AgentLogger:
        """Get the agent's logger"""
        return self._logger

    @property
    def llm(self) -> ChatOllama:
        """Get the agent's LLM"""
        return self._llm

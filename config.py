"""
Configuration Management - Centralized settings for TruthLens AI
Uses Pydantic for validation and environment variable loading
"""

import os
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class Category(str, Enum):
    """Available content categories"""
    SCIENCE = "Science"
    POLITICS = "Politics"
    GAMING = "Gaming"


class LLMConfig(BaseModel):
    """Configuration for LLM models"""
    # Using a local Ollama model - zero cost, no token limits, no API key
    model: str = Field(default="llama3.1:8b")
    temperature: float = Field(default=0.5, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096)
    # How large a context window to request from Ollama (num_ctx).
    # Local models default to a tiny 2048-token window unless told otherwise,
    # which is what was silently truncating research/drafts before.
    context_window: int = Field(default=16384)


class AgentConfigs(BaseModel):
    """Temperature and model settings for each agent"""
    researcher: LLMConfig = Field(default_factory=lambda: LLMConfig(temperature=0.2))
    writer: LLMConfig = Field(default_factory=lambda: LLMConfig(temperature=0.7))
    editor: LLMConfig = Field(default_factory=lambda: LLMConfig(temperature=0.3))


class SearchConfig(BaseModel):
    """Configuration for web search (DuckDuckGo - free, no API key, no quota)"""
    max_results: int = Field(default=6)  # Results per query
    max_sources: int = Field(default=8)
    # Local model + full context window means we don't need to starve the
    # writer of research material. Raised well above the old 3000-char cap.
    max_content_chars: int = Field(default=12000)
    # DuckDuckGo scraping (not a real API) is prone to transient rate-limiting.
    # Retry each query a couple of times before giving up on it.
    max_retries: int = Field(default=2)
    retry_delay: float = Field(default=2.0)
    # Small courtesy delay between the 4 queries in one research() call, to
    # avoid bursting requests in a way that's more likely to get rate-limited.
    query_delay: float = Field(default=1.0)


class WorkflowConfig(BaseModel):
    """Configuration for the workflow"""
    max_iterations: int = Field(default=3, ge=1, le=10)
    content_preview_length: int = Field(default=2000)
    research_preview_length: int = Field(default=1000)


class StorageConfig(BaseModel):
    """Configuration for run history persistence (SQLite)"""
    db_path: str = Field(default="data/truthlens.db")


class Settings(BaseModel):
    """Main application settings"""
    # Ollama runs locally - no API key needed, no cost, no rate limit.
    ollama_base_url: str = Field(default="http://localhost:11434")

    # Configurations
    llm: AgentConfigs = Field(default_factory=AgentConfigs)
    search: SearchConfig = Field(default_factory=SearchConfig)
    workflow: WorkflowConfig = Field(default_factory=WorkflowConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)

    # Logging
    log_level: str = Field(default="INFO")

    @classmethod
    def from_env(cls) -> "Settings":
        """Load settings from environment variables"""
        return cls(
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )

    def validate_ollama(self) -> tuple[bool, list[str]]:
        """Check that the local Ollama server is reachable and has the model."""
        errors = []
        try:
            import requests

            resp = requests.get(f"{self.ollama_base_url}/api/tags", timeout=3)
            resp.raise_for_status()
            tags = [m["name"] for m in resp.json().get("models", [])]
            wanted = self.llm.writer.model
            # Ollama tags sometimes omit ":latest" so compare loosely
            if not any(wanted == t or wanted.split(":")[0] == t.split(":")[0] for t in tags):
                errors.append(
                    f"Model '{wanted}' not found in Ollama. Run: ollama pull {wanted}"
                )
        except Exception as e:
            errors.append(
                f"Could not reach Ollama at {self.ollama_base_url} ({e}). "
                "Is 'ollama serve' running?"
            )
        return len(errors) == 0, errors


# Global settings instance
settings = Settings.from_env()

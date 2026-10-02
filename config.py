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
    # Search results only carry a 1-2 sentence snippet. Download the top N
    # pages and use their actual article text instead (0 disables this).
    fetch_pages: int = Field(default=5, ge=0)
    fetch_timeout: float = Field(default=8.0)
    # Per-page cap on extracted text, so ~5 full pages plus the remaining
    # snippets still fit inside max_content_chars.
    max_page_chars: int = Field(default=1800)


class WorkflowConfig(BaseModel):
    """Configuration for the workflow"""
    max_iterations: int = Field(default=3, ge=1, le=10)
    # After the grounding check flags claims, how many fix-and-recheck rounds
    # the editor gets before the post goes to the human (0 = only report).
    # Each round costs two extra LLM calls (fix + recheck).
    max_fix_rounds: int = Field(default=1, ge=0, le=3)
    content_preview_length: int = Field(default=2000)
    research_preview_length: int = Field(default=1000)


class StorageConfig(BaseModel):
    """Configuration for run history persistence (SQLite)"""
    db_path: str = Field(default="data/truthlens.db")
    # Postgres connection URL (e.g. a free Neon database). When set, history
    # goes there instead of the SQLite file - needed on hosts whose disk is
    # wiped on every restart.
    database_url: str = Field(default="")


# Hosted, OpenAI-compatible model APIs with a free tier. Each preset only
# fills in defaults - LLM_BASE_URL / LLM_MODEL / LLM_MAX_TOKENS /
# LLM_REASONING_EFFORT override any of them.
PROVIDER_PRESETS = {
    # Google AI Studio. Free-tier limits are shown in your AI Studio dashboard;
    # free-tier prompts may be used by Google to improve its products.
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-3.8-flash",
        "reasoning_effort": "low",
        "max_tokens": 4096,
    },
    # Groq. Free tier on gpt-oss is 8K tokens/minute and 200K tokens/day, so
    # replies are capped lower to keep each request under the per-minute limit
    # (expect a handful of articles per day).
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "openai/gpt-oss-120b",
        "reasoning_effort": "low",
        "max_tokens": 2500,
    },
}


class ProviderConfig(BaseModel):
    """
    Which LLM backend the agents use.

    "ollama" (default) is the local, free, no-key setup the project started
    with. "gemini" / "groq" use a hosted free tier (see PROVIDER_PRESETS), and
    "openai_compatible" is any other OpenAI-style API - set LLM_BASE_URL and
    LLM_MODEL yourself. All hosted options need LLM_API_KEY.
    """
    provider: str = "ollama"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    max_tokens: int = 0
    reasoning_effort: str = ""

    @property
    def is_hosted(self) -> bool:
        return self.provider != "ollama"

    def _preset(self, key: str):
        return PROVIDER_PRESETS.get(self.provider, {}).get(key)

    @property
    def resolved_base_url(self) -> str:
        return self.base_url or self._preset("base_url") or ""

    @property
    def resolved_model(self) -> str:
        return self.model or self._preset("model") or ""

    @property
    def resolved_max_tokens(self) -> int:
        return self.max_tokens or self._preset("max_tokens") or 0

    @property
    def resolved_reasoning_effort(self) -> str:
        return self.reasoning_effort or self._preset("reasoning_effort") or ""

    @classmethod
    def from_env(cls) -> "ProviderConfig":
        return cls(
            provider=os.getenv("LLM_PROVIDER", "ollama").strip().lower() or "ollama",
            base_url=os.getenv("LLM_BASE_URL", ""),
            api_key=os.getenv("LLM_API_KEY", ""),
            model=os.getenv("LLM_MODEL", ""),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "0") or 0),
            reasoning_effort=os.getenv("LLM_REASONING_EFFORT", ""),
        )


class AuthConfig(BaseModel):
    """
    Sign-in for the web UI (Google and/or GitHub OAuth).

    Sign-in is enabled as soon as at least one provider's client ID and secret
    are set. With none set (e.g. running on your own PC) the web UI stays
    sign-in-free and every run belongs to a single local user.
    """
    google_client_id: str = ""
    google_client_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    # Signs the session cookie. Must be set (and kept stable) in production,
    # or everyone is signed out whenever the server restarts.
    session_secret: str = ""
    # Public base URL of the deployed app, e.g. https://you-truthlens.hf.space.
    # Used to build OAuth callback URLs - behind a hosting proxy the server
    # can't reliably work out its own public https address.
    public_url: str = ""

    @property
    def providers(self) -> list[str]:
        configured = []
        if self.google_client_id and self.google_client_secret:
            configured.append("google")
        if self.github_client_id and self.github_client_secret:
            configured.append("github")
        return configured

    @property
    def enabled(self) -> bool:
        return bool(self.providers)

    @classmethod
    def from_env(cls) -> "AuthConfig":
        return cls(
            google_client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
            google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
            github_client_id=os.getenv("GITHUB_CLIENT_ID", ""),
            github_client_secret=os.getenv("GITHUB_CLIENT_SECRET", ""),
            session_secret=os.getenv("SESSION_SECRET", ""),
            public_url=os.getenv("PUBLIC_URL", "").rstrip("/"),
        )


class Settings(BaseModel):
    """Main application settings"""
    # Ollama runs locally - no API key needed, no cost, no rate limit.
    ollama_base_url: str = Field(default="http://localhost:11434")

    # Configurations
    llm: AgentConfigs = Field(default_factory=AgentConfigs)
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    workflow: WorkflowConfig = Field(default_factory=WorkflowConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)

    # Logging
    log_level: str = Field(default="INFO")

    @classmethod
    def from_env(cls) -> "Settings":
        """Load settings from environment variables"""
        return cls(
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            auth=AuthConfig.from_env(),
            provider=ProviderConfig.from_env(),
            storage=StorageConfig(database_url=os.getenv("DATABASE_URL", "")),
        )

    @property
    def model_label(self) -> str:
        """The model actually in use, for display."""
        return self.provider.resolved_model if self.provider.is_hosted else self.llm.writer.model

    def validate_llm(self) -> tuple[bool, list[str]]:
        """Check that whichever LLM backend is configured is reachable and usable."""
        if not self.provider.is_hosted:
            return self.validate_ollama()
        return self.validate_hosted()

    def validate_hosted(self) -> tuple[bool, list[str]]:
        """Check a hosted OpenAI-compatible API: key present, endpoint reachable, key accepted."""
        p = self.provider
        errors = []
        if p.provider not in PROVIDER_PRESETS and p.provider != "openai_compatible":
            errors.append(
                f"Unknown LLM_PROVIDER '{p.provider}'. Use ollama, "
                f"{', '.join(PROVIDER_PRESETS)} or openai_compatible."
            )
        if not p.resolved_base_url:
            errors.append("LLM_BASE_URL is not set.")
        if not p.resolved_model:
            errors.append("LLM_MODEL is not set.")
        if not p.api_key:
            errors.append(f"LLM_API_KEY is not set - create a free API key for {p.provider} and set it.")
        if errors:
            return False, errors

        try:
            import requests

            resp = requests.get(
                f"{p.resolved_base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {p.api_key}"},
                timeout=5,
            )
            # Groq answers a bad key with 401; Gemini with 400 "Please pass a valid API key".
            if resp.status_code in (400, 401, 403):
                errors.append(f"{p.provider} rejected LLM_API_KEY (HTTP {resp.status_code}). Check the key.")
            elif resp.status_code == 404:
                errors.append(f"{p.resolved_base_url} doesn't look like an OpenAI-compatible API (HTTP 404). Check LLM_BASE_URL.")
            elif resp.status_code >= 500:
                errors.append(f"{p.provider} API is having problems (HTTP {resp.status_code}).")
        except Exception as e:
            errors.append(f"Could not reach the {p.provider} API at {p.resolved_base_url} ({e}).")
        return len(errors) == 0, errors

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

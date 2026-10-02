"""Choosing the LLM backend: local Ollama (default) vs hosted OpenAI-compatible APIs."""

import requests

from agents.writer import WriterAgent
from config import LLMConfig, ProviderConfig, Settings


def _settings(**provider):
    s = Settings()
    s.provider = ProviderConfig(**provider)
    return s


def test_default_is_gemini():
    from langchain_openai import ChatOpenAI

    assert ProviderConfig().provider == "gemini"
    writer = WriterAgent(llm_config=LLMConfig(model="x"), app_settings=_settings(api_key="k"))
    assert isinstance(writer._llm, ChatOpenAI)
    assert writer._llm.model_name == "gemini-2.5-flash"


def test_env_defaults_to_gemini(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert ProviderConfig.from_env().provider == "gemini"
    monkeypatch.setenv("LLM_PROVIDER", " Ollama ")
    assert ProviderConfig.from_env().provider == "ollama"


def test_ollama_when_selected():
    from langchain_ollama import ChatOllama

    writer = WriterAgent(llm_config=LLMConfig(model="llama3.1:8b"), app_settings=_settings(provider="ollama"))
    assert isinstance(writer._llm, ChatOllama)


def test_gemini_preset_builds_openai_compatible_client():
    from langchain_openai import ChatOpenAI

    s = _settings(provider="gemini", api_key="k")
    writer = WriterAgent(llm_config=LLMConfig(model="ignored", temperature=0.7), app_settings=s)

    llm = writer._llm
    assert isinstance(llm, ChatOpenAI)
    assert llm.model_name == "gemini-2.5-flash"
    assert "generativelanguage.googleapis.com" in str(llm.openai_api_base)
    assert llm.temperature == 0.7
    assert llm.reasoning_effort == "low"
    assert s.model_label == "gemini-2.5-flash"


def test_env_style_overrides_beat_preset():
    s = _settings(provider="groq", api_key="k", model="openai/gpt-oss-20b", max_tokens=1000)
    llm = WriterAgent(llm_config=LLMConfig(model="x"), app_settings=s)._llm
    assert llm.model_name == "openai/gpt-oss-20b"
    assert llm.max_tokens == 1000


def test_groq_preset_caps_reply_length_for_its_per_minute_limit():
    llm = WriterAgent(llm_config=LLMConfig(model="x", max_tokens=4096), app_settings=_settings(provider="groq", api_key="k"))._llm
    assert llm.max_tokens == 2500


def test_validate_hosted_requires_api_key():
    ok, errors = _settings(provider="gemini").validate_llm()
    assert not ok
    assert any("LLM_API_KEY" in e for e in errors)


def test_validate_hosted_unknown_provider():
    ok, errors = _settings(provider="openia", api_key="k").validate_llm()
    assert not ok
    assert any("Unknown LLM_PROVIDER" in e for e in errors)


def test_validate_hosted_reports_rejected_key(monkeypatch):
    class Resp:
        status_code = 401

    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    ok, errors = _settings(provider="groq", api_key="bad").validate_llm()
    assert not ok and "rejected" in errors[0]


def test_validate_hosted_ok(monkeypatch):
    class Resp:
        status_code = 200

    calls = []
    monkeypatch.setattr(requests, "get", lambda url, **k: calls.append((url, k["headers"])) or Resp())

    ok, errors = _settings(provider="groq", api_key="secret").validate_llm()

    assert ok and errors == []
    assert calls[0][0] == "https://api.groq.com/openai/v1/models"
    assert calls[0][1]["Authorization"] == "Bearer secret"


def test_friendly_error_explains_rate_limits(monkeypatch):
    import server
    from config import settings

    monkeypatch.setattr(settings, "provider", ProviderConfig(provider="groq", api_key="k"))
    message = server._friendly_error(RuntimeError("Error code: 429 - rate_limit_exceeded"), "writing")
    assert "rate limit" in message and "retry" in message


def test_validate_hosted_treats_gemini_400_as_rejected_key(monkeypatch):
    class Resp:
        status_code = 400

    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    ok, errors = _settings(provider="gemini", api_key="bad").validate_llm()
    assert not ok and "rejected" in errors[0]


# ---------- Cut-off streams from hosted models ----------

class _StreamLLM:
    """Yields scripted streams: each is a list of (text, finish_reason) pairs."""

    def __init__(self, streams):
        self.streams = list(streams)
        self.calls = 0

    def stream(self, messages):
        from tests.fakes import FakeChunk

        stream = self.streams[min(self.calls, len(self.streams) - 1)]
        self.calls += 1
        for text, finish in stream:
            yield FakeChunk(text, {"finish_reason": finish} if finish else {})


def test_hosted_stream_cut_off_is_retried():
    llm = _StreamLLM([[("Half an art", None)], [("Full ", None), ("article.", "stop")]])
    writer = WriterAgent(llm=llm, llm_config=LLMConfig(model="x"), app_settings=_settings(provider="gemini", api_key="k"))
    tokens = []

    out = writer._invoke_llm_stream("prompt", on_token=tokens.append)

    assert out == "Full article."
    assert llm.calls == 2
    assert any("cut off" in t for t in tokens)


def test_hosted_stream_that_never_finishes_falls_back():
    llm = _StreamLLM([[("Half", None)]])
    writer = WriterAgent(llm=llm, llm_config=LLMConfig(model="x"), app_settings=_settings(provider="gemini", api_key="k"))

    assert writer._invoke_llm_stream_safe("prompt", fallback="FALLBACK") == "FALLBACK"
    assert llm.calls == 3


def test_ollama_streams_are_not_finish_checked():
    llm = _StreamLLM([[("Local ", None), ("reply", None)]])
    writer = WriterAgent(llm=llm, llm_config=LLMConfig(model="x"), app_settings=_settings(provider="ollama"))

    assert writer._invoke_llm_stream("prompt") == "Local reply"
    assert llm.calls == 1


def test_max_tokens_finish_is_accepted_not_retried():
    llm = _StreamLLM([[("Long reply", "length")]])
    writer = WriterAgent(llm=llm, llm_config=LLMConfig(model="x"), app_settings=_settings(provider="gemini", api_key="k"))

    assert writer._invoke_llm_stream("prompt") == "Long reply"
    assert llm.calls == 1

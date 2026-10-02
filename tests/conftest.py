"""Shared pytest fixtures - none of these tests should touch real Ollama/network."""

import time as time_module

import requests

import pytest


@pytest.fixture(autouse=True)
def _no_real_sleeps(monkeypatch):
    """Every retry/backoff path in the app calls time.sleep - skip the wait in tests."""
    monkeypatch.setattr(time_module, "sleep", lambda seconds: None)


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """
    ResearcherAgent downloads the pages behind its search results by default.
    Make any real HTTP request fail fast, so tests that don't inject a
    page_fetcher exercise the "couldn't read page, keep the snippet" path
    instead of hitting the network.
    """
    def blocked(*args, **kwargs):
        raise RuntimeError("network access is disabled in tests")

    monkeypatch.setattr(requests, "get", blocked)


@pytest.fixture(autouse=True)
def _default_settings(monkeypatch):
    """
    Tests must not depend on the developer's .env: reset the settings it can
    change to defaults - local Ollama provider (so fake LLMs aren't treated as
    a hosted API), no DATABASE_URL (so nothing ever writes to a real
    Postgres), and sign-in off. Tests that need otherwise set it themselves.
    """
    from config import AuthConfig, ProviderConfig, settings

    monkeypatch.setattr(settings, "provider", ProviderConfig())
    monkeypatch.setattr(settings, "auth", AuthConfig())
    monkeypatch.setattr(settings.storage, "database_url", "")

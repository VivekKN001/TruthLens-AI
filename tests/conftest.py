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

"""Shared pytest fixtures - none of these tests should touch real Ollama/network."""

import time as time_module

import pytest


@pytest.fixture(autouse=True)
def _no_real_sleeps(monkeypatch):
    """Every retry/backoff path in the app calls time.sleep - skip the wait in tests."""
    monkeypatch.setattr(time_module, "sleep", lambda seconds: None)

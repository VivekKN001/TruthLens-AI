"""Test doubles for the LLM and search clients - keep the test suite free of real network/Ollama calls."""


class FakeChunk:
    def __init__(self, content):
        self.content = content


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeLLM:
    """Stands in for ChatOllama wherever agents/base.py expects one. Supports .invoke() and .stream()."""

    def __init__(self, response=None, chunks=None, fail_times=0, exception=None):
        self.chunks = chunks if chunks is not None else [response or "fake response"]
        self.response = response if response is not None else "".join(self.chunks)
        self.fail_times = fail_times
        self.exception = exception or RuntimeError("simulated LLM failure")
        self.calls = 0

    def _maybe_fail(self):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exception

    def invoke(self, messages):
        self._maybe_fail()
        return FakeMessage(self.response)

    def stream(self, messages):
        self._maybe_fail()
        for chunk in self.chunks:
            yield FakeChunk(chunk)


class FakeSearchClient:
    """Stands in for ddgs.DDGS wherever ResearcherAgent expects one. Supports .text()."""

    def __init__(self, results=None, fail_times=0, exception=None):
        self.results = results if results is not None else []
        self.fail_times = fail_times
        self.exception = exception or RuntimeError("simulated search failure")
        self.calls = 0

    def text(self, query, max_results=10):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exception
        return self.results

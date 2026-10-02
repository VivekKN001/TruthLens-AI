from agents.editor import EditorAgent
from agents.researcher import ResearcherAgent
from agents.writer import WriterAgent
from config import LLMConfig
from state import AgentState
from tests.fakes import FakeLLM, FakeSearchClient


def _llm_config():
    return LLMConfig(model="fake", temperature=0.5)


# ---------- WriterAgent ----------

def test_writer_write_blog_streams_tokens_and_sets_draft():
    fake_llm = FakeLLM(chunks=["Hello ", "world."])
    writer = WriterAgent(llm=fake_llm, llm_config=_llm_config())
    state = AgentState(category="Science", topic="Test", research_content="facts")

    collected = []
    result = writer.write_blog(state, on_token=collected.append)

    assert result.blog_draft == "Hello world."
    assert collected == ["Hello ", "world."]
    assert any("Writer" in m for m in result.messages)


def test_writer_rewrite_with_feedback_updates_draft_and_iteration():
    fake_llm = FakeLLM(chunks=["Revised draft"])
    writer = WriterAgent(llm=fake_llm, llm_config=_llm_config())
    state = AgentState(category="Science", topic="Test", blog_draft="old", human_feedback="make it better")

    result = writer.rewrite_with_feedback(state)

    assert result.blog_draft == "Revised draft"
    assert result.iteration_count == 1


# ---------- EditorAgent ----------

def test_editor_edit_blog_parses_standard_format():
    body = "word " * 30
    fake_llm = FakeLLM(chunks=[f"---EDITED BLOG POST---\n{body}\n---EDITORIAL NOTES---\nTightened intro."])
    editor = EditorAgent(llm=fake_llm, llm_config=_llm_config())
    state = AgentState(category="Science", topic="Test", blog_draft="draft", research_content="facts")

    result = editor.edit_blog(state)

    assert "word" in result.blog_final
    assert any("Polished" in m for m in result.messages)


def test_editor_edit_blog_falls_back_to_draft_when_llm_fails():
    fake_llm = FakeLLM(chunks=["should not be used"], fail_times=99)
    editor = EditorAgent(llm=fake_llm, llm_config=_llm_config())
    state = AgentState(category="Science", topic="Test", blog_draft="original draft", research_content="facts")

    result = editor.edit_blog(state)

    assert result.blog_final == "original draft"


def test_editor_check_grounding_sets_notes():
    fake_llm = FakeLLM(chunks=["- The claim about 50% growth is not supported by the research."])
    editor = EditorAgent(llm=fake_llm, llm_config=_llm_config())
    state = AgentState(
        category="Science",
        topic="Test",
        research_content="Growth was modest.",
        blog_final="Growth exploded by 50%.",
    )

    result = editor.check_grounding(state)

    assert "50%" in result.grounding_notes
    assert any("Grounding check" in m for m in result.messages)


# ---------- ResearcherAgent ----------

def test_researcher_deduplicates_and_collects_sources():
    results = [
        {"title": "A", "href": "https://a.example", "body": "content a"},
        {"title": "A dup", "href": "https://a.example", "body": "dup content"},
        {"title": "B", "href": "https://b.example", "body": "content b"},
    ]
    fake_search = FakeSearchClient(results=results)
    fake_llm = FakeLLM(chunks=["synthesized research"])
    researcher = ResearcherAgent(llm=fake_llm, llm_config=_llm_config(), search_client=fake_search)
    state = AgentState(category="Science", topic="Test")

    result = researcher.research(state)

    assert result.research_content == "synthesized research"
    assert set(result.research_sources) == {"https://a.example", "https://b.example"}


def test_researcher_retries_failed_search_then_succeeds():
    fake_search = FakeSearchClient(
        results=[{"title": "A", "href": "https://a.example", "body": "content a"}],
        fail_times=1,
    )
    researcher = ResearcherAgent(llm=FakeLLM(), llm_config=_llm_config(), search_client=fake_search)

    result = researcher._search_topic("test query")

    assert result["results"][0]["url"] == "https://a.example"
    assert fake_search.calls == 2  # first call failed, second succeeded


def test_researcher_survives_total_search_failure():
    fake_search = FakeSearchClient(results=[], fail_times=999)
    fake_llm = FakeLLM(chunks=["still wrote something from model knowledge"])
    researcher = ResearcherAgent(llm=fake_llm, llm_config=_llm_config(), search_client=fake_search)
    state = AgentState(category="Science", topic="Test")

    result = researcher.research(state)

    assert result.research_sources == []
    assert any("degraded" in m for m in result.messages)


# ---------- Full-page reading & numbered sources ----------

def _settings(**search_overrides):
    from config import Settings

    s = Settings()
    for key, value in search_overrides.items():
        setattr(s.search, key, value)
    return s


LONG_ARTICLE = "The reactor produced more energy than it consumed, a first for the facility. " * 10


def test_researcher_uses_full_page_text_and_numbers_sources():
    results = [
        {"title": "A", "href": "https://a.example", "body": "snippet a"},
        {"title": "B", "href": "https://b.example", "body": "snippet b"},
    ]
    fetched_urls = []

    def fake_fetcher(url):
        fetched_urls.append(url)
        return LONG_ARTICLE if url == "https://a.example" else ""

    fake_llm = FakeLLM(chunks=["synthesized"])
    researcher = ResearcherAgent(
        llm=fake_llm,
        llm_config=_llm_config(),
        search_client=FakeSearchClient(results=results),
        app_settings=_settings(fetch_pages=5),
        page_fetcher=fake_fetcher,
    )
    state = AgentState(category="Science", topic="Fusion")

    result = researcher.research(state)

    prompt = fake_llm.prompts[-1]
    assert "[1] A" in prompt and "[2] B" in prompt
    assert "produced more energy" in prompt  # full text replaced A's snippet
    assert "snippet b" in prompt  # B's page was empty, so its snippet stays
    assert result.research_sources == ["https://a.example", "https://b.example"]
    assert sorted(fetched_urls) == ["https://a.example", "https://b.example"]
    assert any("full text of 1 of 2" in m for m in result.messages)


def test_researcher_keeps_snippet_when_page_fetch_raises():
    def failing_fetcher(url):
        raise TimeoutError("slow site")

    fake_llm = FakeLLM(chunks=["synthesized"])
    researcher = ResearcherAgent(
        llm=fake_llm,
        llm_config=_llm_config(),
        search_client=FakeSearchClient(results=[{"title": "A", "href": "https://a.example", "body": "snippet a"}]),
        page_fetcher=failing_fetcher,
    )

    result = researcher.research(AgentState(category="Science", topic="Fusion"))

    assert "snippet a" in fake_llm.prompts[-1]
    assert result.research_sources == ["https://a.example"]


def test_researcher_skips_fetching_when_disabled():
    calls = []
    researcher = ResearcherAgent(
        llm=FakeLLM(chunks=["synthesized"]),
        llm_config=_llm_config(),
        search_client=FakeSearchClient(results=[{"title": "A", "href": "https://a.example", "body": "snippet a"}]),
        app_settings=_settings(fetch_pages=0),
        page_fetcher=lambda url: calls.append(url) or LONG_ARTICLE,
    )

    researcher.research(AgentState(category="Science", topic="Fusion"))

    assert calls == []


def test_researcher_interleaves_queries_and_caps_sources():
    researcher = ResearcherAgent(llm=FakeLLM(), llm_config=_llm_config(), search_client=FakeSearchClient())
    per_query = [
        [{"url": "q1-a"}, {"url": "q1-b"}, {"url": "q1-c"}],
        [{"url": "q2-a"}],
        [{"url": "q3-a"}, {"url": "q3-b"}],
    ]

    order = [r["url"] for r in researcher._interleave_results(per_query)]

    assert order == ["q1-a", "q2-a", "q3-a", "q1-b", "q3-b", "q1-c"]


def test_researcher_source_numbers_match_research_sources_when_capped():
    results = [{"title": f"T{i}", "href": f"https://s{i}.example", "body": f"body {i}"} for i in range(12)]
    fake_llm = FakeLLM(chunks=["synthesized"])
    researcher = ResearcherAgent(
        llm=fake_llm,
        llm_config=_llm_config(),
        search_client=FakeSearchClient(results=results),
        app_settings=_settings(fetch_pages=0, max_sources=8),
    )

    result = researcher.research(AgentState(category="Science", topic="Fusion"))

    assert len(result.research_sources) == 8
    assert "[8] T7" in fake_llm.prompts[-1]
    assert "[9]" not in fake_llm.prompts[-1]


# ---------- Citation cleanup in writer/editor ----------

def test_writer_removes_citations_to_missing_sources():
    fake_llm = FakeLLM(chunks=["Fusion works [1]. Also cheap [5]."])
    writer = WriterAgent(llm=fake_llm, llm_config=_llm_config())
    state = AgentState(category="Science", topic="Fusion", research_content="[1] facts",
                       research_sources=["https://a.example"])

    result = writer.write_blog(state)

    assert result.blog_draft == "Fusion works [1]. Also cheap."
    assert any("non-existent source" in m for m in result.messages)
    assert "Cite your sources" in fake_llm.prompts[-1]


def test_editor_keeps_valid_citations_and_strips_model_sources_list():
    body = "Fusion produced net energy [1]. " * 20
    response = f"---EDITED BLOG POST---\n{body}\n\n## References\n1. https://invented.example\n\n---EDITORIAL NOTES---\nTightened."
    editor = EditorAgent(llm=FakeLLM(chunks=[response]), llm_config=_llm_config())
    state = AgentState(category="Science", topic="Fusion", blog_draft="draft",
                       research_sources=["https://a.example"])

    result = editor.edit_blog(state)

    assert "[1]" in result.blog_final
    assert "invented.example" not in result.blog_final


def test_researcher_falls_through_blocked_pages_up_to_fetch_limit():
    results = [{"title": f"T{i}", "href": f"https://s{i}.example", "body": f"snippet {i}"} for i in range(5)]

    def fetcher(url):
        if url in ("https://s0.example", "https://s1.example"):
            raise RuntimeError("403 Forbidden")
        return LONG_ARTICLE

    fake_llm = FakeLLM(chunks=["synthesized"])
    researcher = ResearcherAgent(
        llm=fake_llm,
        llm_config=_llm_config(),
        search_client=FakeSearchClient(results=results),
        app_settings=_settings(fetch_pages=2),
        page_fetcher=fetcher,
    )

    result = researcher.research(AgentState(category="Science", topic="Fusion"))

    prompt = fake_llm.prompts[-1]
    # s0/s1 were blocked, so s2 and s3 get full text; s4 is past the limit and keeps its snippet
    assert "snippet 0" in prompt and "snippet 1" in prompt and "snippet 4" in prompt
    assert "snippet 2" not in prompt and "snippet 3" not in prompt
    assert any("full text of 2 of 5" in m for m in result.messages)

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

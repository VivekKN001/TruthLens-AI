from state import AgentState


def test_is_valid_category_accepts_known_categories():
    for category in ["Science", "Politics", "Gaming"]:
        assert AgentState(category=category).is_valid_category()


def test_is_valid_category_rejects_unknown():
    assert not AgentState(category="Sports").is_valid_category()


def test_can_iterate_respects_max_iterations():
    state = AgentState(iteration_count=2, max_iterations=3)
    assert state.can_iterate()

    state.iteration_count = 3
    assert not state.can_iterate()


def test_add_message_appends_to_log():
    state = AgentState()
    state.add_message("did a thing")

    assert state.messages == ["did a thing"]


def test_grounding_notes_defaults_empty():
    assert AgentState().grounding_notes == ""

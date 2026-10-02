"""
LangGraph Workflow - Orchestrates the multi-agent system

Two workflows:
1. Full workflow: Research -> Write -> Edit (initial run)
2. Revision workflow: Revise based on feedback (subsequent runs)
"""

from typing import Callable, Optional

from langgraph.graph import StateGraph, END

from state import AgentState
from agents import ResearcherAgent, WriterAgent, EditorAgent
from utils.parsers import determine_revision_target
from utils.logger import get_logger

logger = get_logger(__name__)


def create_workflow(
    researcher: Optional[ResearcherAgent] = None,
    writer: Optional[WriterAgent] = None,
    editor: Optional[EditorAgent] = None,
    on_token: Optional[Callable[[str], None]] = None,
):
    """
    Create the FULL workflow for initial research and writing.

    Flow: Research -> Write -> Edit -> Grounding Check

    Args:
        researcher: Optional pre-configured researcher agent (for testing)
        writer: Optional pre-configured writer agent (for testing)
        editor: Optional pre-configured editor agent (for testing)
        on_token: Optional callback invoked with each chunk of every LLM
            response as it streams in (e.g. to drive a live UI)

    Returns:
        Compiled LangGraph workflow
    """
    # Initialize agents (use provided or create new)
    researcher = researcher or ResearcherAgent()
    writer = writer or WriterAgent()
    editor = editor or EditorAgent()

    # Create the workflow graph
    workflow = StateGraph(AgentState)

    # Define nodes
    def research_node(state: AgentState) -> AgentState:
        """Execute research phase"""
        return researcher.research(state, on_token=on_token)

    def write_node(state: AgentState) -> AgentState:
        """Execute writing phase"""
        return writer.write_blog(state, on_token=on_token)

    def edit_node(state: AgentState) -> AgentState:
        """Execute editing phase"""
        return editor.edit_blog(state, on_token=on_token)

    def grounding_node(state: AgentState) -> AgentState:
        """Check the edited blog's claims against the research data, fixing what it flags"""
        return editor.verify_and_fix(state, on_token=on_token)

    # Add nodes to graph
    workflow.add_node("research", research_node)
    workflow.add_node("write", write_node)
    workflow.add_node("edit", edit_node)
    workflow.add_node("grounding_check", grounding_node)

    # Define edges - simple linear flow
    workflow.add_edge("research", "write")
    workflow.add_edge("write", "edit")
    workflow.add_edge("edit", "grounding_check")
    workflow.add_edge("grounding_check", END)

    # Set starting node
    workflow.set_entry_point("research")

    return workflow.compile()


def create_revision_workflow(
    writer: Optional[WriterAgent] = None,
    editor: Optional[EditorAgent] = None,
    on_token: Optional[Callable[[str], None]] = None,
):
    """
    Create the REVISION workflow for handling feedback.

    This workflow does NOT re-research. It only revises based on feedback.

    Flow: Analyze feedback -> Route to Writer or Editor -> Edit if needed -> Grounding Check

    Args:
        writer: Optional pre-configured writer agent (for testing)
        editor: Optional pre-configured editor agent (for testing)
        on_token: Optional callback invoked with each chunk of every LLM
            response as it streams in (e.g. to drive a live UI)

    Returns:
        Compiled LangGraph workflow
    """
    # Initialize agents (use provided or create new)
    writer = writer or WriterAgent()
    editor = editor or EditorAgent()

    # Create the workflow graph
    workflow = StateGraph(AgentState)

    # Define nodes
    def route_feedback_node(state: AgentState) -> AgentState:
        """Analyze feedback and prepare for routing"""
        target = determine_revision_target(state.human_feedback)
        # Store routing decision in messages for debugging
        state.messages.append(f"Feedback routed to: {target}")
        return state

    def writer_revise_node(state: AgentState) -> AgentState:
        """Writer revises based on feedback"""
        return writer.rewrite_with_feedback(state, on_token=on_token)

    def editor_revise_node(state: AgentState) -> AgentState:
        """Editor revises based on feedback"""
        return editor.rewrite_with_feedback(state, on_token=on_token)

    def final_edit_node(state: AgentState) -> AgentState:
        """Final edit pass after writer revision"""
        return editor.edit_blog(state, on_token=on_token)

    def grounding_node(state: AgentState) -> AgentState:
        """Check the revised blog's claims against the research data, fixing what it flags"""
        return editor.verify_and_fix(state, on_token=on_token)

    # Add nodes
    workflow.add_node("route_feedback", route_feedback_node)
    workflow.add_node("writer_revise", writer_revise_node)
    workflow.add_node("editor_revise", editor_revise_node)
    workflow.add_node("final_edit", final_edit_node)
    workflow.add_node("grounding_check", grounding_node)

    # Routing function
    def route_to_agent(state: AgentState) -> str:
        """Determine which agent should handle the revision"""
        return determine_revision_target(state.human_feedback)

    # Set entry point
    workflow.set_entry_point("route_feedback")

    # Add conditional routing
    workflow.add_conditional_edges(
        "route_feedback",
        route_to_agent,
        {
            "writer_revise": "writer_revise",
            "editor_revise": "editor_revise",
        }
    )

    # After writer revises, do a final edit pass, then re-check grounding
    workflow.add_edge("writer_revise", "final_edit")
    workflow.add_edge("final_edit", "grounding_check")

    # Editor revise also re-checks grounding before ending
    workflow.add_edge("editor_revise", "grounding_check")
    workflow.add_edge("grounding_check", END)

    return workflow.compile()


def print_workflow_info():
    """Print workflow structure information"""
    print("""
╔════════════════════════════════════════════════════════════════╗
║           TRUTHLENS AI - Multi-Agent Orchestration             ║
╚════════════════════════════════════════════════════════════════╝

WORKFLOW:

  INITIAL RUN:
    Research -> Write -> Edit -> Grounding Check -> [Show to User]

  ON FEEDBACK:
    - "approve/yes/ok" -> Publish
    - "reresearch"     -> Start over with new research
    - Other feedback   -> Revise (writer or editor) -> Show again

AGENTS:

  RESEARCHER:
    - Conducts thorough web research using DuckDuckGo (free, no API key)
    - Synthesizes findings into structured research
    - Category-specific approach (Science/Politics/Gaming)

  WRITER:
    - Transforms research into engaging blog post
    - Tailored writing style per category
    - 1500-2500 word blog posts

  EDITOR:
    - Reviews for accuracy and clarity
    - Corrects factual errors
    - Polishes language and structure
    - Runs a final grounding check: flags specific claims (numbers, quotes,
      dates) in the finished post that the research doesn't actually support,
      auto-corrects them, and re-checks before handing the post to you

════════════════════════════════════════════════════════════════
""")

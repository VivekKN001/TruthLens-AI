"""
Main Entry Point - Interactive CLI for TruthLens AI
Handles user input, workflow execution, and human-in-the-loop feedback
"""

# Suppress transformers warning about PyTorch (not needed for API calls)
import os
os.environ["TRANSFORMERS_VERBOSITY"] = "error"

import sys
import uuid
from pathlib import Path

from config import settings, Category
from state import AgentState
from graph.workflow import create_workflow, create_revision_workflow, print_workflow_info
from utils import storage
from utils.logger import get_logger

logger = get_logger(__name__)


# Category-specific examples for topic selection
CATEGORY_EXAMPLES = {
    Category.SCIENCE.value: [
        "The impact of quantum computing on cybersecurity",
        "CRISPR gene editing and its medical applications",
        "Latest breakthroughs in nuclear fusion energy",
        "How AI is transforming drug discovery",
    ],
    Category.POLITICS.value: [
        "2024 US Presidential Election analysis",
        "India's new criminal law reforms",
        "Russia-Ukraine conflict latest developments",
        "Climate policy changes in the EU",
    ],
    Category.GAMING.value: [
        "The rise of AI in competitive gaming",
        "GTA 6 release and industry expectations",
        "Cloud gaming vs traditional consoles",
        "Esports growth and mainstream acceptance",
    ],
}


def clear_screen():
    """Clear console screen"""
    os.system("cls" if os.name == "nt" else "clear")


def print_header(title: str):
    """Print formatted header"""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


def get_category() -> str:
    """Get topic category from user"""
    print_header("SELECT RESEARCH CATEGORY")
    print("Available categories:")
    print("  1. Science (AI, Technology, Research)")
    print("  2. Politics (Current Affairs, Government, International Relations)")
    print("  3. Gaming (Games, Industry, Esports)")

    categories = {
        "1": Category.SCIENCE.value,
        "2": Category.POLITICS.value,
        "3": Category.GAMING.value,
    }

    while True:
        choice = input("\nEnter your choice (1-3): ").strip()

        if choice in categories:
            selected = categories[choice]
            logger.info(f"Selected category: {selected}")
            return selected
        else:
            print("Invalid choice. Please enter 1, 2, or 3.")


def get_topic(category: str) -> str:
    """
    Get topic from user with category-specific examples.

    Args:
        category: The selected category to show relevant examples
    """
    print_header("ENTER YOUR TOPIC")
    print(f"Category: {category}")
    print("\nWhat would you like us to research and write about?")

    # Show category-specific examples
    examples = CATEGORY_EXAMPLES.get(category, CATEGORY_EXAMPLES[Category.SCIENCE.value])
    print(f"\nExamples for {category}:")
    for example in examples:
        print(f"  - {example}")

    while True:
        topic = input("\nEnter your topic: ").strip()

        if topic:
            logger.info(f"Topic selected: {topic}")
            return topic

        print("Topic cannot be empty!")


def display_content(content: str, title: str, max_length: int = 2000):
    """Display content with optional truncation"""
    print_header(title)

    if len(content) > max_length:
        print(content[:max_length])
        print(f"\n... [Content continues - {len(content)} total characters] ...\n")
    else:
        print(content)


def display_final_blog(blog_final: str, sources: list[str]):
    """Display final polished blog with sources"""
    display_content(blog_final, "FINAL POLISHED BLOG POST", settings.workflow.content_preview_length)

    print("\n" + "=" * 70)
    print("SOURCES USED:")
    for i, source in enumerate(sources[:5], 1):
        print(f"  {i}. {source}")
    if len(sources) > 5:
        print(f"  ... and {len(sources) - 5} more sources")


def get_human_feedback(blog_content: str) -> tuple[str, bool, bool]:
    """
    Get feedback from human reviewer.

    Args:
        blog_content: Full blog content for 'show_full' option

    Returns:
        Tuple of (feedback string, approved boolean, re_research boolean)
    """
    print_header("HUMAN REVIEW & FEEDBACK")
    print("Review the content above.")
    print("\nOptions:")
    print("  - Type 'approve' or 'yes' or 'ok' to publish as-is")
    print("  - Type 'reresearch' to research the topic again from scratch")
    print("  - Type 'show_full' to see the complete blog post")
    print("  - Describe specific changes needed (e.g., 'make it more detailed')")

    while True:
        feedback = input("\nYour feedback/decision: ").strip()

        if not feedback:
            print("Please provide feedback or approval.")
            continue

        feedback_lower = feedback.lower()

        # Approve variations
        if feedback_lower in ["approve", "yes", "ok", "good", "looks good", "perfect", "done"]:
            logger.info("Content approved for publication")
            return "", True, False

        # Show full content
        if feedback_lower == "show_full":
            print("\n" + "=" * 70)
            print("FULL BLOG POST:")
            print("=" * 70)
            print(blog_content)
            print("=" * 70 + "\n")
            continue

        # Re-research request
        if feedback_lower in ["reresearch", "re-research", "research again", "start over"]:
            logger.info("User requested re-research")
            return "", False, True

        # Specific feedback for revision
        logger.info(f"Feedback recorded: {feedback[:50]}...")
        return feedback, False, False


def save_blog_to_file(state: AgentState) -> None:
    """Save blog post to markdown file"""
    default_filename = f"blog_{state.category}_{state.topic.replace(' ', '_')[:20]}"
    filename = input(f"Enter filename (default: {default_filename}): ").strip()

    if not filename:
        filename = default_filename

    filepath = Path(f"{filename}.md")

    content = f"""# {state.topic}

**Category:** {state.category}

---

{state.blog_final}

---

## Sources

"""
    for i, source in enumerate(state.research_sources, 1):
        content += f"{i}. {source}\n"

    if state.grounding_notes:
        content += f"\n## Grounding Check\n\n{state.grounding_notes}\n"

    try:
        filepath.write_text(content, encoding="utf-8")
        logger.info(f"Blog saved to: {filepath}")
    except Exception as e:
        logger.error(f"Error saving file: {e}")


def _persist(run_id: str, state: AgentState, status: str) -> None:
    """Best-effort snapshot to the run history DB - never blocks the CLI on failure."""
    try:
        storage.save_run(run_id, state, status)
    except Exception as e:
        logger.warning(f"Could not save run history: {e}")


def run_workflow(category: str, topic: str) -> AgentState:
    """
    Execute the complete workflow.

    Args:
        category: Content category
        topic: Research topic

    Returns:
        Final agent state
    """
    print_header("STARTING RESEARCH & WRITING WORKFLOW")
    print(f"Category: {category}")
    print(f"Topic: {topic}")
    print("\nInitializing agents...\n")

    # Create workflows
    full_workflow = create_workflow()
    revision_workflow = create_revision_workflow()

    # Used to snapshot this run's progress to the history DB (utils/storage.py)
    run_id = uuid.uuid4().hex[:12]

    # Initialize state
    state = AgentState(
        category=category,
        topic=topic,
        iteration_count=0,
        max_iterations=settings.workflow.max_iterations,
    )

    # STEP 1: Run initial full workflow (research -> write -> edit -> grounding check)
    logger.info("Running initial workflow (research -> write -> edit)...")
    print("\nPhase 1: Researching topic...")
    print("Phase 2: Writing blog post...")
    print("Phase 3: Editing and polishing...\n")

    result = full_workflow.invoke(state)

    # Update state with results
    if isinstance(result, dict):
        state = AgentState(**result)
    else:
        state = result

    _persist(run_id, state, "awaiting_feedback")

    # Main feedback loop
    iteration = 0
    max_iterations = settings.workflow.max_iterations

    while iteration < max_iterations:
        iteration += 1

        # Display results
        print(f"\n{'#'*70}")
        print(f"REVIEW #{iteration}")
        print(f"{'#'*70}")

        if state.research_content:
            display_content(
                state.research_content,
                "RESEARCH FINDINGS",
                settings.workflow.research_preview_length,
            )

        if state.blog_final:
            display_final_blog(state.blog_final, state.research_sources)
        elif state.blog_draft:
            display_content(
                state.blog_draft,
                "BLOG DRAFT",
                settings.workflow.content_preview_length,
            )

        if state.grounding_notes:
            display_content(state.grounding_notes, "GROUNDING CHECK (claims not clearly backed by research)", 800)

        # Get human feedback
        feedback, approved, re_research = get_human_feedback(state.blog_final or state.blog_draft)

        if approved:
            # User approved - publish
            print_header("PUBLICATION READY")
            logger.info("Content finalized and ready for publication")
            print("Content has been finalized and is ready for publication!")
            _persist(run_id, state, "done")

            save_option = input("\nWould you like to save this blog to a file? (y/n): ").strip().lower()
            if save_option == "y":
                save_blog_to_file(state)

            return state

        if re_research:
            # User wants to re-research from scratch
            print("\nRe-researching topic from scratch...")
            state = AgentState(
                category=category,
                topic=topic,
                iteration_count=0,
                max_iterations=max_iterations,
            )
            result = full_workflow.invoke(state)
            if isinstance(result, dict):
                state = AgentState(**result)
            else:
                state = result
            _persist(run_id, state, "awaiting_feedback")
            continue

        # User provided feedback - run revision workflow
        print(f"\nRevising based on feedback: '{feedback[:50]}...'")
        state.human_feedback = feedback
        state.human_approved = False

        result = revision_workflow.invoke(state)
        if isinstance(result, dict):
            state = AgentState(**result)
        else:
            state = result
        _persist(run_id, state, "awaiting_feedback")

    # Max iterations reached
    logger.warning("Maximum revision iterations reached. Finalizing current version...")
    print_header("MAXIMUM REVISIONS REACHED")
    print("Content has been finalized after maximum revision attempts.")
    _persist(run_id, state, "done")

    save_option = input("\nWould you like to save this blog to a file? (y/n): ").strip().lower()
    if save_option == "y":
        save_blog_to_file(state)

    return state


def validate_environment() -> bool:
    """
    Validate that the local Ollama server is running and has the model pulled.
    No API keys needed - everything here is free/local.

    Returns:
        True if valid, False otherwise
    """
    ok, errors = settings.validate_ollama()
    if not ok:
        for err in errors:
            logger.error(err)
        print("\nCould not reach your local Ollama setup:")
        for err in errors:
            print(f"  - {err}")
        print("\nSetup steps:")
        print("  1. Install Ollama: https://ollama.com/download")
        print("  2. Run: ollama serve   (if it isn't already running)")
        print(f"  3. Run: ollama pull {settings.llm.writer.model}")
        return False

    return True


def main():
    """Main function - entry point for the CLI"""
    # Validate environment
    if not validate_environment():
        sys.exit(1)

    # Main loop
    while True:
        clear_screen()
        print_workflow_info()

        # Get user input
        category = get_category()
        topic = get_topic(category)  # Pass category for relevant examples

        # Run workflow
        run_workflow(category, topic)

        # Ask if user wants to process another topic
        print("\n" + "=" * 70)
        another = input("Would you like to research another topic? (y/n): ").strip().lower()

        if another != "y":
            print("\nThank you for using TruthLens AI!")
            print("Happy publishing!\n")
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nWorkflow interrupted by user.")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)

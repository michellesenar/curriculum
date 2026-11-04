import json
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from curriculum import db
from curriculum.ingest import ingest_export
from curriculum.models import Connection

# Anchored to the project root regardless of where the server process starts.
PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_PATH = PROJECT_ROOT / "curriculum.db"
CONFIG_PATH = PROJECT_ROOT / "config" / "filter_topics.txt"

mcp = MCPServer("curriculum")


@mcp.tool()
def ingest_conversations(export_path: str) -> str:
    """
    Ingest a ChatGPT conversations.json export into the curriculum database.
    Filters for curriculum-relevant conversations, extracts topics, and saves
    them to the knowledge graph. Safe to run multiple times on the same file —
    already-ingested conversations are skipped.

    Args:
        export_path: Absolute path to the conversations.json file.
    """
    path = Path(export_path)
    if not path.exists():
        return f"File not found: {export_path}"
    if not path.suffix == ".json":
        return f"Expected a .json file, got: {export_path}"

    result = ingest_export(
        export_path=path,
        config_path=CONFIG_PATH,
        db_path=DB_PATH,
    )
    return json.dumps(result.model_dump(), indent=2)


@mcp.tool()
def get_curriculum() -> str:
    """
    Return all topics currently in the curriculum knowledge graph.
    Topics include books, articles, concepts, and practices extracted
    from ingested conversations or added manually.
    """
    db.init_db(DB_PATH)
    topics = db.get_all_topics()

    if not topics:
        return "No topics in the curriculum yet. Use ingest_conversations to import a ChatGPT export."

    return json.dumps(
        [t.model_dump() for t in topics],
        indent=2,
    )


@mcp.tool()
def log_topic(
    name: str,
    type: str,
    author: str = "",
    description: str = "",
    tags: list[str] | None = None,
) -> str:
    """
    Manually add a topic to the curriculum knowledge graph.
    Use this for books, articles, concepts, or practices that weren't
    captured during ingest or that you want to add proactively.

    Args:
        name: Title of the book, name of the concept, or short label for the topic.
        type: One of 'book', 'article', 'concept', or 'practice'.
        author: Author name, for books and articles.
        description: A brief description of the topic and why it matters to the curriculum.
        tags: List of thematic tags, e.g. ['anthropology', 'political economy'].
    """
    db.init_db(DB_PATH)

    from curriculum.models import Topic as TopicModel
    topic = db.save_topic(
        TopicModel(
            name=name,
            type=type,
            author=author or None,
            description=description or None,
            tags=tags or [],
        ),
        DB_PATH,
    )
    return json.dumps(topic.model_dump(), indent=2)


@mcp.tool()
def search_curriculum(query: str) -> str:
    """
    Search the curriculum knowledge graph by keyword or theme.
    Searches across topic names, descriptions, and tags.

    Args:
        query: A keyword or phrase to search for, e.g. 'anthropology' or 'colonial'.
    """
    db.init_db(DB_PATH)
    topics = db.search_topics(query, DB_PATH)

    if not topics:
        return f"No topics found matching '{query}'."

    return json.dumps(
        [t.model_dump() for t in topics],
        indent=2,
    )


@mcp.tool()
def link_topics(from_topic: str, to_topic: str, reason: str) -> str:
    """
    Record a directed connection between two topics with a reason explaining
    why one led to the other. Both topics must already exist in the curriculum.

    Args:
        from_topic: Name of the topic that led to the other.
        to_topic: Name of the topic that was reached.
        reason: A concise explanation of why from_topic led to to_topic.
    """
    db.init_db(DB_PATH)

    source = db.find_topic_by_name(from_topic, DB_PATH)
    if source is None:
        return f"Topic not found: '{from_topic}'. Check get_curriculum for exact names."

    target = db.find_topic_by_name(to_topic, DB_PATH)
    if target is None:
        return f"Topic not found: '{to_topic}'. Check get_curriculum for exact names."

    if source.id == target.id:
        return "Cannot link a topic to itself."

    connection = db.save_connection(
        Connection(
            from_topic_id=source.id,
            to_topic_id=target.id,
            reason=reason,
        ),
        DB_PATH,
    )

    return json.dumps({
        "linked": f"{source.name} → {target.name}",
        "reason": connection.reason,
        "connection_id": connection.id,
    }, indent=2)


@mcp.tool()
def get_threads() -> str:
    """
    Return all connected chains of topics in the curriculum knowledge graph.
    Each thread is a path from a root topic (nothing leads to it) through
    connected topics, with the reason for each connection shown.
    """
    db.init_db(DB_PATH)
    threads = db.get_threads(DB_PATH)

    if not threads:
        return "No connections recorded yet. Use link_topics to connect topics."

    return json.dumps(threads, indent=2)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

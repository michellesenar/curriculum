import json
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from curriculum import db
from curriculum.ingest import ingest_export
from curriculum.models import Connection, Recommendation

# Anchored to the project root regardless of where the server process starts.
PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_PATH = PROJECT_ROOT / "curriculum.db"
CONFIG_PATH = PROJECT_ROOT / "config" / "filter_topics.txt"

mcp = MCPServer("curriculum")


@mcp.tool()
def setup_curriculum(keywords: list[str]) -> str:
    """
    Set up the curriculum filter for a new user by writing their interest keywords.
    Call this at the start of a new installation before any ingest.

    Before calling this tool, expand the user's described interests into specific,
    varied keywords — not just 'history' but 'colonialism', 'archaeology', 'empire'.
    Aim for 30-60 keywords covering the full range of what they described.
    The keywords are used to filter ChatGPT conversations during ingest, so specificity
    matters: too broad and junk gets in, too narrow and relevant conversations get skipped.

    Args:
        keywords: List of lowercase keyword strings representing the user's interests.
    """
    if not keywords:
        return "No keywords provided. Describe your interests and I'll generate a keyword list."

    merged = db.write_filter_keywords(keywords, CONFIG_PATH)

    return json.dumps({
        "status": "Filter keywords saved.",
        "keyword_count": len(merged),
        "keywords": merged,
    }, indent=2)


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
    if not CONFIG_PATH.exists():
        return "No filter keywords configured. Run setup_curriculum first to describe your interests."

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


@mcp.tool()
def update_topic(
    name: str,
    new_name: str = "",
    type: str = "",
    author: str = "",
    description: str = "",
    tags: list[str] | None = None,
) -> str:
    """
    Update fields on an existing topic. Only fields you provide are changed —
    leave everything else empty to keep current values.

    Args:
        name: Current exact name of the topic to update.
        new_name: Rename the topic to this.
        type: Correct the type — 'book', 'article', 'concept', or 'practice'.
        author: Set or correct the author name.
        description: Set or update the description.
        tags: Replace the current tags with this list.
    """
    db.init_db(DB_PATH)

    updates = {}
    if new_name:
        updates["name"] = new_name
    if type:
        updates["type"] = type
    if author:
        updates["author"] = author
    if description:
        updates["description"] = description
    if tags is not None:
        updates["tags"] = tags

    if not updates:
        return "No fields provided to update."

    updated = db.update_topic(name, updates, DB_PATH)
    if updated is None:
        return f"Topic not found: '{name}'. Check get_curriculum for exact names."

    return json.dumps(updated.model_dump(), indent=2)


@mcp.tool()
def delete_topic(name: str) -> str:
    """
    Remove a topic from the curriculum along with all its connections and mentions.
    Use this to clean up extraction errors or topics that don't belong in the graph.
    Recommendations linked to this topic are preserved but unlinked.

    Args:
        name: Exact name of the topic to delete.
    """
    db.init_db(DB_PATH)

    deleted = db.delete_topic(name, DB_PATH)
    if not deleted:
        return f"Topic not found: '{name}'. Check get_curriculum for exact names."

    return f"Deleted '{name}' and removed all its connections."


@mcp.tool()
def update_recommendation(
    id: int,
    title: str = "",
    author: str = "",
    reason: str = "",
    topic_name: str = "",
) -> str:
    """
    Update fields on an existing recommendation. Use get_recommendations to
    find the id first. Only provided fields are changed.

    Args:
        id: The numeric id of the recommendation to update.
        title: Corrected title.
        author: Corrected or added author.
        reason: Updated context for why this was recommended.
        topic_name: Name of a curriculum topic to link this recommendation to.
    """
    db.init_db(DB_PATH)

    updates = {}
    if title:
        updates["title"] = title
    if author:
        updates["author"] = author
    if reason:
        updates["reason"] = reason
    if topic_name:
        topic = db.find_topic_by_name(topic_name, DB_PATH)
        if topic is None:
            return f"Topic not found: '{topic_name}'. Check get_curriculum for exact names."
        updates["topic_id"] = topic.id

    if not updates:
        return "No fields provided to update."

    updated = db.update_recommendation(id, updates, DB_PATH)
    if updated is None:
        return f"Recommendation not found with id {id}. Use get_recommendations to find the correct id."

    return json.dumps(updated.model_dump(), indent=2)


@mcp.tool()
def save_recommendation(
    title: str,
    reason: str,
    author: str = "",
    topic_name: str = "",
) -> str:
    """
    Save a book or reading recommendation that came up during a conversation.
    Call this whenever a recommendation worth remembering surfaces — capture
    the full context in 'reason' so it's searchable later.

    Args:
        title: Title of the recommended book or article.
        reason: Full context for why it was recommended and what it connects to.
                Be specific — this is what makes it findable months later.
        author: Author name if known.
        topic_name: Name of the curriculum topic this recommendation relates to, if any.
    """
    db.init_db(DB_PATH)

    topic_id = None
    if topic_name:
        topic = db.find_topic_by_name(topic_name, DB_PATH)
        if topic:
            topic_id = topic.id

    rec = db.save_recommendation(
        Recommendation(
            title=title,
            author=author or None,
            reason=reason,
            topic_id=topic_id,
        ),
        DB_PATH,
    )
    return json.dumps(rec.model_dump(), indent=2)


@mcp.tool()
def get_recommendations(query: str = "") -> str:
    """
    Retrieve saved recommendations, optionally filtered by a keyword search.
    Searches across title, author, and the full reason/context text.
    Leave query empty to return all recommendations, most recent first.

    Args:
        query: Keyword or phrase to search for, e.g. 'hypomanic' or 'Vodou' or 'Scott'.
    """
    db.init_db(DB_PATH)
    recs = db.search_recommendations(query or None, DB_PATH)

    if not recs:
        msg = "No recommendations found."
        if query:
            msg = f"No recommendations found matching '{query}'."
        return msg

    return json.dumps(
        [r.model_dump() for r in recs],
        indent=2,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

import json
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from curriculum import db, privacy
from curriculum.ingest import ingest_export
from curriculum.models import Connection, Recommendation
from curriculum.paths import CONFIG_PATH, DB_PATH

VALID_TYPES = {"book", "article", "media", "concept", "practice"}

mcp = MCPServer("curriculum")


# --- shared helpers ------------------------------------------------------

NOT_SET_UP = (
    "NOT_SET_UP: This curriculum is empty, so the user hasn't been through setup. "
    "Tell them setup comes first (a short interview, a few minutes), then call "
    "setup_curriculum and follow its instructions."
)


def _not_set_up() -> bool:
    """True when the curriculum has no topics yet."""
    db.init_db(DB_PATH)
    return db.count_topics(DB_PATH) == 0


def _review(field: str, text: str, reviewed: bool) -> str | None:
    """
    Plain check before saving free text. Returns a refusal message if the text
    refers to the person instead of the subject, or None if it can be saved.
    """
    if reviewed or not text:
        return None
    found = privacy.personal_references(text)
    if not found:
        return None
    return (
        f"NOT SAVED. The {field} refers to the person ({', '.join(found)}). "
        f"Rewrite it to describe the subject matter only, with nothing about the "
        f"person's health, family, work, or circumstances, then call again. "
        f"If it is fine as written (for example a quoted title), call again with "
        f"reviewed=True."
    )


# --- setup ---------------------------------------------------------------

SETUP_QUESTIONS = [
    {
        "id": "anchors",
        "ask": (
            "Tell me about five books, shows, films, or other things that really "
            "made you think. For each one, say what stuck with you."
        ),
    },
    {
        "id": "topics",
        "ask": "List five to ten topics you'd like to follow. Specific is better.",
        "examples": [
            "opportunity cost (economics)",
            "confirmation bias (psychology)",
            "the causes of World War I (history)",
            "the Sicilian Defense (chess)",
        ],
    },
]


@mcp.tool()
def setup_curriculum() -> str:
    """
    Start or continue first-time setup for a new user. Call this when another
    tool returns NOT_SET_UP, or when the user wants to set up or add interests.

    Returns two interview questions and instructions. Ask the questions in
    order, one at a time, in your own friendly words. Do not guess answers or
    skip ahead.
    """
    already = not _not_set_up()
    return json.dumps(
        {
            "status": "already_set_up" if already else "not_set_up",
            "questions": SETUP_QUESTIONS,
            "instructions": [
                *(
                    [
                        "This user has already been through setup. Ask whether "
                        "they want to add more interests. If not, stop here."
                    ]
                    if already
                    else []
                ),
                "Ask the 'anchors' question first and wait for the answer. Fewer "
                "than five is fine. Do not press for more.",
                "Then ask the 'topics' question, showing the examples so the user "
                "sees the level of detail that helps. Do not present it as a quiz.",
                "Call review_interests with the topics list. Ask every follow-up "
                "question it returns and use the answers in place of the broad word.",
                "Save what you learned. For each work from the first question, call "
                "log_topic (book, article, or media) with a short description of "
                "the subject. Then log the principle that stuck as a 'concept' "
                "named as a transferable idea, with the subject areas as tags, and "
                "connect the work to it with link_topics.",
                "For each interest from the second question, call log_topic with "
                "type 'concept'. If it is really a principle (for example "
                "'opportunity cost'), use that as the name. Link it to anything "
                "already logged that shares the idea. Only link when the shared idea "
                "is real. A specific subject (like a chess opening) can stand alone "
                "until a genuine connection shows up.",
                "Before logging a concept, call search_curriculum and reuse an "
                "existing one if it is the same idea.",
                "Descriptions describe the subject, never the person: nothing "
                "about their health, family, work, or circumstances.",
                "When done, tell the user in a couple of plain sentences what "
                "you saved, and that you'll add to it as they talk.",
            ],
        },
        indent=2,
    )


@mcp.tool()
def review_interests(interests: list[str]) -> str:
    """
    Check the user's list of interests from setup. Any interest given as a
    single word (like 'history' or 'golf') is too broad, and this returns a
    follow-up question for it. Ask each follow-up, then use the answer as the
    interest instead.

    Args:
        interests: The topics the user listed, each as they said it.
    """
    follow_ups = [
        {
            "interest": item.strip(),
            "question": f"Which part of {item.strip()} interests you most?",
        }
        for item in interests
        if len(item.split()) == 1
    ]
    if not follow_ups:
        return json.dumps({"follow_ups": [], "note": "All of these are specific enough."})
    return json.dumps(
        {
            "follow_ups": follow_ups,
            "note": "Ask each question, then use the answer in place of the broad word.",
        },
        indent=2,
    )


@mcp.tool()
def set_ingest_keywords(keywords: list[str]) -> str:
    """
    Owner-only: write filter keywords for ChatGPT history import. Not part of
    first-time setup for new users (use setup_curriculum for that). Call this
    before ingest_conversations.

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
    Owner-only: ingest a ChatGPT conversations.json export into the curriculum
    database. Filters for curriculum-relevant conversations, extracts topics, and
    saves them to the knowledge graph. Safe to run multiple times on the same
    file — already-ingested conversations are skipped.

    Args:
        export_path: Absolute path to the conversations.json file.
    """
    if not CONFIG_PATH.exists():
        return "No filter keywords configured. Run set_ingest_keywords first to describe your interests."

    keywords = [
        line.strip()
        for line in CONFIG_PATH.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not keywords:
        return "Filter keyword file exists but contains no keywords. Run set_ingest_keywords to add your interests."

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


# --- the graph -----------------------------------------------------------

@mcp.tool()
def get_curriculum() -> str:
    """
    Return all topics currently in the curriculum knowledge graph: concepts
    (transferable principles), books, articles, media, and practices.
    """
    if _not_set_up():
        return NOT_SET_UP

    return json.dumps(
        [t.model_dump() for t in db.get_all_topics(DB_PATH)],
        indent=2,
    )


@mcp.tool()
def log_topic(
    name: str,
    type: str,
    author: str = "",
    description: str = "",
    tags: list[str] | None = None,
    reviewed: bool = False,
) -> str:
    """
    Add a topic to the curriculum knowledge graph. Use this whenever a book,
    idea, or practice comes up that is worth keeping.

    The graph connects ideas across fields, so its nodes are transferable
    principles, not subjects. Choose the type with that in mind:
    - 'concept': a principle that applies in more than one field. Name the
      principle itself ('performance under variance', 'liminality', 'opportunity
      cost'), not the subject area ('golf', 'anthropology'). Subject areas go in
      tags.
    - 'book', 'article', 'media' (shows, films, podcasts), 'practice': a
      specific work or activity. When you log one, also log the principle it
      develops as a 'concept' and connect them with link_topics.

    Before adding a concept, call search_curriculum in case the idea is already
    there under a similar name, and reuse it if so.

    The description is one or two sentences about the subject matter and why it
    belongs in the graph. Describe the subject, not the person: nothing about
    their health, family, work, or circumstances, and no 'I', 'my', or 'the user'.
    If you are unsure whether something is too personal, check with the user first.

    Args:
        name: The principle, or the title of the work or practice.
        type: One of 'book', 'article', 'media', 'concept', or 'practice'.
        author: Author or creator, for works.
        description: What the topic is about, in terms of the subject only.
        tags: Subject areas it touches, e.g. ['golf', 'poker', 'investing'].
        reviewed: Set True only if you were told the description was too personal
                  and have confirmed the wording is fine as written.
    """
    if type not in VALID_TYPES:
        return f"Invalid type '{type}'. Must be one of: {', '.join(sorted(VALID_TYPES))}."

    refusal = _review("description", description, reviewed)
    if refusal:
        return refusal

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
    Searches across topic names, descriptions, and tags. Use it to check
    whether an idea is already in the graph before adding it.

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
def link_topics(
    from_topic: str,
    to_topic: str,
    reason: str,
    reviewed: bool = False,
) -> str:
    """
    Record a directed connection between two topics. Both must already exist.

    The best connections cross fields: a principle linking a golf idea to a
    poker idea, or a book to the principle it develops. The reason should name
    what the two share, in terms of the subject only, not the person.

    Args:
        from_topic: Name of the topic that led to the other.
        to_topic: Name of the topic that was reached.
        reason: One sentence on the idea that connects them, e.g. 'both turn on
                judging a decision by its process, not its outcome'.
        reviewed: Set True only if you were told the reason was too personal and
                  have confirmed the wording is fine as written.
    """
    if _not_set_up():
        return NOT_SET_UP

    refusal = _review("reason", reason, reviewed)
    if refusal:
        return refusal

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
    if _not_set_up():
        return NOT_SET_UP

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
    reviewed: bool = False,
) -> str:
    """
    Update fields on an existing topic. Only fields you provide are changed —
    leave everything else empty to keep current values. The same rules as
    log_topic apply: concepts are named as principles, and descriptions
    describe the subject, not the person.

    Args:
        name: Current exact name of the topic to update.
        new_name: Rename the topic to this.
        type: Correct the type — 'book', 'article', 'media', 'concept', or 'practice'.
        author: Set or correct the author name.
        description: Set or update the description.
        tags: Replace the current tags with this list.
        reviewed: Set True only if you were told the description was too personal
                  and have confirmed the wording is fine as written.
    """
    if type and type not in VALID_TYPES:
        return f"Invalid type '{type}'. Must be one of: {', '.join(sorted(VALID_TYPES))}."

    refusal = _review("description", description, reviewed)
    if refusal:
        return refusal

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


# --- recommendations -----------------------------------------------------

@mcp.tool()
def update_recommendation(
    id: int,
    title: str = "",
    author: str = "",
    reason: str = "",
    topic_name: str = "",
    reviewed: bool = False,
) -> str:
    """
    Update fields on an existing recommendation. Use get_recommendations to
    find the id first. Only provided fields are changed. The reason follows the
    same rules as in save_recommendation.

    Args:
        id: The numeric id of the recommendation to update.
        title: Corrected title.
        author: Corrected or added author.
        reason: Updated reason, about the subject matter only.
        topic_name: Name of a curriculum topic to link this recommendation to.
        reviewed: Set True only if you were told the reason was too personal and
                  have confirmed the wording is fine as written.
    """
    refusal = _review("reason", reason, reviewed)
    if refusal:
        return refusal

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
    reviewed: bool = False,
) -> str:
    """
    Save a book or reading recommendation that came up during a conversation.
    Call this whenever a recommendation worth remembering surfaces.

    The reason says what the work connects to in the subject matter: the idea it
    develops, the topic it extends, the other field it speaks to. Be specific, so
    it is findable later. Do not explain why the person needs it, and do not
    include anything about their health, family, work, or circumstances. If you
    are unsure whether something is too personal, leave it out.

    Args:
        title: Title of the recommended book or article.
        reason: What it connects to in the subject matter, e.g. 'develops the
                idea of liminality through rites of passage'.
        author: Author name if known.
        topic_name: Name of the curriculum topic (ideally the concept) this
                    recommendation relates to, if any.
        reviewed: Set True only if you were told the reason was too personal and
                  have confirmed the wording is fine as written.
    """
    if _not_set_up():
        return NOT_SET_UP

    refusal = _review("reason", reason, reviewed)
    if refusal:
        return refusal

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
    Searches across title, author, and the reason text.
    Leave query empty to return all recommendations, most recent first.

    Args:
        query: Keyword or phrase to search for, e.g. 'liminality' or 'Turner'.
    """
    if _not_set_up():
        return NOT_SET_UP

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

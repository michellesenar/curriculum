import json
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from curriculum import db
from curriculum.models import Conversation, Topic, TopicMention


class IngestResult(BaseModel):
    total_conversations: int
    passed_filter: int
    skipped_already_ingested: int
    topics_extracted: int


def load_filter_keywords(config_path: Path) -> set[str]:
    """Load relevance keywords from config file, ignoring comments and blank lines."""
    keywords = set()
    with open(config_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                keywords.add(line.lower())
    return keywords


def _get_message_texts(conv: dict) -> list[tuple[str, str]]:
    """
    Return messages as (role, text) tuples in conversation order.

    The ChatGPT export stores messages as a tree of nodes, not a flat list.
    We walk from current_node back to the root following parent links,
    then reverse so the result reads chronologically.
    """
    mapping = conv.get("mapping", {})
    current_node_id = conv.get("current_node")

    path = []
    node_id = current_node_id
    while node_id and node_id in mapping:
        node = mapping[node_id]
        msg = node.get("message")
        if msg:
            role = msg.get("author", {}).get("role", "")
            parts = msg.get("content", {}).get("parts", [])
            text = " ".join(str(p) for p in parts if isinstance(p, str)).strip()
            if text and role in ("user", "assistant"):
                path.append((role, text))
        node_id = node.get("parent")

    path.reverse()
    return path


def _passes_filter(conv: dict, keywords: set[str]) -> bool:
    """
    Check title and first user message against filter keywords.
    Errs toward inclusion — a single keyword match is enough.
    """
    title = conv.get("title", "").lower()
    if any(kw in title for kw in keywords):
        return True

    messages = _get_message_texts(conv)
    first_user = next((text for role, text in messages if role == "user"), "")
    return any(kw in first_user[:500].lower() for kw in keywords)


def _primary_name_from_title(title: str) -> str:
    """
    Reduce a conversation title to its main subject.

    "Women Who Run with the Wolves - Estés and the Wild Woman"
        → "Women Who Run with the Wolves"

    "Graeber's Debt and the Myth of Barter"
        → "Debt and the Myth of Barter"
    """
    # Drop the subtitle (everything after " - ")
    if " - " in title:
        title = title.split(" - ")[0]

    # Strip common filler prefixes
    title = re.sub(
        r"^(second thoughts on|thoughts on|notes on|reading|revisiting|starting)\s+",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()

    # Strip leading possessives: "Graeber's Debt" → "Debt"
    title = re.sub(r"^\w+'s\s+", "", title).strip()

    return title


def _infer_type(name: str) -> str:
    """
    Guess topic type from name. Defaults to 'concept'.
    Topics with explicit authors get assigned 'book' in the extraction step.
    """
    name_lower = name.lower()
    practice_signals = {
        "sql", "query", "database", "code", "software",
        "performance", "engineering", "index", "schema",
    }
    if any(s in name_lower for s in practice_signals):
        return "practice"
    return "concept"


def _extract_books_from_text(text: str, conversation_id: str) -> list[Topic]:
    """
    Find 'Title by Author' patterns in conversation text.
    Non-greedy match on title so we grab the shortest plausible title
    before the first 'by'.
    """
    topics = []
    seen: set[str] = set()

    pattern = re.compile(
        r"([A-Z][A-Za-z0-9 :,'!?—-]{3,60}?)\s+by\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)"
    )

    for match in pattern.finditer(text):
        title = match.group(1).strip().rstrip(".,;:")
        author = match.group(2).strip()

        if len(title) < 4:
            continue

        key = title.lower()
        if key not in seen:
            seen.add(key)
            topics.append(Topic(
                name=title,
                type="book",
                author=author,
                source_conversation_id=conversation_id,
            ))

    return topics


def extract_topics(conv: dict) -> list[Topic]:
    """
    Extract topics from a single conversation.
    Primary title topic comes first, then any additional books from text.
    """
    conversation_id = conv.get("conversation_id", "")
    title = conv.get("title", "")

    messages = _get_message_texts(conv)
    full_text = " ".join(text for _, text in messages)

    topics: list[Topic] = []
    seen: set[str] = set()

    def add(t: Topic) -> None:
        key = t.name.lower()
        if key not in seen:
            seen.add(key)
            topics.append(t)

    # Primary topic from the conversation title
    primary = _primary_name_from_title(title)
    if primary:
        add(Topic(
            name=primary,
            type=_infer_type(primary),
            source_conversation_id=conversation_id,
        ))

    # Additional books found via "X by Y" patterns in the full text
    for book in _extract_books_from_text(full_text, conversation_id):
        add(book)

    return topics


def ingest_export(
    export_path: Path,
    config_path: Path,
    db_path: Path,
) -> IngestResult:
    """
    Parse a ChatGPT conversations.json export, filter for curriculum-relevant
    conversations, extract topics, and save to the database.
    Safe to run multiple times — already-ingested conversations are skipped.
    """
    keywords = load_filter_keywords(config_path)

    with open(export_path) as f:
        conversations = json.load(f)

    db.init_db(db_path)

    total = len(conversations)
    passed = 0
    skipped = 0
    topics_extracted = 0

    for raw in conversations:
        conv_id = raw.get("conversation_id", "")

        if db.conversation_exists(conv_id, db_path):
            skipped += 1
            continue

        passes = _passes_filter(raw, keywords)
        create_time = raw.get("create_time", 0)
        date = datetime.fromtimestamp(create_time, tz=timezone.utc).isoformat()

        db.save_conversation(
            Conversation(
                id=conv_id,
                title=raw.get("title", "Untitled"),
                date=date,
                passed_filter=passes,
            ),
            db_path,
        )

        if not passes:
            continue

        passed += 1
        topics = extract_topics(raw)
        topics_extracted += len(topics)

        for topic in topics:
            saved = db.save_topic(topic, db_path)
            db.save_topic_mention(
                TopicMention(topic_id=saved.id, conversation_id=conv_id),
                db_path,
            )

    return IngestResult(
        total_conversations=total,
        passed_filter=passed,
        skipped_already_ingested=skipped,
        topics_extracted=topics_extracted,
    )

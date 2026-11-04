import sqlite3
from pathlib import Path

from curriculum.models import Conversation, Topic, TopicMention

# Walk up from src/curriculum/db.py → src/curriculum → src → project root
DB_PATH = Path(__file__).parent.parent.parent / "curriculum.db"


def _get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # access columns by name, not index
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    """Create all tables if they don't exist. Safe to call repeatedly."""
    with _get_connection(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                date TEXT NOT NULL,
                passed_filter INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL,
                author TEXT,
                description TEXT,
                tags TEXT,
                source_conversation_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (source_conversation_id) REFERENCES conversations(id)
            );

            CREATE TABLE IF NOT EXISTS topic_mentions (
                topic_id INTEGER NOT NULL,
                conversation_id TEXT NOT NULL,
                PRIMARY KEY (topic_id, conversation_id),
                FOREIGN KEY (topic_id) REFERENCES topics(id),
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            );

            CREATE TABLE IF NOT EXISTS connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_topic_id INTEGER NOT NULL,
                to_topic_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                source_conversation_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (from_topic_id) REFERENCES topics(id),
                FOREIGN KEY (to_topic_id) REFERENCES topics(id),
                FOREIGN KEY (source_conversation_id) REFERENCES conversations(id)
            );
        """)


# --- row → model ---------------------------------------------------------

def _row_to_topic(row: sqlite3.Row) -> Topic:
    return Topic(
        id=row["id"],
        name=row["name"],
        type=row["type"],
        author=row["author"],
        description=row["description"],
        tags=row["tags"].split(",") if row["tags"] else [],
        source_conversation_id=row["source_conversation_id"],
        created_at=row["created_at"],
    )


# --- writes --------------------------------------------------------------

def save_conversation(conv: Conversation, db_path: Path = DB_PATH) -> None:
    """Insert a conversation. Silently does nothing if the id already exists."""
    with _get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO conversations (id, title, date, passed_filter)
            VALUES (?, ?, ?, ?)
            """,
            (conv.id, conv.title, conv.date, int(conv.passed_filter)),
        )


def save_topic(topic: Topic, db_path: Path = DB_PATH) -> Topic:
    """
    Insert a topic. If a topic with the same name already exists, return
    the existing one. Either way, returns the topic with its database id set.
    """
    with _get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO topics
                (name, type, author, description, tags, source_conversation_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topic.name,
                topic.type,
                topic.author,
                topic.description,
                ",".join(topic.tags) if topic.tags else None,
                topic.source_conversation_id,
                topic.created_at,
            ),
        )
        row = conn.execute(
            "SELECT * FROM topics WHERE name = ?", (topic.name,)
        ).fetchone()

    return _row_to_topic(row)


def save_topic_mention(mention: TopicMention, db_path: Path = DB_PATH) -> None:
    """Record that a topic appeared in a conversation. Safe to call twice."""
    with _get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO topic_mentions (topic_id, conversation_id)
            VALUES (?, ?)
            """,
            (mention.topic_id, mention.conversation_id),
        )


# --- reads ---------------------------------------------------------------

def get_all_topics(db_path: Path = DB_PATH) -> list[Topic]:
    """Return every topic in the database, ordered by name."""
    with _get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM topics ORDER BY name"
        ).fetchall()
    return [_row_to_topic(row) for row in rows]


def conversation_exists(conversation_id: str, db_path: Path = DB_PATH) -> bool:
    """Check whether a conversation has already been ingested."""
    with _get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
    return row is not None

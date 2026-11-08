import re
import sqlite3
from pathlib import Path

from curriculum.models import Connection, Conversation, Recommendation, Topic, TopicMention

from curriculum.paths import DB_PATH


def _get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # access columns by name, not index
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    """Create all tables if they don't exist. Safe to call repeatedly."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
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

            CREATE TABLE IF NOT EXISTS recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                author TEXT,
                reason TEXT NOT NULL,
                topic_id INTEGER,
                source_conversation_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (topic_id) REFERENCES topics(id),
                FOREIGN KEY (source_conversation_id) REFERENCES conversations(id)
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
    the existing one (names that differ only in case, punctuation, or a
    leading "the/a/an" count as the same). Either way, returns the topic with
    its database id set.
    """
    existing = find_topic_by_name(topic.name, db_path)
    if existing is not None:
        return existing

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


def count_topics(db_path: Path = DB_PATH) -> int:
    """Return how many topics exist. Zero means the curriculum isn't set up yet."""
    with _get_connection(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0]


def conversation_exists(conversation_id: str, db_path: Path = DB_PATH) -> bool:
    """Check whether a conversation has already been ingested."""
    with _get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
    return row is not None


def search_topics(query: str, db_path: Path = DB_PATH) -> list[Topic]:
    """Search topics by name, description, or tags. Case-insensitive substring match."""
    with _get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM topics
            WHERE
                LOWER(name)        LIKE LOWER(?)
                OR LOWER(COALESCE(description, '')) LIKE LOWER(?)
                OR LOWER(COALESCE(tags, ''))        LIKE LOWER(?)
            ORDER BY name
            """,
            (f"%{query}%", f"%{query}%", f"%{query}%"),
        ).fetchall()
    return [_row_to_topic(row) for row in rows]


_LEADING_ARTICLE = re.compile(r"^(the|a|an)\s+")


def normalize_name(name: str) -> str:
    """
    Reduce a topic name to a plain form for comparing: lowercase, apostrophes
    removed, other punctuation treated as spaces, leading "the/a/an" dropped.
    "The Malazan Book of the Fallen" and "malazan book of the fallen!" match.
    """
    text = name.lower().replace("'", "").replace("\u2019", "")
    text = re.sub(r"[^\w\s]", " ", text)
    text = " ".join(text.split())
    return _LEADING_ARTICLE.sub("", text)


def find_topic_by_name(name: str, db_path: Path = DB_PATH) -> Topic | None:
    """
    Look up a topic by name. Tries an exact (case-insensitive) match first,
    then a match on the normalized name. Returns None if not found.
    """
    with _get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM topics WHERE LOWER(name) = LOWER(?)", (name,)
        ).fetchone()
        if row is None:
            wanted = normalize_name(name)
            if wanted:
                for candidate in conn.execute("SELECT * FROM topics ORDER BY id"):
                    if normalize_name(candidate["name"]) == wanted:
                        row = candidate
                        break
    return _row_to_topic(row) if row else None


def save_connection(connection: Connection, db_path: Path = DB_PATH) -> Connection:
    """Insert a directed connection between two topics."""
    with _get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO connections
                (from_topic_id, to_topic_id, reason, source_conversation_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                connection.from_topic_id,
                connection.to_topic_id,
                connection.reason,
                connection.source_conversation_id,
                connection.created_at,
            ),
        )
        return connection.model_copy(update={"id": cursor.lastrowid})


def get_threads(db_path: Path = DB_PATH) -> list[list[dict]]:
    """
    Traverse the connection graph and return all paths as threads.

    Starts from root topics — those with outgoing connections but no incoming
    ones. Follows edges depth-first until reaching a topic with no further
    connections. Each thread is a list of steps where every step after the
    first includes the reason it was reached from the previous topic.

    Ideas link back to each other, so the graph can contain loops. A thread
    that returns to a topic it already passed ends there, with that last step
    marked "loops_back": True. Topics in a loop that no root leads into are
    started from separately, so they still appear.
    """
    with _get_connection(db_path) as conn:
        rows = conn.execute("""
            SELECT
                c.reason,
                ft.id  AS from_id,  ft.name AS from_name,  ft.type AS from_type,
                tt.id  AS to_id,    tt.name AS to_name,    tt.type AS to_type
            FROM connections c
            JOIN topics ft ON c.from_topic_id = ft.id
            JOIN topics tt ON c.to_topic_id   = tt.id
        """).fetchall()

    if not rows:
        return []

    # Build adjacency list and collect metadata in one pass
    graph: dict[int, list[dict]] = {}
    topic_info: dict[int, dict] = {}
    has_incoming: set[int] = set()

    for row in rows:
        fid, fname, ftype = row["from_id"], row["from_name"], row["from_type"]
        tid, tname, ttype = row["to_id"],   row["to_name"],   row["to_type"]

        graph.setdefault(fid, []).append({"to_id": tid, "reason": row["reason"]})
        topic_info[fid] = {"name": fname, "type": ftype}
        topic_info[tid] = {"name": tname, "type": ttype}
        has_incoming.add(tid)

    roots = [nid for nid in graph if nid not in has_incoming]

    threads: list[list[dict]] = []
    covered: set[int] = set()

    def dfs(node_id: int, path: list[dict], visited: set[int]) -> None:
        visited = visited | {node_id}
        covered.add(node_id)
        edges = graph.get(node_id, [])
        if not edges:
            # Leaf — record the completed path
            if len(path) > 1:
                threads.append(path)
            return
        for edge in edges:
            to_id = edge["to_id"]
            step = {
                "topic": topic_info[to_id]["name"],
                "type":  topic_info[to_id]["type"],
                "reason": edge["reason"],
            }
            if to_id in visited:
                # The thread circles back to a topic already on this path
                threads.append(path + [{**step, "loops_back": True}])
                continue
            dfs(to_id, path + [step], visited)

    def start_thread(node_id: int) -> None:
        dfs(node_id, [{
            "topic": topic_info[node_id]["name"],
            "type":  topic_info[node_id]["type"],
            "reason": None,
        }], set())

    for root_id in roots:
        start_thread(root_id)

    # Anything still unvisited sits in a loop with no root leading into it
    for node_id in sorted(graph):
        if node_id not in covered:
            start_thread(node_id)

    return threads


def update_topic(name: str, updates: dict, db_path: Path = DB_PATH) -> Topic | None:
    """
    Update fields on an existing topic. Only keys present in updates are changed.
    Pass tags as a list[str] — serialization is handled here.
    Returns the updated topic, or None if the topic wasn't found.
    """
    topic = find_topic_by_name(name, db_path)
    if topic is None:
        return None

    allowed = {"name", "type", "author", "description", "tags"}
    fields = {k: v for k, v in updates.items() if k in allowed}

    if not fields:
        return topic

    if "tags" in fields:
        fields["tags"] = ",".join(fields["tags"]) if fields["tags"] else None

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [topic.id]

    with _get_connection(db_path) as conn:
        conn.execute(f"UPDATE topics SET {set_clause} WHERE id = ?", values)
        new_name = fields.get("name", name)
        row = conn.execute(
            "SELECT * FROM topics WHERE name = ?", (new_name,)
        ).fetchone()

    return _row_to_topic(row) if row else None


def delete_topic(name: str, db_path: Path = DB_PATH) -> bool:
    """
    Remove a topic and all its connections, mentions, and recommendation links.
    Returns True if deleted, False if not found.
    """
    topic = find_topic_by_name(name, db_path)
    if topic is None:
        return False

    with _get_connection(db_path) as conn:
        conn.execute(
            "DELETE FROM connections WHERE from_topic_id = ? OR to_topic_id = ?",
            (topic.id, topic.id),
        )
        conn.execute(
            "DELETE FROM topic_mentions WHERE topic_id = ?", (topic.id,)
        )
        conn.execute(
            "UPDATE recommendations SET topic_id = NULL WHERE topic_id = ?",
            (topic.id,),
        )
        conn.execute("DELETE FROM topics WHERE id = ?", (topic.id,))

    return True


def save_recommendation(rec: Recommendation, db_path: Path = DB_PATH) -> Recommendation:
    """Insert a recommendation and return it with its database id set."""
    with _get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO recommendations
                (title, author, reason, topic_id, source_conversation_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                rec.title,
                rec.author,
                rec.reason,
                rec.topic_id,
                rec.source_conversation_id,
                rec.created_at,
            ),
        )
    return rec.model_copy(update={"id": cursor.lastrowid})


def update_recommendation(rec_id: int, updates: dict, db_path: Path = DB_PATH) -> Recommendation | None:
    """
    Update fields on an existing recommendation. Only keys present in updates are changed.
    Returns the updated recommendation, or None if not found.
    """
    with _get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM recommendations WHERE id = ?", (rec_id,)
        ).fetchone()

        if row is None:
            return None

        allowed = {"title", "author", "reason", "topic_id"}
        fields = {k: v for k, v in updates.items() if k in allowed}

        if not fields:
            return Recommendation(
                id=row["id"],
                title=row["title"],
                author=row["author"],
                reason=row["reason"],
                topic_id=row["topic_id"],
                source_conversation_id=row["source_conversation_id"],
                created_at=row["created_at"],
            )

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [rec_id]
        conn.execute(
            f"UPDATE recommendations SET {set_clause} WHERE id = ?", values
        )
        row = conn.execute(
            "SELECT * FROM recommendations WHERE id = ?", (rec_id,)
        ).fetchone()

    return Recommendation(
        id=row["id"],
        title=row["title"],
        author=row["author"],
        reason=row["reason"],
        topic_id=row["topic_id"],
        source_conversation_id=row["source_conversation_id"],
        created_at=row["created_at"],
    )


def write_filter_keywords(keywords: list[str], config_path: Path) -> list[str]:
    """
    Merge keywords into the existing filter file, preserving current keywords.
    Returns the full sorted list of keywords after merging.
    """
    existing: set[str] = set()
    if config_path.exists():
        for line in config_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                existing.add(line.lower())

    merged = existing | {kw.lower().strip() for kw in keywords if kw.strip()}
    sorted_kws = sorted(merged)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# One keyword per line. Conversations matching any of these (in title or first message)",
        "# will be processed during ingest. Everything else is skipped.",
        "",
    ] + sorted_kws
    config_path.write_text("\n".join(lines) + "\n")

    return sorted_kws


def search_recommendations(query: str | None = None, db_path: Path = DB_PATH) -> list[Recommendation]:
    """
    Return recommendations matching a keyword search across title, author, and reason.
    If query is empty or None, return all recommendations ordered by most recent first.
    """
    with _get_connection(db_path) as conn:
        if not query:
            rows = conn.execute(
                "SELECT * FROM recommendations ORDER BY created_at DESC"
            ).fetchall()
        else:
            pattern = f"%{query}%"
            rows = conn.execute(
                """
                SELECT * FROM recommendations
                WHERE
                    LOWER(title)                       LIKE LOWER(?)
                    OR LOWER(COALESCE(author, ''))     LIKE LOWER(?)
                    OR LOWER(reason)                   LIKE LOWER(?)
                ORDER BY created_at DESC
                """,
                (pattern, pattern, pattern),
            ).fetchall()

    return [
        Recommendation(
            id=row["id"],
            title=row["title"],
            author=row["author"],
            reason=row["reason"],
            topic_id=row["topic_id"],
            source_conversation_id=row["source_conversation_id"],
            created_at=row["created_at"],
        )
        for row in rows
    ]

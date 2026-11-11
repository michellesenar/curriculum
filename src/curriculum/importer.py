"""
Merge one hosted curriculum database into another.

Used to move a tester's existing graph (for example one built during a trial
run) into their real hosted account. The source is a hosted-schema SQLite file.
Nothing is overwritten and nothing is printed except counts, so the owner never
has to read the tester's content.

Safe to run again: topics are matched by name the same way the live tools match
them (case, punctuation and a leading "the/a/an are ignored), a connection is
skipped if the same pair of topics is already connected, and a recommendation is
skipped if the same title and author is already saved.
"""

import sqlite3
from pathlib import Path

from curriculum import db
from curriculum.models import Connection, Recommendation, Topic

REQUIRED_TABLES = {"topics", "connections", "recommendations"}


class ImportFailed(Exception):
    """The source file can't be imported (wrong shape, missing, same as target)."""


def _read_source(source: Path) -> tuple[list, list, list]:
    if not source.exists():
        raise ImportFailed(f"Source file not found: {source}")
    try:
        conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not REQUIRED_TABLES <= tables:
            raise ImportFailed(
                f"That file doesn't look like a curriculum database (missing: "
                f"{', '.join(sorted(REQUIRED_TABLES - tables))})."
            )
        topics = conn.execute("SELECT * FROM topics ORDER BY id").fetchall()
        connections = conn.execute("SELECT * FROM connections ORDER BY id").fetchall()
        recs = conn.execute("SELECT * FROM recommendations ORDER BY id").fetchall()
        conn.close()
        return topics, connections, recs
    except sqlite3.DatabaseError as e:
        raise ImportFailed(f"Couldn't read that file as a database: {e}") from e


def _connected(target: Path, from_id: int, to_id: int) -> bool:
    conn = sqlite3.connect(target)
    try:
        return conn.execute(
            "SELECT 1 FROM connections WHERE from_topic_id = ? AND to_topic_id = ?",
            (from_id, to_id),
        ).fetchone() is not None
    finally:
        conn.close()


def _rec_exists(target: Path, title: str, author: str | None) -> bool:
    conn = sqlite3.connect(target)
    try:
        return conn.execute(
            "SELECT 1 FROM recommendations WHERE LOWER(title) = LOWER(?) "
            "AND LOWER(COALESCE(author, '')) = LOWER(?)",
            (title, author or ""),
        ).fetchone() is not None
    finally:
        conn.close()


def import_graph(source: Path, target: Path, dry_run: bool = False) -> dict:
    """
    Merge `source` into `target`. Returns counts only:
    {"topics_new", "topics_reused", "connections_new", "connections_skipped",
     "recommendations_new", "recommendations_skipped"}.
    With dry_run=True nothing is written.
    """
    if source.resolve() == target.resolve():
        raise ImportFailed("The source and the target are the same file.")

    topics, connections, recs = _read_source(source)
    db.init_hosted_db(target)

    counts = dict(
        topics_new=0, topics_reused=0,
        connections_new=0, connections_skipped=0,
        recommendations_new=0, recommendations_skipped=0,
    )

    # Validate every topic row up front so a bad row stops us before anything is written.
    parsed: dict[int, Topic] = {}
    for r in topics:
        try:
            parsed[r["id"]] = Topic(
                name=r["name"], type=r["type"], author=r["author"],
                description=r["description"],
                tags=r["tags"].split(",") if r["tags"] else [],
                created_at=r["created_at"],
            )
        except Exception as e:  # pydantic validation error
            raise ImportFailed(f"A topic in the source couldn't be read ({type(e).__name__}); nothing was imported.") from e

    id_map: dict[int, int | None] = {}  # source topic id -> target topic id (None in a dry run for new ones)
    for src_id, topic in parsed.items():
        existing = db.find_topic_by_name(topic.name, target)
        if existing is not None:
            id_map[src_id] = existing.id
            counts["topics_reused"] += 1
        else:
            counts["topics_new"] += 1
            id_map[src_id] = None if dry_run else db.save_topic(topic, target).id

    for r in connections:
        a, b = id_map.get(r["from_topic_id"]), id_map.get(r["to_topic_id"])
        if r["from_topic_id"] not in id_map or r["to_topic_id"] not in id_map:
            counts["connections_skipped"] += 1
        elif a is not None and b is not None and _connected(target, a, b):
            counts["connections_skipped"] += 1
        else:
            counts["connections_new"] += 1
            if not dry_run:
                db.save_connection(
                    Connection(from_topic_id=a, to_topic_id=b, reason=r["reason"], created_at=r["created_at"]),
                    target,
                )

    for r in recs:
        if _rec_exists(target, r["title"], r["author"]):
            counts["recommendations_skipped"] += 1
        else:
            counts["recommendations_new"] += 1
            if not dry_run:
                db.save_recommendation(
                    Recommendation(
                        title=r["title"], author=r["author"], reason=r["reason"],
                        topic_id=id_map.get(r["topic_id"]) if r["topic_id"] is not None else None,
                        created_at=r["created_at"],
                    ),
                    target,
                )
    return counts

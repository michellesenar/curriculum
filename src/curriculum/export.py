"""
Export everything stored for one tester as a single CSV.

One file, one row per topic, connection and recommendation, with a `kind`
column to tell them apart (filter on it in Excel to get each table back).
Connections and recommendations name their topics instead of using ids, so the
file reads on its own. Built straight from the tester's tables; no model is
involved, so it is exactly what is stored.
"""

import csv
import io
import sqlite3
from pathlib import Path

COLUMNS = [
    "kind", "name", "type", "author", "description", "tags",
    "from_topic", "to_topic", "title", "reason", "topic", "created_at",
]

# A spreadsheet treats a cell starting with one of these as a formula.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def _cell(value) -> str:
    """Text for one cell. Defuses leading formula characters with an apostrophe."""
    if value is None:
        return ""
    text = str(value)
    return "'" + text if text.startswith(_FORMULA_STARTS) else text


def _tags(raw) -> str:
    return ", ".join(t for t in (raw or "").split(",") if t)


def export_csv(db_path: Path) -> bytes:
    """The whole database as CSV bytes: UTF-8 with a BOM, so Excel reads accents right."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        topics = conn.execute(
            "SELECT name, type, author, description, tags, created_at "
            "FROM topics ORDER BY name COLLATE NOCASE"
        ).fetchall()
        connections = conn.execute(
            "SELECT ft.name AS from_topic, tt.name AS to_topic, c.reason, c.created_at "
            "FROM connections c "
            "JOIN topics ft ON ft.id = c.from_topic_id "
            "JOIN topics tt ON tt.id = c.to_topic_id ORDER BY c.id"
        ).fetchall()
        recs = conn.execute(
            "SELECT r.title, r.author, r.reason, t.name AS topic, r.created_at "
            "FROM recommendations r LEFT JOIN topics t ON t.id = r.topic_id ORDER BY r.id"
        ).fetchall()
    finally:
        conn.close()

    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=COLUMNS)
    writer.writeheader()
    for r in topics:
        writer.writerow({
            "kind": "topic", "name": _cell(r["name"]), "type": r["type"],
            "author": _cell(r["author"]), "description": _cell(r["description"]),
            "tags": _cell(_tags(r["tags"])), "created_at": r["created_at"],
        })
    for r in connections:
        writer.writerow({
            "kind": "connection", "from_topic": _cell(r["from_topic"]),
            "to_topic": _cell(r["to_topic"]), "reason": _cell(r["reason"]),
            "created_at": r["created_at"],
        })
    for r in recs:
        writer.writerow({
            "kind": "recommendation", "title": _cell(r["title"]),
            "author": _cell(r["author"]), "reason": _cell(r["reason"]),
            "topic": _cell(r["topic"]), "created_at": r["created_at"],
        })
    return ("﻿" + out.getvalue()).encode("utf-8")

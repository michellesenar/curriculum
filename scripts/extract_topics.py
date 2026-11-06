#!/usr/bin/env python3
"""
Harness: model-driven topic extraction from filtered conversations.

Replaces the regex-based extraction in ingest.py with a model call per
conversation. Only processes conversations already ingested with passed_filter=1.
Requires ANTHROPIC_API_KEY in the environment.

Usage: python scripts/extract_topics.py path/to/conversations.json
"""

import json
import sqlite3
import sys
from pathlib import Path

import anthropic

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from curriculum import db
from curriculum.ingest import _get_message_texts
from curriculum.models import Topic, TopicMention

DB_PATH = PROJECT_ROOT / "curriculum.db"

EXTRACT_TOOL = {
    "name": "record_topics",
    "description": "Record the intellectual topics found in this conversation.",
    "input_schema": {
        "type": "object",
        "properties": {
            "topics": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "type": {
                            "type": "string",
                            "enum": ["book", "article", "concept", "practice"],
                        },
                        "author": {"type": "string"},
                        "description": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["name", "type"],
                },
            }
        },
        "required": ["topics"],
    },
}

SYSTEM = """\
You extract intellectual topics from conversation transcripts.

Focus on:
- Books and articles (with author if mentioned)
- Academic or intellectual concepts explored in depth
- Practices or disciplines discussed

Skip:
- Technical debugging, error messages, code snippets
- Casual or logistical chit-chat
- Formatting or writing help with no intellectual content

Only extract things that belong in a personal intellectual curriculum.\
"""


def get_passed_conversation_ids(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id FROM conversations WHERE passed_filter = 1"
        ).fetchall()
    return {row[0] for row in rows}


def extract_topics(conv: dict, client: anthropic.Anthropic) -> list[Topic]:
    conv_id = conv.get("conversation_id", "")
    messages = _get_message_texts(conv)
    full_text = "\n\n".join(f"{role.upper()}: {text}" for role, text in messages)

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        system=SYSTEM,
        tools=[EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "record_topics"},
        messages=[{
            "role": "user",
            "content": f"Extract intellectual topics from this conversation:\n\n{full_text[:15000]}",
        }],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "record_topics":
            topics = []
            for t in block.input.get("topics", []):
                topics.append(Topic(
                    name=t["name"],
                    type=t["type"],
                    author=t.get("author") or None,
                    description=t.get("description") or None,
                    tags=t.get("tags", []),
                    source_conversation_id=conv_id,
                ))
            return topics

    return []


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/extract_topics.py path/to/conversations.json")
        sys.exit(1)

    export_path = Path(sys.argv[1])
    if not export_path.exists():
        print(f"File not found: {export_path}")
        sys.exit(1)

    client = anthropic.Anthropic()
    db.init_db(DB_PATH)

    with open(export_path) as f:
        conversations = json.load(f)

    passed_ids = get_passed_conversation_ids(DB_PATH)
    if not passed_ids:
        print("No ingested conversations found. Run ingest_conversations first.")
        sys.exit(1)

    to_process = [c for c in conversations if c.get("conversation_id") in passed_ids]
    print(f"Processing {len(to_process)} conversations...")

    total_topics = 0
    for i, raw in enumerate(to_process, 1):
        conv_id = raw.get("conversation_id", "")
        title = raw.get("title", "Untitled")

        topics = extract_topics(raw, client)
        total_topics += len(topics)

        for topic in topics:
            saved = db.save_topic(topic, DB_PATH)
            db.save_topic_mention(
                TopicMention(topic_id=saved.id, conversation_id=conv_id),
                DB_PATH,
            )

        print(f"[{i}/{len(to_process)}] {title[:60]} → {len(topics)} topics")

    print(f"\nDone. {total_topics} topics extracted from {len(to_process)} conversations.")


if __name__ == "__main__":
    main()

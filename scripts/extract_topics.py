#!/usr/bin/env python3
"""
Harness: model-driven topic extraction from filtered conversations.

Replaces the regex-based extraction in ingest.py with a model call per
conversation. Only processes conversations already ingested with passed_filter=1.
Requires ANTHROPIC_API_KEY in the environment.

Usage: python scripts/extract_topics.py path/to/conversations.json
       python scripts/extract_topics.py path/to/conversations.json --conversation-id ID [--save]
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import anthropic

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from curriculum import db
from curriculum.paths import DB_PATH
from curriculum.ingest import _get_message_texts
from curriculum.models import Topic, TopicMention


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


MAX_CHARS = 15000


def extract_topics(
    conv: dict, client: anthropic.Anthropic
) -> tuple[list[Topic], dict]:
    """Return (topics, diagnostics). Diagnostics explain an empty or odd result."""
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
            "content": f"Extract intellectual topics from this conversation:\n\n{full_text[:MAX_CHARS]}",
        }],
    )

    diag = {
        "chars_total": len(full_text),
        "chars_sent": min(len(full_text), MAX_CHARS),
        "stop_reason": response.stop_reason,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "tool_use_found": False,
        "raw_tool_input": None,
    }

    valid_types = {"book", "article", "concept", "practice"}

    for block in response.content:
        if block.type == "tool_use" and block.name == "record_topics":
            diag["tool_use_found"] = True
            diag["raw_tool_input"] = block.input
            topics = []
            for t in block.input.get("topics", []):
                raw_type = t.get("type", "concept")
                topic_type = raw_type if raw_type in valid_types else "concept"
                topics.append(Topic(
                    name=t["name"],
                    type=topic_type,
                    author=t.get("author") or None,
                    description=t.get("description") or None,
                    tags=t.get("tags", []),
                    source_conversation_id=conv_id,
                ))
            return topics, diag

    return [], diag


def format_diag(diag: dict) -> str:
    cut = diag["chars_total"] - diag["chars_sent"]
    trunc = f", truncated {cut} chars" if cut else ""
    flag = "" if diag["tool_use_found"] else ", NO tool_use block"
    return (
        f"sent {diag['chars_sent']}/{diag['chars_total']} chars{trunc}; "
        f"tokens in={diag['input_tokens']} out={diag['output_tokens']}; "
        f"stop={diag['stop_reason']}{flag}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("export_path", type=Path, help="path to conversations.json")
    parser.add_argument(
        "--conversation-id",
        help="run on this one conversation only. Dry run: prints the topics "
        "and diagnostics, and does not touch the database (add --save to store them)",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="with --conversation-id, also save the topics to the database",
    )
    args = parser.parse_args()

    if args.save and not args.conversation_id:
        parser.error("--save only applies with --conversation-id")
    if not args.export_path.exists():
        print(f"File not found: {args.export_path}")
        sys.exit(1)

    with open(args.export_path) as f:
        conversations = json.load(f)

    single = args.conversation_id is not None
    write_db = not single or args.save

    if single:
        to_process = [
            c for c in conversations if c.get("conversation_id") == args.conversation_id
        ]
        if not to_process:
            print(f"No conversation with id {args.conversation_id} in {args.export_path}")
            sys.exit(1)
    else:
        db.init_db(DB_PATH)
        passed_ids = get_passed_conversation_ids(DB_PATH)
        if not passed_ids:
            print("No ingested conversations found. Run ingest_conversations first.")
            sys.exit(1)
        to_process = [c for c in conversations if c.get("conversation_id") in passed_ids]

    if write_db:
        db.init_db(DB_PATH)
    elif single:
        print("Dry run: nothing will be written to the database.")

    client = anthropic.Anthropic()
    print(f"Processing {len(to_process)} conversations...")

    total_topics = 0
    for i, raw in enumerate(to_process, 1):
        conv_id = raw.get("conversation_id", "")
        title = raw.get("title", "Untitled")

        topics, diag = extract_topics(raw, client)
        total_topics += len(topics)

        if write_db:
            for topic in topics:
                saved = db.save_topic(topic, DB_PATH)
                db.save_topic_mention(
                    TopicMention(topic_id=saved.id, conversation_id=conv_id),
                    DB_PATH,
                )

        print(f"[{i}/{len(to_process)}] {title[:60]} → {len(topics)} topics")
        print(f"    {format_diag(diag)}")
        if single:
            for t in topics:
                by = f" ({t.author})" if t.author else ""
                print(f"    - [{t.type}] {t.name}{by}")
        if not topics:
            # Empty result: show exactly what the model returned.
            print(f"    EMPTY. raw tool input: {json.dumps(diag['raw_tool_input'])}")

    print(f"\nDone. {total_topics} topics extracted from {len(to_process)} conversations.")


if __name__ == "__main__":
    main()

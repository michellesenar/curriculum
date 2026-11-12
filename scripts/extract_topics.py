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
from datetime import datetime, timezone
from pathlib import Path

import anthropic

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from curriculum import db
from curriculum.paths import DB_PATH
from curriculum.ingest import _get_message_texts
from curriculum.models import Conversation, Topic, TopicMention


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

Only extract things that belong in a personal intellectual curriculum.

Naming rules:
- Use the work's real title (and the author, if known) for books and articles.
  Never use a sentence from the conversation as a name, and never use the
  conversation's own title as a topic.
- Name concepts after the idea itself, in a short noun phrase.
- Do not record personal circumstances, health, family situations or the
  user's feelings as topics.
- This is a long transcript: cover the whole conversation, including ideas
  that appear late.\
"""


def get_passed_conversation_ids(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id FROM conversations WHERE passed_filter = 1"
        ).fetchall()
    return {row[0] for row in rows}


MAX_CHARS = 15000  # default cap; --match mode sends the whole conversation
MAX_OUTPUT_TOKENS = 4096
MAX_FULL_CHARS = 450_000  # ~110k tokens; refuse rather than silently truncate


def extract_topics(
    conv: dict, client: anthropic.Anthropic, max_chars: int | None = MAX_CHARS
) -> tuple[list[Topic], dict]:
    """Return (topics, diagnostics). Diagnostics explain an empty or odd result."""
    conv_id = conv.get("conversation_id", "")
    messages = _get_message_texts(conv)
    full_text = "\n\n".join(f"{role.upper()}: {text}" for role, text in messages)

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=MAX_OUTPUT_TOKENS,
        system=SYSTEM,
        tools=[EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "record_topics"},
        messages=[{
            "role": "user",
            "content": f"Extract intellectual topics from this conversation:\n\n{(full_text if max_chars is None else full_text[:max_chars])}",
        }],
    )

    diag = {
        "chars_total": len(full_text),
        "chars_sent": len(full_text) if max_chars is None else min(len(full_text), max_chars),
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


def matches_keyword(conv: dict, keyword: str) -> bool:
    """True if keyword is in the title or the first 500 words (case-insensitive)."""
    kw = keyword.lower()
    if kw in (conv.get("title") or "").lower():
        return True
    text = " ".join(t for _, t in _get_message_texts(conv))
    return kw in " ".join(text.split()[:500]).lower()


def ensure_conversation_row(conv: dict, db_path: Path) -> None:
    """topic_mentions has a foreign key to conversations, so make sure the row exists."""
    date = datetime.fromtimestamp(conv.get("create_time") or 0, tz=timezone.utc).isoformat()
    db.save_conversation(
        Conversation(
            id=conv.get("conversation_id", ""),
            title=conv.get("title") or "Untitled",
            date=date,
            passed_filter=True,
        ),
        db_path,
    )


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
    parser.add_argument(
        "--match",
        metavar="WORD",
        help="run on every conversation with WORD in its title or first 500 words, "
        "on the full text (no truncation), whether or not it passed the ingest "
        "filter or already has topics. Saves to the database unless --dry-run",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="with --match, print what would be extracted without saving",
    )
    args = parser.parse_args()

    if args.save and not args.conversation_id:
        parser.error("--save only applies with --conversation-id")
    if args.dry_run and not args.match:
        parser.error("--dry-run only applies with --match")
    if args.match and args.conversation_id:
        parser.error("use --match or --conversation-id, not both")
    if not args.export_path.exists():
        print(f"File not found: {args.export_path}")
        sys.exit(1)

    with open(args.export_path) as f:
        conversations = json.load(f)

    single = args.conversation_id is not None
    matching = args.match is not None
    full_text_mode = single or matching
    write_db = (not single and not args.dry_run) or args.save
    max_chars = None if matching else MAX_CHARS

    if single:
        to_process = [
            c for c in conversations if c.get("conversation_id") == args.conversation_id
        ]
        if not to_process:
            print(f"No conversation with id {args.conversation_id} in {args.export_path}")
            sys.exit(1)
    elif matching:
        to_process = [c for c in conversations if matches_keyword(c, args.match)]
        if not to_process:
            print(f"No conversation matches {args.match!r} in {args.export_path}")
            sys.exit(1)
        too_big = [
            c.get("title") for c in to_process
            if sum(len(t) for _, t in _get_message_texts(c)) > MAX_FULL_CHARS
        ]
        if too_big:
            print(f"Too long to send whole (over {MAX_FULL_CHARS} chars), refusing to truncate: {too_big}")
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
    else:
        print("Dry run: nothing will be written to the database.")

    client = anthropic.Anthropic()
    print(f"Processing {len(to_process)} conversations...")

    total_topics = 0
    for i, raw in enumerate(to_process, 1):
        conv_id = raw.get("conversation_id", "")
        title = raw.get("title", "Untitled")

        topics, diag = extract_topics(raw, client, max_chars)
        total_topics += len(topics)

        marks = []
        if write_db:
            if matching or single:
                ensure_conversation_row(raw, DB_PATH)
            for topic in topics:
                marks.append("existing" if db.find_topic_by_name(topic.name, DB_PATH) else "NEW")
                saved = db.save_topic(topic, DB_PATH)
                db.save_topic_mention(
                    TopicMention(topic_id=saved.id, conversation_id=conv_id),
                    DB_PATH,
                )

        print(f"[{i}/{len(to_process)}] {title[:60]} → {len(topics)} topics")
        print(f"    {format_diag(diag)}")
        if full_text_mode:
            for n, t in enumerate(topics):
                by = f" ({t.author})" if t.author else ""
                tag = f" [{marks[n]}]" if marks else ""
                print(f"    - [{t.type}] {t.name}{by}{tag}")
        if diag["stop_reason"] == "max_tokens":
            print("    WARNING: output hit the token limit; the topic list may be cut off.")
        if not topics:
            # Empty result: show exactly what the model returned.
            print(f"    EMPTY. raw tool input: {json.dumps(diag['raw_tool_input'])}")

    print(f"\nDone. {total_topics} topics extracted from {len(to_process)} conversations.")


if __name__ == "__main__":
    main()

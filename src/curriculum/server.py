import json
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from curriculum import db
from curriculum.ingest import ingest_export

# Anchored to the project root regardless of where the server process starts.
PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_PATH = PROJECT_ROOT / "curriculum.db"
CONFIG_PATH = PROJECT_ROOT / "config" / "filter_topics.txt"

mcp = MCPServer("curriculum")


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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

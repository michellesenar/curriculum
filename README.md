# curriculum

A knowledge graph of ideas, not subjects, built from your conversations with Claude. One concept, like liminality, can tie together anthropology, sleep science, film, and fantasy.

curriculum is an [MCP](https://modelcontextprotocol.io) server. Once it's connected to Claude, you answer a short interview about what you've read and what you want to follow. After that, Claude adds ideas to your graph and links them as you talk, and can recommend what to read next.

> **Status:** early alpha. The core server works locally. A hosted version for non-developers is in progress, and this README will change as it lands.

## The idea

Most of what people learn gets filed under subjects: golf, World War I, nutrition. The same idea shows up in several of them, and nothing notices.

curriculum keeps **principles** as the nodes, and subjects are tags on them. "Performance under variance" is a node. "Golf" is a tag. That's what lets the graph point out that your golf reading, your poker reading, and your questions about investing are circling the same idea.

Two consequences:

- **Books are one kind of node, not the center.** A book, an article, a concept, and a practice are all topics, and any of them can have connections. The link between two readings is almost always an idea, so the idea gets to be a node with its own edges instead of disappearing into a tag.
- **You can walk the graph.** Ask *what connects to syncretism?* and get actual nodes back, not a list of books that share a tag. You can find a concept sitting at the intersection of three reading threads, or trace a path from one idea to another through the ideas between them.

A books-first model gives you a reading list with arrows. This gives you a graph where books are one of the things in it.

## How it works

1. **Setup.** Claude interviews you: a few things that really made you think, and what stuck with you about each, then five to ten specific topics you'd like to follow. Those answers become your first nodes.
2. **Live use.** As you talk, Claude logs topics, links them, and saves recommendations, using the tools below.
3. **Ask.** You can search the graph, walk the threads between ideas, and get recommendations.

The server stores your graph and does the lookups. It makes no model calls itself. Claude does the reasoning.

## Tools

| Tool | What it does |
| --- | --- |
| `setup_curriculum` | First-time setup |
| `log_topic` | Add a topic (book, article, concept, practice) |
| `update_topic`, `delete_topic` | Edit or remove a topic |
| `link_topics` | Connect two topics with a labeled, directed edge and a reason |
| `get_curriculum`, `search_curriculum` | See or search what's in the graph |
| `get_threads` | Follow connections between topics |
| `save_recommendation`, `update_recommendation`, `get_recommendations` | Keep track of what to read next |
| `ingest_conversations` | Optional: load a ChatGPT export (see below) |

## Data model

```
topics        — the nodes (books, articles, concepts, practices)
connections   — directed, labeled edges between topics
recommendations — things to read next, each tied to a topic
```

The local version also keeps `conversations` and `topic_mentions` for ingest. A hosted version would store only topics, connections, and recommendations. It would hold no chat text and no chat identifiers.

## Running it locally

Requires Python 3.13 or newer.

```
python -m venv venv
source venv/bin/activate
pip install -e .
```

Then add the server to your MCP client. For Claude Desktop, add an entry like this to its config file, using the full path to the `curriculum` command in your venv:

```json
{
  "mcpServers": {
    "curriculum": {
      "command": "/path/to/curriculum/venv/bin/curriculum"
    }
  }
}
```

Restart Claude and ask it to set up your curriculum.

Your graph is stored in a local SQLite file, `curriculum.db`, in the project folder.

## Importing a ChatGPT history (optional)

If you have a ChatGPT data export, `ingest_conversations` can read it and add what it finds to your local graph. This is a separate, local-only tool. It's how the project started, and it's still used by the author, but it isn't part of the main experience. Message text is not stored. The database keeps only titles, dates, and whether a conversation passed the filter.

`scripts/extract_topics.py` is an experimental model-driven extractor for the same purpose. It needs an Anthropic API key in a `.env` file and isn't exposed as a tool.

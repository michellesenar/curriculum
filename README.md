# curriculum

An MCP server that builds a knowledge graph from ChatGPT conversation exports.

## Why topics, not books

The central entity is a **topic**, not a book. Books are one type of topic.

Not everything worth tracking is a book. Concepts like "political dynasties in kin-based societies" or "SQL query performance at scale" are intellectual nodes that belong in the graph as first-class citizens. In a books-first model they'd be orphaned records with nullable fields, or demoted to tags — which means they can't have their own connections or participate in the graph as peers.

The other reason: the connection between two readings is never really *book pointing at book*. It's always mediated by an idea. That idea is worth keeping. With topics as nodes, the concept in the middle of a connection can itself become a node with its own edges, its own cluster of related material.

This means you can ask: *what connects to syncretism?* and get actual traversable nodes back — not a filtered list of books that share a tag. You can discover that a concept sits at the intersection of three different reading threads. You can walk a path from one idea to another through intermediate concepts, because all of them are nodes and the edges between them are real.

A books-first model gives you a reading list with arrows. A topics-first model gives you a knowledge graph where books are one kind of thing that can live in it.

## Data model

```
topics        — the nodes (books, articles, concepts, practices)
connections   — directed, labeled edges between topics
conversations — source conversation exports
topic_mentions — which topics appeared in which conversations
```

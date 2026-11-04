from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

# Restricts the type field to exactly these four strings.
# Anything else will raise a validation error.
TopicType = Literal["book", "article", "concept", "practice"]


class Topic(BaseModel):
    id: int | None = None
    name: str
    type: TopicType
    author: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    source_conversation_id: str | None = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class Connection(BaseModel):
    id: int | None = None
    from_topic_id: int
    to_topic_id: int
    reason: str
    source_conversation_id: str | None = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class Conversation(BaseModel):
    id: str  # the UUID from the ChatGPT JSON
    title: str
    date: str
    passed_filter: bool


class TopicMention(BaseModel):
    topic_id: int
    conversation_id: str

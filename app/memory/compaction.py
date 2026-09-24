"""Context Compaction and Token Bloat Management.

Implements token-based thresholding and sliding window event retention via ADK's
EventsCompactionConfig to prevent context bloat during extended multi-turn
teacher dialogue without custom chat history management.
"""

from __future__ import annotations

from google.adk.apps.app import EventsCompactionConfig

from app.config import config


def get_events_compaction_config() -> EventsCompactionConfig:
    """Return configured ADK EventsCompactionConfig for long-turn conversations.

    Configured with token_threshold (32k tokens) and event_retention_size (5)
    to retain the most recent turns while summarizing older interactions.
    """
    return EventsCompactionConfig(
        token_threshold=config.compaction_token_threshold,
        event_retention_size=config.compaction_event_retention_size,
        compaction_interval=3,
        overlap_size=1,
    )

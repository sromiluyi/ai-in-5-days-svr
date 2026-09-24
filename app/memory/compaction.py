"""Context Compaction and Token Bloat Management.

Implements token-based thresholding and sliding window event retention via ADK's
EventsCompactionConfig to prevent context bloat during extended multi-turn
teacher dialogue.
"""

from __future__ import annotations

from typing import Any, List
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


def compact_conversation_history(events: List[Any], max_events: int = 10) -> List[Any]:
    """Utility function to apply a sliding window retention on event sequences.

    Args:
        events: List of raw conversation events or messages.
        max_events: Maximum number of recent events to retain.

    Returns:
        Sliding window slice of the most recent events.
    """
    if len(events) <= max_events:
        return events
    return events[-max_events:]

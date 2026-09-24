"""ADK Session Service Management.

Connects the ADK agent to session service implementations:
1. VertexAiSessionService in Google Cloud Agent Runtime.
2. DatabaseSessionService for local SQL persistence if configured.
3. InMemorySessionService for testing and lightweight local execution.

NOTE: For external student scorecards and gradebook records, see app.db.gradebook.
"""

from __future__ import annotations

import os
from google.adk.sessions import InMemorySessionService
from app.config import config

# Backward-compatible re-exports from app.db.gradebook
from app.db.gradebook import (
    GradebookDatabase as PersistentScoreCardStore,
    gradebook_db as scorecard_db,
)


def get_session_service(force_in_memory: bool = False):
    """Instantiate the appropriate ADK SessionService based on runtime environment.

    Returns:
        VertexAiSessionService in Google Cloud Agent Runtime.
        DatabaseSessionService in local persistent mode.
        InMemorySessionService in test mode.
    """
    if force_in_memory:
        return InMemorySessionService()

    # If running on Vertex AI Agent Runtime with managed telemetry/sessions
    if os.getenv("GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY") == "true" or os.getenv(
        "USE_VERTEX_SESSIONS", "false"
    ).lower() == "true":
        try:
            from google.adk.sessions import VertexAiSessionService

            return VertexAiSessionService()
        except Exception:
            pass

    # Persistent SQLite / Cloud SQL backing for ADK session events (if configured)
    try:
        from google.adk.sessions import DatabaseSessionService

        return DatabaseSessionService(db_url=config.gradebook_db_url)
    except Exception:
        return InMemorySessionService()


__all__ = [
    "get_session_service",
    "PersistentScoreCardStore",
    "scorecard_db",
]

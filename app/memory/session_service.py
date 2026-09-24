"""Persistent Session State Management.

Connects the agent to persistent storage across turns and server restarts:
1. Google Cloud Vertex AI Agent Runtime managed sessions (VertexAiSessionService) in cloud deployment.
2. SQLAlchemy/SQLite DatabaseSessionService for persistent local development.
3. PersistentScoreCardStore: Dedicated SQLite database storing student scorecards,
   audit logs, and teacher interaction histories.
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

from google.adk.sessions import InMemorySessionService
from app.config import config
from app.models import AuditLogEntry, StudentScoreCard
from app.observability.logger import log_intent, log_outcome


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

    # Persistent SQLite / Cloud SQL backing
    try:
        from google.adk.sessions import DatabaseSessionService

        return DatabaseSessionService(db_url=config.db_url)
    except Exception:
        return InMemorySessionService()


class PersistentScoreCardStore:
    """Persistent SQLite database manager for exam scorecards and audit trails."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # Extract local filename from db_url if sqlite
            if config.db_url.startswith("sqlite:///"):
                self.db_path = config.db_url.replace("sqlite:///", "")
            else:
                self.db_path = "hunger_games_agent_sessions.db"
        else:
            self.db_path = db_path

        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create tables for persistent scorecards and audit records."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS scorecards (
                    scorecard_id TEXT PRIMARY KEY,
                    student_token TEXT NOT NULL,
                    overall_percentage REAL NOT NULL,
                    letter_grade TEXT NOT NULL,
                    is_flagged_hitl INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scorecard_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target_section TEXT NOT NULL,
                    previous_score REAL,
                    new_score REAL,
                    actor TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY(scorecard_id) REFERENCES scorecards(scorecard_id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS teacher_preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def save_scorecard(self, scorecard: StudentScoreCard) -> None:
        """Persist or update a student scorecard."""
        log_intent(
            "PersistentScoreCardStore",
            "SAVE_SCORECARD",
            scorecard.scorecard_id,
            {"student_token": scorecard.student_token, "score": scorecard.overall_percentage},
        )
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO scorecards (
                    scorecard_id, student_token, overall_percentage, letter_grade,
                    is_flagged_hitl, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scorecard_id) DO UPDATE SET
                    overall_percentage=excluded.overall_percentage,
                    letter_grade=excluded.letter_grade,
                    is_flagged_hitl=excluded.is_flagged_hitl,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    scorecard.scorecard_id,
                    scorecard.student_token,
                    scorecard.overall_percentage,
                    scorecard.letter_grade,
                    1 if scorecard.hitL_review.is_flagged else 0,
                    scorecard.model_dump_json(),
                    scorecard.created_at.isoformat(),
                    scorecard.updated_at.isoformat(),
                ),
            )
            conn.commit()

        log_outcome(
            "PersistentScoreCardStore",
            "SAVE_SCORECARD",
            "SUCCESS",
            f"Saved scorecard {scorecard.scorecard_id} for token {scorecard.student_token}",
        )

    def get_scorecard(self, scorecard_id: str) -> Optional[StudentScoreCard]:
        """Retrieve a persisted scorecard by ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT payload_json FROM scorecards WHERE scorecard_id = ?", (scorecard_id,))
            row = cursor.fetchone()
            if row:
                return StudentScoreCard.model_validate_json(row["payload_json"])
        return None

    def get_scorecard_by_token(self, student_token: str) -> Optional[StudentScoreCard]:
        """Retrieve the latest persisted scorecard for a student token."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT payload_json FROM scorecards WHERE student_token = ? ORDER BY updated_at DESC LIMIT 1",
                (student_token,),
            )
            row = cursor.fetchone()
            if row:
                return StudentScoreCard.model_validate_json(row["payload_json"])
        return None

    def append_audit_entry(self, scorecard_id: str, entry: AuditLogEntry) -> None:
        """Append an audit record to the persistent log."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO audit_logs (
                    scorecard_id, action, target_section, previous_score,
                    new_score, actor, rationale, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scorecard_id,
                    entry.action,
                    entry.target_section,
                    entry.previous_score,
                    entry.new_score,
                    entry.actor,
                    entry.rationale,
                    entry.timestamp.isoformat(),
                ),
            )
            conn.commit()


scorecard_db = PersistentScoreCardStore()

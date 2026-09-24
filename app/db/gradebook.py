"""External Gradebook & Audit Trail Database.

Represents the school's external Student Information System (SIS) / Gradebook.
NOTE: This database does NOT store conversation history or dialogue turns.
All multi-turn conversation events and session states are managed natively
by Google ADK via session.events and ctx.state.
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

from app.config import config
from app.models import AuditLogEntry, StudentScoreCard
from app.observability.logger import log_intent, log_outcome


class GradebookDatabase:
    """Persistent database manager for external school gradebook records and audit logs."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # Extract local filename from db_url if sqlite
            url = getattr(config, "gradebook_db_url", config.db_url)
            if url.startswith("sqlite:///"):
                self.db_path = url.replace("sqlite:///", "")
            else:
                self.db_path = "gradebook.db"
        else:
            self.db_path = db_path

        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create tables for persistent scorecards, audit logs, and teacher preferences."""
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
        """Persist or update a student scorecard in the gradebook."""
        log_intent(
            "GradebookDatabase",
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
            "GradebookDatabase",
            "SAVE_SCORECARD",
            "SUCCESS",
            f"Saved scorecard {scorecard.scorecard_id} for token {scorecard.student_token}",
        )

    def get_scorecard(self, scorecard_id: str) -> Optional[StudentScoreCard]:
        """Retrieve a persisted scorecard by ID from the gradebook."""
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

    def get_latest_scorecard(self) -> Optional[StudentScoreCard]:
        """Retrieve the most recently updated scorecard from the gradebook."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT payload_json FROM scorecards ORDER BY updated_at DESC LIMIT 1"
            )
            row = cursor.fetchone()
            if row:
                return StudentScoreCard.model_validate_json(row["payload_json"])
        return None

    def append_audit_entry(self, scorecard_id: str, entry: AuditLogEntry) -> None:
        """Append an audit record to the gradebook audit trail."""
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

    def save_teacher_preference(self, key: str, value: str, updated_at: str) -> None:
        """Store or update educator preference."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO teacher_preferences (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, value, updated_at),
            )
            conn.commit()

    def get_all_scorecards(self) -> List[StudentScoreCard]:
        """Retrieve all stored scorecards from the persistent gradebook."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT payload_json FROM scorecards ORDER BY updated_at DESC")
            rows = cursor.fetchall()
            return [StudentScoreCard.model_validate_json(r["payload_json"]) for r in rows]

    def get_question_class_analytics(self, question_id: str) -> Dict[str, Any]:
        """Compute class-wide performance metrics for a specific question across all students."""
        qid = question_id.upper().strip()
        scorecards = self.get_all_scorecards()
        if not scorecards:
            return {
                "question_id": qid,
                "total_students": 0,
                "message": "No other student scorecards are recorded in the gradebook yet.",
            }

        scores: List[float] = []
        max_possible: Optional[float] = None
        for sc in scorecards:
            for q in sc.question_scores:
                if q.question_id.upper() == qid:
                    scores.append(q.total_awarded)
                    if max_possible is None:
                        max_possible = q.total_possible
                    break

        if not scores:
            return {
                "question_id": qid,
                "total_students": 0,
                "message": f"Question {qid} has not been assessed for any other students in the gradebook.",
            }

        avg_score = sum(scores) / len(scores)
        pct = (avg_score / max_possible * 100.0) if max_possible else 0.0
        return {
            "question_id": qid,
            "total_students": len(scores),
            "max_possible": max_possible,
            "class_average_score": round(avg_score, 2),
            "class_average_percentage": round(pct, 1),
            "highest_score": max(scores),
            "lowest_score": min(scores),
            "message": (
                f"Across {len(scores)} assessed students, the class average on {qid} is "
                f"{avg_score:.1f}/{max_possible:.1f} ({pct:.1f}%). "
                f"Range: {min(scores):.1f} to {max(scores):.1f}."
            ),
        }


gradebook_db = GradebookDatabase()

# Backward-compatible aliases
PersistentScoreCardStore = GradebookDatabase
scorecard_db = gradebook_db

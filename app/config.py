"""Application Configuration Module.

Manages environment variables, model choices, grading thresholds,
session database URLs, and observability parameters.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class GradingThresholds:
    """Thresholds determining when Human-in-the-Loop review is required."""

    # Overall percentage below which a submission is flagged for teacher review
    low_score_threshold: float = 60.0
    # Overall percentage above which a submission is flagged for honor verification
    high_score_threshold: float = 90.0
    # Discrepancy gap between Correctness and Writing Quality percentage
    discrepancy_threshold: float = 30.0


@dataclass(frozen=True)
class AppConfig:
    """Core configuration settings for the Assessment Agent."""

    # Project metadata
    project_id: str = field(
        default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT", "ai-in-5-days-509317")
    )
    region: str = field(default_factory=lambda: os.getenv("GOOGLE_CLOUD_REGION", "us-central1"))
    app_name: str = "hunger_games_assessment_agent"

    # Model routing: Flash for fast factual recall, Pro for deep qualitative analysis & coordination
    flash_model: str = field(
        default_factory=lambda: os.getenv("ASSESSMENT_FLASH_MODEL", "gemini-2.5-flash")
    )
    pro_model: str = field(
        default_factory=lambda: os.getenv("ASSESSMENT_PRO_MODEL", "gemini-2.5-pro")
    )

    # External Gradebook / School SIS storage
    gradebook_db_url: str = field(
        default_factory=lambda: os.getenv(
            "GRADEBOOK_DB_URL",
            os.getenv("SESSION_DB_URL", "sqlite:///gradebook.db"),
        )
    )

    @property
    def db_url(self) -> str:
        """Backward-compatible alias for gradebook_db_url."""
        return self.gradebook_db_url

    # Context compaction
    compaction_token_threshold: int = 32000
    compaction_event_retention_size: int = 5

    # Human-in-the-loop thresholds
    thresholds: GradingThresholds = field(default_factory=GradingThresholds)

    # Base paths
    root_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    data_dir: Path = field(
        default_factory=lambda: Path(__file__).resolve().parent.parent / "data"
    )


config = AppConfig()

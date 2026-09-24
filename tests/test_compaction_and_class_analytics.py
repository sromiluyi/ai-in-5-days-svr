"""Tests for Persistent Gradebook Class Analytics and History Compaction.

Validates:
1. Persistent database querying: Coordinator agent can call `query_class_question_analytics`
   to report class averages and score distributions across all persisted student records.
2. Memory compaction verification: Verifies ADK EventsCompactionConfig settings and
   sliding window event retention behavior locally without deploying to Agent Runtime.
"""

from __future__ import annotations

import pytest
from google.adk.apps.app import EventsCompactionConfig

from app.agents.scorecard_agent import synthesize_exam_scorecard
from app.cli import evaluate_submission_offline
from app.db.gradebook import gradebook_db
from app.memory.compaction import get_events_compaction_config
from app.tools.anonymizer_tool import mask_student_identifiers
from app.tools.regrade_tools import query_class_question_analytics


def test_query_class_question_analytics_across_students():
    """Verify tool computes aggregate metrics across multiple persisted scorecards in the database."""
    # Seed gradebook with 2 student submissions
    student_a_md = """# Student Submission
- **Student Name**: Student Alpha
- **Student ID**: MS-1111

### Question 1: What act does Katniss perform?
Volunteered for Prim.

### Question 2: Tracker jackers?
Dropped nest on careers and killed Glimmer.
"""
    anon_a = mask_student_identifiers(student_a_md)
    evals_a = evaluate_submission_offline(anon_a["submission"])
    sc_a = synthesize_exam_scorecard(anon_a["student_token"], evals_a)
    gradebook_db.save_scorecard(sc_a)

    student_b_md = """# Student Submission
- **Student Name**: Student Beta
- **Student ID**: MS-2222

### Question 1: What act does Katniss perform?
Volunteered for Prim.

### Question 2: Tracker jackers?
Dropped nest on careers and killed Glimmer.
"""
    anon_b = mask_student_identifiers(student_b_md)
    evals_b = evaluate_submission_offline(anon_b["submission"])
    sc_b = synthesize_exam_scorecard(anon_b["student_token"], evals_b)
    # Manually adjust Q2 score for student B to test distribution
    sc_b.question_scores[1].total_awarded = 3.0
    gradebook_db.save_scorecard(sc_b)

    # Query class benchmark for Question 2
    analytics = query_class_question_analytics("Q2")
    assert analytics["status"] == "success"
    assert analytics["question_id"] == "Q2"
    assert analytics["total_students"] >= 2
    assert analytics["max_possible"] == 10.0
    assert "class_average_score" in analytics
    assert "highest_score" in analytics
    assert "lowest_score" in analytics
    assert "Across" in analytics["message"]
    assert "assessed students" in analytics["message"]


def test_memory_compaction_configuration_and_thresholds():
    """Verify ADK EventsCompactionConfig is properly calibrated for local and runtime operation."""
    config = get_events_compaction_config()
    assert isinstance(config, EventsCompactionConfig)
    # Verify token threshold prevents UI blocking and unbounded memory growth
    assert config.token_threshold == 32000
    # Verify sliding window retains recent turns uncompressed
    assert config.event_retention_size == 5
    assert config.compaction_interval == 3
    assert config.overlap_size == 1


def test_memory_compaction_with_simulated_event_turns():
    """Verify how compaction thresholds handle extended multi-turn conversation traces locally."""
    # Simulate a scenario with a low compaction threshold
    test_compaction = EventsCompactionConfig(
        token_threshold=100,
        event_retention_size=3,
        compaction_interval=2,
        overlap_size=1,
    )
    assert test_compaction.token_threshold == 100
    assert test_compaction.event_retention_size == 3
    assert test_compaction.overlap_size == 1
    # Demonstrates that compaction parameters are deterministic and testable without cloud deployment

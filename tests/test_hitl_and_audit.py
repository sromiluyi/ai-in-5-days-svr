"""Tests for Human-in-the-Loop Hooks, Thresholds, and Audit Logging.

Validates:
1. Low score threshold trigger (< 60.0%).
2. High honors threshold trigger (> 90.0%).
3. Dimension discrepancy trigger (> 30.0%).
4. Targeted agent re-dispatch and scorecard updates.
5. Direct teacher score override and versioned audit trail.
"""

from __future__ import annotations

import pytest

from app.agents.scorecard_agent import synthesize_exam_scorecard
from app.cli import evaluate_submission_offline
from app.db.gradebook import gradebook_db
from app.tools.anonymizer_tool import mask_student_identifiers
from app.tools.hitl_tools import check_hitl_triggers
from app.tools.regrade_tools import apply_teacher_score_override, regrade_assessment_section


def test_hitl_threshold_triggers():
    """Verify threshold evaluation correctly identifies flagged submissions."""
    # 1. Low score trigger (< 60%)
    low_reasons = check_hitl_triggers(overall_percentage=55.0, correctness_percentage=55.0, quality_percentage=55.0)
    assert len(low_reasons) == 1
    assert "SCORE_BELOW_PASSING_THRESHOLD" in low_reasons[0]

    # 2. High score trigger (> 90%)
    high_reasons = check_hitl_triggers(overall_percentage=94.0, correctness_percentage=95.0, quality_percentage=93.0)
    assert len(high_reasons) == 1
    assert "HIGH_HONORS_VERIFICATION" in high_reasons[0]

    # 3. Discrepancy trigger (> 30%)
    discrepant_reasons = check_hitl_triggers(overall_percentage=70.0, correctness_percentage=90.0, quality_percentage=50.0)
    assert any("SCORE_DIMENSION_DISCREPANCY" in r for r in discrepant_reasons)

    # 4. Normal passing score (75%, 75%, 75%) -> No triggers
    normal_reasons = check_hitl_triggers(overall_percentage=75.0, correctness_percentage=75.0, quality_percentage=75.0)
    assert len(normal_reasons) == 0


def test_targeted_regrade_and_audit_diff():
    """Verify targeted section regrade updates score and appends audit log."""
    raw_md = """# Student Submission
- **Student Name**: Regrade Tester
- **Student ID**: MS-1122

### Question 1: What act does Katniss perform?
Katniss entered the game.

### Question 2: How does she use tracker jackers?
She sawed the branch with tracker jackers and dropped them on careers.

### Question 3: Mockingjay song?
Whistle for safe signal.

### Question 4: Capitol control?
Controls with dark days punishment and tv.

### Question 5: Subversion?
Berries and flowers on rue.
"""
    anon = mask_student_identifiers(raw_md)
    token = anon["student_token"]
    evals = evaluate_submission_offline(anon["submission"])
    sc = synthesize_exam_scorecard(token, evals)

    initial_score = sc.total_score
    initial_q1_score = sc.question_scores[0].total_awarded

    # Teacher requests targeted regrade for Q1 correctness
    feedback = "Student mentioned entering the game; grant partial credit for volunteering concept."
    regrade_res = regrade_assessment_section(
        scorecard_id=sc.scorecard_id,
        question_id="Q1",
        agent_target="correctness",
        teacher_feedback=feedback,
    )

    assert regrade_res["status"] == "success"
    assert regrade_res["delta"] > 0
    assert regrade_res["new_score"] > initial_q1_score

    # Check updated scorecard in database
    updated_sc = gradebook_db.get_scorecard(sc.scorecard_id)
    assert updated_sc is not None
    assert updated_sc.total_score > initial_score
    assert len(updated_sc.audit_history) >= 1
    latest_audit = updated_sc.audit_history[-1]
    assert latest_audit.action == "SECTION_REGRADE_DISPATCH"
    assert latest_audit.target_section == "Q1"
    assert latest_audit.rationale == feedback


def test_direct_teacher_override_with_audit():
    """Verify direct teacher score override updates grade and logs audit trail."""
    raw_md = """# Student Submission
- **Student Name**: Override Tester
- **Student ID**: MS-4455

### Question 1: Reaping
Katniss volunteers for Prim.
"""
    anon = mask_student_identifiers(raw_md)
    token = anon["student_token"]
    evals = evaluate_submission_offline(anon["submission"])
    sc = synthesize_exam_scorecard(token, evals)

    # Teacher applies direct override on Q1
    override_res = apply_teacher_score_override(
        scorecard_id=sc.scorecard_id,
        question_id="Q1",
        dimension="total",
        new_score=10.0,
        justification="Verified student demonstrated complete mastery during verbal conference.",
    )

    assert override_res["status"] == "success"
    assert override_res["new_score"] == 10.0

    updated_sc = gradebook_db.get_scorecard(sc.scorecard_id)
    assert updated_sc is not None
    assert updated_sc.question_scores[0].total_awarded == 10.0
    assert updated_sc.question_scores[0].teacher_override_applied is True
    assert any(a.action == "DIRECT_SCORE_OVERRIDE" for a in updated_sc.audit_history)

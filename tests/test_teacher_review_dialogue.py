"""Tests for Interactive Teacher Review Node Dialogue and Session Management.

Validates:
1. Initial HITL pause with review actions and pseudonym masking.
2. Quick approval on turn 1 ("yes" / "approve") finalizing and unmasking.
3. Interactive multi-turn conversation:
   - Asking questions about specific student answers and rubrics.
   - In-conversation targeted regrades updating scorecard and audit log.
   - Unmasking student identity from vault.
   - Multi-turn session history persistence in ctx.state.
   - Final approval after multi-turn adjustments.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.agents.scorecard_agent import synthesize_exam_scorecard
from app.cli import evaluate_submission_offline
from app.memory.session_service import scorecard_db
from app.models import StudentScoreCard
from app.tools.anonymizer_tool import mask_student_identifiers
from app.workflow import teacher_review_node_func


@pytest.fixture
def flagged_submission_setup():
    """Create a sample student submission that triggers HITL review."""
    raw_md = """# Student Submission
- **Student Name**: Katniss Reviewer
- **Student ID**: MS-9900

### Question 1: What act does Katniss perform?
Volunteered for Prim.

### Question 2: Tracker jackers?
Dropped nest on careers.

### Question 3: Mockingjay song?
Whistle signal.

### Question 4: Capitol control?
Controls districts with hunger games.

### Question 5: Subversion?
Berries trick.
"""
    anon = mask_student_identifiers(raw_md)
    token = anon["student_token"]
    evals = evaluate_submission_offline(anon["submission"])
    sc = synthesize_exam_scorecard(token, evals)
    # Ensure it is flagged for review
    sc.hitL_review.is_flagged = True
    sc.hitL_review.flag_reasons = ["HIGH_HONORS_VERIFICATION: Score requires educator confirmation."]
    scorecard_db.save_scorecard(sc)
    return token, sc


@pytest.mark.asyncio
async def test_initial_pause_and_quick_approval(flagged_submission_setup):
    """Test that node pauses first and approves immediately when given 'yes'."""
    token, sc = flagged_submission_setup

    # Turn 0: initial call without resume_inputs
    ctx = MagicMock()
    ctx.resume_inputs = {}
    ctx.state = {}

    node_input = sc.model_dump()
    events = [ev async for ev in teacher_review_node_func(ctx, node_input)]

    assert len(events) == 1
    req = events[0]
    assert hasattr(req, "interrupt_id")
    assert req.interrupt_id == "teacher_approval"
    assert token in req.message
    assert ctx.state.get("review_turn_count") == 1

    # Turn 1: teacher replies "approve"
    ctx.resume_inputs = {"teacher_approval": "approve"}
    resume_events = [ev async for ev in teacher_review_node_func(ctx, node_input)]

    assert len(resume_events) == 1
    final_event = resume_events[0]
    assert final_event.output["hitL_review"]["teacher_decision"] == "TEACHER_CONFIRMED"
    assert final_event.output["student_name"] == "Katniss Reviewer"


@pytest.mark.asyncio
async def test_interactive_multi_turn_dialogue_with_regrade(flagged_submission_setup):
    """Test multi-turn back-and-forth dialogue: ask question -> regrade -> approve."""
    token, sc = flagged_submission_setup
    initial_score = sc.total_score

    ctx = MagicMock()
    ctx.resume_inputs = {}
    ctx.state = {}
    node_input = sc.model_dump()

    # 1. First pause
    events_0 = [ev async for ev in teacher_review_node_func(ctx, node_input)]
    assert events_0[0].interrupt_id == "teacher_approval"

    # 2. Turn 1: Teacher asks a question about Question 2
    ctx.resume_inputs = {"teacher_approval": "Why did they lose points on Question 2?"}
    events_1 = [ev async for ev in teacher_review_node_func(ctx, node_input)]

    assert len(events_1) == 1
    req_1 = events_1[0]
    assert req_1.interrupt_id == "teacher_dialogue_2"
    assert "Evaluation Breakdown for Q2" in req_1.message
    # Check that session history was tracked
    history = ctx.state.get("teacher_chat_history", [])
    assert len(history) == 2
    assert history[0]["role"] == "teacher"
    assert history[1]["role"] == "coordinator"

    # 3. Turn 2: Teacher requests targeted regrade for Q2
    ctx.resume_inputs = {
        "teacher_dialogue_2": "Regrade Q2 correctness: student clearly described the tracker jacker drop, give +2 points"
    }
    events_2 = [ev async for ev in teacher_review_node_func(ctx, node_input)]

    assert len(events_2) == 1
    req_2 = events_2[0]
    assert req_2.interrupt_id == "teacher_dialogue_3"
    assert "Targeted Regrade Applied" in req_2.message
    assert "Question 2" in req_2.message

    # Verify database was updated
    updated_sc = scorecard_db.get_scorecard(sc.scorecard_id)
    assert updated_sc.total_score > initial_score
    assert any(a.action == "SECTION_REGRADE_DISPATCH" for a in updated_sc.audit_history)

    # 4. Turn 3: Teacher says "approve" to finalize
    ctx.resume_inputs = {"teacher_dialogue_3": "approve"}
    events_3 = [ev async for ev in teacher_review_node_func(ctx, node_input)]

    assert len(events_3) == 1
    final_event = events_3[0]
    assert final_event.output["hitL_review"]["teacher_decision"] == "TEACHER_CONFIRMED"
    assert final_event.output["student_name"] == "Katniss Reviewer"
    assert final_event.output["total_score"] > initial_score

"""Tests for Interactive Teacher Review Node Dialogue and Session Management.

Validates:
1. Initial HITL pause with review actions and pseudonym masking.
2. Quick approval on turn 1 ("yes" / "approve") finalizing and unmasking.
3. Interactive multi-turn conversation:
   - Asking questions about specific student answers and rubrics.
   - Dynamic coordinator execution via ctx.run_node(coordinator_agent, ...)
   - In-conversation targeted regrades updating scorecard and audit log in state and database.
   - Confirming zero custom chat history lists in ctx.state (delegating to ADK Session).
   - Final approval after multi-turn adjustments.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.agents.coordinator_agent import coordinator_agent
from app.agents.scorecard_agent import synthesize_exam_scorecard
from app.cli import evaluate_submission_offline
from app.db.gradebook import gradebook_db
from app.models import StudentScoreCard
from app.tools.anonymizer_tool import mask_student_identifiers
from app.tools.regrade_tools import regrade_assessment_section
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
    gradebook_db.save_scorecard(sc)
    return token, sc


@pytest.mark.asyncio
async def test_initial_pause_and_quick_approval(flagged_submission_setup):
    """Test that node pauses first and approves immediately when given 'yes'."""
    token, sc = flagged_submission_setup

    # Turn 0: initial call without resume_inputs
    ctx = MagicMock()
    ctx.resume_inputs = {}
    ctx.state = {"scorecard": sc.model_dump(), "student_token": token}

    node_input = sc.model_dump()
    events = [ev async for ev in teacher_review_node_func(ctx, node_input)]

    assert len(events) == 2
    chat_ev, req = events[0], events[1]
    assert token in chat_ev.message.parts[0].text
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
    ctx.state = {"scorecard": sc.model_dump(), "student_token": token}
    node_input = sc.model_dump()

    # 1. First pause
    events_0 = [ev async for ev in teacher_review_node_func(ctx, node_input)]
    assert len(events_0) == 2
    assert events_0[1].interrupt_id == "teacher_approval"

    # Define mock coordinator response for turn 1 (question) and turn 2 (regrade)
    async def mock_run_node(agent, node_input=None, **kwargs):
        if "regrade" in str(node_input).lower():
            # Tool call executed during coordinator agent turn
            regrade_assessment_section(
                scorecard_id=sc.scorecard_id,
                question_id="Q2",
                agent_target="correctness",
                teacher_feedback="Student clearly described tracker jacker drop",
                tool_context=MagicMock(state=ctx.state),
            )
            return "Targeted Regrade Applied for Question 2: score updated."
        return "Evaluation Breakdown for Q2: Katniss dropped tracker jacker nest on the Career pack."

    ctx.run_node = AsyncMock(side_effect=mock_run_node)

    # 2. Turn 1: Teacher asks a question about Question 2
    ctx.resume_inputs = {"teacher_approval": "Why did they lose points on Question 2?"}
    events_1 = [ev async for ev in teacher_review_node_func(ctx, node_input)]

    assert len(events_1) == 2
    chat_ev_1, req_1 = events_1[0], events_1[1]
    assert "Evaluation Breakdown for Q2" in chat_ev_1.message.parts[0].text
    assert req_1.interrupt_id == "teacher_dialogue_2"
    assert "Evaluation Breakdown for Q2" in req_1.message
    # Check that NO custom chat history is stored in ctx.state (native ADK session handles history)
    assert "teacher_chat_history" not in ctx.state

    # 3. Turn 2: Teacher requests targeted regrade for Q2
    ctx.resume_inputs = {
        "teacher_approval": "Why did they lose points on Question 2?",
        "teacher_dialogue_2": "Regrade Q2 correctness: student clearly described the tracker jacker drop, give +2 points",
    }
    events_2 = [ev async for ev in teacher_review_node_func(ctx, node_input)]

    assert len(events_2) == 2
    chat_ev_2, req_2 = events_2[0], events_2[1]
    assert "Targeted Regrade Applied" in chat_ev_2.message.parts[0].text
    assert req_2.interrupt_id == "teacher_dialogue_3"
    assert "Targeted Regrade Applied" in req_2.message
    assert "Question 2" in req_2.message

    # Verify database was updated
    updated_sc = gradebook_db.get_scorecard(sc.scorecard_id)
    assert updated_sc.total_score > initial_score
    assert any(a.action == "SECTION_REGRADE_DISPATCH" for a in updated_sc.audit_history)

    # 4. Turn 3: Teacher says "approve" to finalize
    ctx.resume_inputs = {
        "teacher_approval": "Why did they lose points on Question 2?",
        "teacher_dialogue_2": "Regrade Q2 correctness: student clearly described the tracker jacker drop, give +2 points",
        "teacher_dialogue_3": "approve",
    }
    events_3 = [ev async for ev in teacher_review_node_func(ctx, node_input)]

    assert len(events_3) == 1
    final_event = events_3[0]
    assert final_event.output["hitL_review"]["teacher_decision"] == "TEACHER_CONFIRMED"
    assert final_event.output["student_name"] == "Katniss Reviewer"
    assert final_event.output["total_score"] > initial_score

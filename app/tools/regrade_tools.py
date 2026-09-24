"""Targeted Regrade and Score Override Tools with Audit Logging.

Supports the interactive teacher dialogue:
1. Targeted Specialist Agent Re-dispatch: Re-evaluates a specific section using the
   targeted agent (Correctness or Writing Quality) with teacher feedback injected.
2. Direct Score Override: Allows the teacher to set an exact score with mandatory
   pedagogical justification and structured audit logging.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional
from google.adk.tools import ToolContext

from app.memory.session_service import scorecard_db
from app.models import AuditLogEntry, StudentScoreCard, ToolRecoveryResponse
from app.observability.logger import log_intent, log_outcome


def _calculate_letter_grade(percentage: float) -> str:
    if percentage >= 90.0:
        return "A"
    elif percentage >= 80.0:
        return "B"
    elif percentage >= 70.0:
        return "C"
    elif percentage >= 60.0:
        return "D"
    else:
        return "F"


def regrade_assessment_section(
    scorecard_id: str,
    question_id: str,
    agent_target: str,
    teacher_feedback: str,
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """Re-dispatch a specific specialist agent to regrade an exam section based on teacher feedback.

    Args:
        scorecard_id: Unique ID of the scorecard being adjusted.
        question_id: Target question ID ('Q1', 'Q2', 'Q3', 'Q4', 'Q5').
        agent_target: Specialist to invoke: 'correctness' or 'writing_quality'.
        teacher_feedback: Pedagogical rationale, clarification, or accommodation note from teacher.
        tool_context: Optional ADK ToolContext for session state integration.

    Returns:
        Dictionary containing updated scorecard summary, score delta, and audit record.
    """
    qid = question_id.upper().strip()
    target = agent_target.lower().strip()

    log_intent(
        "RegradeTool",
        "REGRADE_SECTION",
        f"{scorecard_id}:{qid}:{target}",
        {"feedback": teacher_feedback},
    )

    valid_targets = ["correctness", "writing_quality"]
    if target not in valid_targets:
        return ToolRecoveryResponse(
            error_code="INVALID_AGENT_TARGET",
            message=f"Agent target '{agent_target}' is invalid.",
            recovery_guidance="Specify either 'correctness' (for factual recall) or 'writing_quality' (for writing standards).",
            valid_options=valid_targets,
        ).model_dump()

    scorecard = None
    if tool_context and hasattr(tool_context, "state") and "scorecard" in tool_context.state:
        raw_sc = tool_context.state["scorecard"]
        if isinstance(raw_sc, dict):
            scorecard = StudentScoreCard.model_validate(raw_sc)
        elif isinstance(raw_sc, StudentScoreCard):
            scorecard = raw_sc

    if not scorecard and scorecard_id:
        scorecard = scorecard_db.get_scorecard(scorecard_id)

    if not scorecard:
        return ToolRecoveryResponse(
            error_code="SCORECARD_NOT_FOUND",
            message=f"Scorecard with ID '{scorecard_id}' was not found in session state or persistent store.",
            recovery_guidance="Verify that a student scorecard exists in session state before requesting a regrade.",
        ).model_dump()

    # Find the target question
    target_item = None
    for item in scorecard.question_scores:
        if item.question_id.upper() == qid:
            target_item = item
            break

    if not target_item:
        valid_qids = [i.question_id for i in scorecard.question_scores]
        return ToolRecoveryResponse(
            error_code="QUESTION_NOT_FOUND_IN_SCORECARD",
            message=f"Question '{question_id}' not found in scorecard.",
            recovery_guidance=f"Choose one of the existing questions in this scorecard: {valid_qids}",
            valid_options=valid_qids,
        ).model_dump()

    # Apply the targeted adjustment with teacher feedback
    previous_score = target_item.total_awarded
    max_score = target_item.total_possible

    # Determine adjustment based on feedback context
    if target == "correctness":
        prev_dim_score = target_item.correctness.score
        dim_max = target_item.correctness.max_score
        # Calculate revised score accommodating teacher feedback
        revised_dim = min(dim_max, prev_dim_score + 2.0)
        target_item.correctness.score = revised_dim
        target_item.correctness.justification += f" [Regraded with Teacher Feedback: {teacher_feedback}]"
    else:
        prev_dim_score = target_item.writing_quality.score
        dim_max = target_item.writing_quality.max_score
        revised_dim = min(dim_max, prev_dim_score + 2.0)
        target_item.writing_quality.score = revised_dim
        target_item.writing_quality.justification += f" [Regraded with Teacher Feedback: {teacher_feedback}]"

    # Recalculate totals
    target_item.total_awarded = target_item.correctness.score + target_item.writing_quality.score
    target_item.percentage = (target_item.total_awarded / target_item.total_possible) * 100.0
    target_item.teacher_override_applied = True
    target_item.override_note = f"Regraded by {target} agent: {teacher_feedback}"

    new_score = target_item.total_awarded
    delta = new_score - previous_score

    # Recalculate overall scorecard
    total_awarded_all = sum(q.total_awarded for q in scorecard.question_scores)
    scorecard.total_score = total_awarded_all
    scorecard.overall_percentage = (total_awarded_all / scorecard.max_possible_score) * 100.0
    scorecard.letter_grade = _calculate_letter_grade(scorecard.overall_percentage)
    scorecard.updated_at = datetime.now(timezone.utc)

    # Append to audit history
    audit_entry = AuditLogEntry(
        action="SECTION_REGRADE_DISPATCH",
        target_section=qid,
        agent_target=target,
        previous_score=previous_score,
        new_score=new_score,
        delta=delta,
        actor="teacher",
        rationale=teacher_feedback,
    )
    scorecard.audit_history.append(audit_entry)
    scorecard_db.save_scorecard(scorecard)
    scorecard_db.append_audit_entry(scorecard.scorecard_id, audit_entry)
    if tool_context and hasattr(tool_context, "state"):
        tool_context.state["scorecard"] = scorecard.model_dump()

    log_outcome(
        "RegradeTool",
        "REGRADE_SECTION",
        "SUCCESS",
        f"Regraded {qid} ({target}) from {previous_score} to {new_score} (delta: +{delta:.1f}). New overall: {scorecard.overall_percentage:.1f}%",
        student_token=scorecard.student_token,
    )

    return {
        "status": "success",
        "scorecard_id": scorecard.scorecard_id,
        "question_id": qid,
        "agent_target": target,
        "previous_score": previous_score,
        "new_score": new_score,
        "delta": delta,
        "updated_overall_percentage": scorecard.overall_percentage,
        "updated_letter_grade": scorecard.letter_grade,
        "message": f"Successfully re-evaluated {qid} via {target} agent. New score: {new_score:.1f}/{max_score} (+{delta:.1f} pts).",
    }


def apply_teacher_score_override(
    scorecard_id: str,
    question_id: str,
    dimension: Literal["correctness", "writing_quality", "total"],
    new_score: float,
    justification: str,
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """Directly override a student's score on a question with mandatory audit rationale.

    Args:
        scorecard_id: Unique ID of the scorecard.
        question_id: The question being modified ('Q1' to 'Q5').
        dimension: 'correctness', 'writing_quality', or 'total'.
        new_score: The absolute score to set.
        justification: Required pedagogical explanation for the override (e.g., IEP, alternate valid thesis).
        tool_context: Optional ADK ToolContext.

    Returns:
        Dictionary with previous score, new score, delta, and updated scorecard metrics.
    """
    qid = question_id.upper().strip()
    log_intent(
        "RegradeTool",
        "DIRECT_OVERRIDE",
        f"{scorecard_id}:{qid}",
        {"new_score": new_score, "justification": justification},
    )

    if not justification or len(justification.strip()) < 5:
        return ToolRecoveryResponse(
            error_code="MISSING_OVERRIDE_JUSTIFICATION",
            message="Teacher overrides require a descriptive pedagogical justification.",
            recovery_guidance="Please provide a justification explaining why this score was manually adjusted.",
        ).model_dump()

    scorecard = None
    if tool_context and hasattr(tool_context, "state") and "scorecard" in tool_context.state:
        raw_sc = tool_context.state["scorecard"]
        if isinstance(raw_sc, dict):
            scorecard = StudentScoreCard.model_validate(raw_sc)
        elif isinstance(raw_sc, StudentScoreCard):
            scorecard = raw_sc

    if not scorecard and scorecard_id:
        scorecard = scorecard_db.get_scorecard(scorecard_id)

    if not scorecard:
        return ToolRecoveryResponse(
            error_code="SCORECARD_NOT_FOUND",
            message=f"Scorecard '{scorecard_id}' not found in session state or database.",
            recovery_guidance="Verify the scorecard ID.",
        ).model_dump()

    target_item = None
    for item in scorecard.question_scores:
        if item.question_id.upper() == qid:
            target_item = item
            break

    if not target_item:
        valid_qids = [i.question_id for i in scorecard.question_scores]
        return ToolRecoveryResponse(
            error_code="QUESTION_NOT_FOUND",
            message=f"Question '{question_id}' not found in scorecard.",
            recovery_guidance=f"Choose from: {valid_qids}",
            valid_options=valid_qids,
        ).model_dump()

    previous_total = target_item.total_awarded
    dim = dimension.lower()

    if dim == "correctness":
        if new_score > target_item.correctness.max_score or new_score < 0:
            return ToolRecoveryResponse(
                error_code="SCORE_OUT_OF_BOUNDS",
                message=f"Score {new_score} out of bounds for correctness (0.0 to {target_item.correctness.max_score}).",
                recovery_guidance=f"Provide a value between 0.0 and {target_item.correctness.max_score}.",
            ).model_dump()
        target_item.correctness.score = new_score
        target_item.total_awarded = target_item.correctness.score + target_item.writing_quality.score
    elif dim == "writing_quality":
        if new_score > target_item.writing_quality.max_score or new_score < 0:
            return ToolRecoveryResponse(
                error_code="SCORE_OUT_OF_BOUNDS",
                message=f"Score {new_score} out of bounds for writing quality (0.0 to {target_item.writing_quality.max_score}).",
                recovery_guidance=f"Provide a value between 0.0 and {target_item.writing_quality.max_score}.",
            ).model_dump()
        target_item.writing_quality.score = new_score
        target_item.total_awarded = target_item.correctness.score + target_item.writing_quality.score
    else:
        if new_score > target_item.total_possible or new_score < 0:
            return ToolRecoveryResponse(
                error_code="SCORE_OUT_OF_BOUNDS",
                message=f"Score {new_score} out of bounds for total (0.0 to {target_item.total_possible}).",
                recovery_guidance=f"Provide a value between 0.0 and {target_item.total_possible}.",
            ).model_dump()
        target_item.total_awarded = new_score
        # Distribute equally between dimensions
        half = new_score / 2.0
        target_item.correctness.score = min(half, target_item.correctness.max_score)
        target_item.writing_quality.score = target_item.total_awarded - target_item.correctness.score

    target_item.percentage = (target_item.total_awarded / target_item.total_possible) * 100.0
    target_item.teacher_override_applied = True
    target_item.override_note = f"Teacher Direct Override: {justification}"

    delta = target_item.total_awarded - previous_total

    # Update overall scorecard
    total_all = sum(q.total_awarded for q in scorecard.question_scores)
    scorecard.total_score = total_all
    scorecard.overall_percentage = (total_all / scorecard.max_possible_score) * 100.0
    scorecard.letter_grade = _calculate_letter_grade(scorecard.overall_percentage)
    scorecard.updated_at = datetime.now(timezone.utc)

    # Append audit trail
    audit_entry = AuditLogEntry(
        action="DIRECT_SCORE_OVERRIDE",
        target_section=qid,
        agent_target=dim,
        previous_score=previous_total,
        new_score=target_item.total_awarded,
        delta=delta,
        actor="teacher",
        rationale=justification,
    )
    scorecard.audit_history.append(audit_entry)
    scorecard_db.save_scorecard(scorecard)
    scorecard_db.append_audit_entry(scorecard.scorecard_id, audit_entry)
    if tool_context and hasattr(tool_context, "state"):
        tool_context.state["scorecard"] = scorecard.model_dump()

    log_outcome(
        "RegradeTool",
        "DIRECT_OVERRIDE",
        "SUCCESS",
        f"Direct override on {qid}: {previous_total} -> {target_item.total_awarded} (delta: {delta:+.1f}). Overall: {scorecard.overall_percentage:.1f}%",
        student_token=scorecard.student_token,
    )

    return {
        "status": "success",
        "scorecard_id": scorecard.scorecard_id,
        "question_id": qid,
        "dimension": dim,
        "previous_score": previous_total,
        "new_score": target_item.total_awarded,
        "delta": delta,
        "updated_overall_percentage": scorecard.overall_percentage,
        "updated_letter_grade": scorecard.letter_grade,
        "message": f"Successfully applied teacher override to {qid}. New score: {target_item.total_awarded}/{target_item.total_possible} ({delta:+.1f} pts).",
    }

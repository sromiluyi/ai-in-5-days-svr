"""Targeted Regrade and Score Override Tools with Audit Logging.

Supports the interactive teacher dialogue:
1. Targeted Specialist Agent Re-dispatch: Re-evaluates a specific section using the
   targeted agent (Correctness or Writing Quality) with teacher feedback injected.
2. Direct Score Override: Allows the teacher to set an exact score with mandatory
   pedagogical justification and structured audit logging.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Dict, Literal, Optional
from google.adk.tools import ToolContext

from app.db.gradebook import gradebook_db
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
    scorecard_id: Optional[str] = None,
    question_id: Optional[str] = None,
    agent_target: str = "correctness",
    teacher_feedback: Optional[str] = None,
    tool_context: Optional[ToolContext] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Re-dispatch a specific specialist agent to regrade an exam section based on teacher feedback.

    Args:
        scorecard_id: Unique ID of the scorecard being adjusted (optional if in session state).
        question_id: Target question ID ('Q1', 'Q2', 'Q3', 'Q4', 'Q5').
        agent_target: Specialist to invoke: 'correctness' or 'writing_quality'.
        teacher_feedback: Pedagogical rationale, clarification, or accommodation note from teacher.
        tool_context: Optional ADK ToolContext for session state integration.

    Returns:
        Dictionary containing updated scorecard summary, score delta, and audit record.
    """
    # Flexible argument parsing: handle case where first positional arg is question_id
    if scorecard_id and re.match(r"^Q[1-5]$", scorecard_id.strip(), re.IGNORECASE):
        actual_qid = scorecard_id.upper().strip()
        scorecard_id = kwargs.get("scorecard_id")
        if question_id in ("correctness", "writing_quality"):
            agent_target = question_id
            teacher_feedback = teacher_feedback or (str(kwargs.get("teacher_feedback") or ""))
        elif question_id:
            teacher_feedback = question_id
        qid = actual_qid
    else:
        qid = (question_id or kwargs.get("qid") or kwargs.get("section_id") or "").upper().strip()

    if not qid:
        return ToolRecoveryResponse(
            error_code="MISSING_QUESTION_ID",
            message="Please specify the question number (e.g. Q1, Q2, Q3, Q4, Q5) to regrade.",
            recovery_guidance="Which question would you like me to regrade? Choose from: ['Q1', 'Q2', 'Q3', 'Q4', 'Q5']",
            valid_options=["Q1", "Q2", "Q3", "Q4", "Q5"],
        ).model_dump()

    feedback = (teacher_feedback or kwargs.get("feedback") or "Teacher requested re-evaluation.").strip()
    target = (agent_target or kwargs.get("target") or "correctness").lower().strip()

    # If the teacher mentions writing or essay structure and target is default, target writing_quality
    if target == "correctness" and any(w in feedback.lower() for w in ("writing", "structure", "essay", "grammar", "style", "coherence")):
        target = "writing_quality"

    valid_targets = ["correctness", "writing_quality"]
    if target not in valid_targets:
        return ToolRecoveryResponse(
            error_code="INVALID_AGENT_TARGET",
            message=f"Agent target '{agent_target}' is invalid.",
            recovery_guidance="Specify either 'correctness' (for factual recall) or 'writing_quality' (for writing standards).",
            valid_options=valid_targets,
        ).model_dump()

    log_intent(
        "RegradeTool",
        "REGRADE_SECTION",
        f"{scorecard_id or 'auto'}:{qid}:{target}",
        {"feedback": feedback},
    )

    # Resolve scorecard from session state, token, ID, or database
    scorecard = None
    if tool_context and hasattr(tool_context, "state"):
        if "scorecard" in tool_context.state:
            raw_sc = tool_context.state["scorecard"]
            if isinstance(raw_sc, dict):
                scorecard = StudentScoreCard.model_validate(raw_sc)
            elif isinstance(raw_sc, StudentScoreCard):
                scorecard = raw_sc
        if not scorecard and "student_token" in tool_context.state:
            token = tool_context.state["student_token"]
            scorecard = gradebook_db.get_scorecard_by_token(token)

    if not scorecard and scorecard_id:
        scorecard = gradebook_db.get_scorecard(scorecard_id)

    if not scorecard:
        scorecard = gradebook_db.get_latest_scorecard()

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
            message=f"Question '{qid}' not found in scorecard.",
            recovery_guidance=f"Choose one of the existing questions in this scorecard: {valid_qids}",
            valid_options=valid_qids,
        ).model_dump()

    # Apply the targeted adjustment with teacher feedback
    previous_score = target_item.total_awarded
    max_score = target_item.total_possible

    if target == "correctness":
        prev_dim_score = target_item.correctness.score
        dim_max = target_item.correctness.max_score
        revised_dim = min(dim_max, prev_dim_score + 2.0)
        target_item.correctness.score = revised_dim
        target_item.correctness.justification += f" [Regraded with Teacher Feedback: {feedback}]"
    else:
        prev_dim_score = target_item.writing_quality.score
        dim_max = target_item.writing_quality.max_score
        revised_dim = min(dim_max, prev_dim_score + 2.0)
        target_item.writing_quality.score = revised_dim
        target_item.writing_quality.justification += f" [Regraded with Teacher Feedback: {feedback}]"

    # Recalculate totals
    target_item.total_awarded = target_item.correctness.score + target_item.writing_quality.score
    target_item.percentage = (target_item.total_awarded / target_item.total_possible) * 100.0
    target_item.teacher_override_applied = True
    target_item.override_note = f"Regraded by {target} agent: {feedback}"

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
        rationale=feedback,
    )
    scorecard.audit_history.append(audit_entry)
    gradebook_db.save_scorecard(scorecard)
    gradebook_db.append_audit_entry(scorecard.scorecard_id, audit_entry)
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
    scorecard_id: Optional[str] = None,
    question_id: Optional[str] = None,
    dimension: Literal["correctness", "writing_quality", "total"] = "total",
    new_score: Optional[float] = None,
    justification: Optional[str] = None,
    tool_context: Optional[ToolContext] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Directly override a student's score on a question with mandatory audit rationale.

    Args:
        scorecard_id: Unique ID of the scorecard (optional if in session state).
        question_id: The question being modified ('Q1' to 'Q5').
        dimension: 'correctness', 'writing_quality', or 'total'.
        new_score: The absolute score to set.
        justification: Pedagogical explanation for the override (auto-generated if omitted).
        tool_context: Optional ADK ToolContext.

    Returns:
        Dictionary with previous score, new score, delta, and updated scorecard metrics.
    """
    # Flexible argument parsing: check if first positional arg is actually a question_id
    if scorecard_id and re.match(r"^Q[1-5]$", scorecard_id.strip(), re.IGNORECASE):
        actual_qid = scorecard_id.upper().strip()
        scorecard_id = kwargs.get("scorecard_id")

        if isinstance(question_id, (int, float)):
            actual_score = float(question_id)
            actual_dim = "total"
            actual_justification = str(dimension) if dimension and dimension not in ("correctness", "writing_quality", "total") else justification
        elif question_id in ("correctness", "writing_quality", "total"):
            actual_dim = question_id
            actual_score = float(dimension) if isinstance(dimension, (int, float)) else new_score
            actual_justification = justification or (str(dimension) if not isinstance(dimension, (int, float)) else None)
        else:
            actual_dim = str(dimension) if dimension in ("correctness", "writing_quality", "total") else "total"
            actual_score = new_score
            actual_justification = justification

        qid = actual_qid
        dim = actual_dim
        resolved_score = actual_score
        resolved_justification = actual_justification
    else:
        qid = (question_id or kwargs.get("qid") or kwargs.get("section_id") or "").upper().strip()
        dim = (dimension or kwargs.get("dim") or "total").lower().strip()
        resolved_score = new_score if new_score is not None else (float(kwargs["score"]) if "score" in kwargs else (float(kwargs["points"]) if "points" in kwargs else None))
        resolved_justification = justification or kwargs.get("rationale") or kwargs.get("reason")

    if not qid:
        return ToolRecoveryResponse(
            error_code="MISSING_QUESTION_ID",
            message="Please specify which question number (Q1 to Q5) you would like to override.",
            recovery_guidance="Specify a question like 'Q1', 'Q2', 'Q3', 'Q4', or 'Q5'.",
            valid_options=["Q1", "Q2", "Q3", "Q4", "Q5"],
        ).model_dump()

    if resolved_score is None:
        return ToolRecoveryResponse(
            error_code="MISSING_SCORE",
            message=f"Please specify the numeric score to assign to {qid}.",
            recovery_guidance="Provide the target numerical score, e.g. 5.0.",
        ).model_dump()

    if dim not in ("correctness", "writing_quality", "total"):
        dim = "total"

    # Auto-generate justification if not provided or too short
    if not resolved_justification or len(resolved_justification.strip()) < 3:
        resolved_justification = f"Teacher direct score override applied to {qid} ({dim}: {resolved_score:.1f})."

    log_intent(
        "RegradeTool",
        "DIRECT_OVERRIDE",
        f"{scorecard_id or 'auto'}:{qid}",
        {"new_score": resolved_score, "dimension": dim, "justification": resolved_justification},
    )

    # Resolve scorecard from session state, token, ID, or database
    scorecard = None
    if tool_context and hasattr(tool_context, "state"):
        if "scorecard" in tool_context.state:
            raw_sc = tool_context.state["scorecard"]
            if isinstance(raw_sc, dict):
                scorecard = StudentScoreCard.model_validate(raw_sc)
            elif isinstance(raw_sc, StudentScoreCard):
                scorecard = raw_sc
        if not scorecard and "student_token" in tool_context.state:
            token = tool_context.state["student_token"]
            scorecard = gradebook_db.get_scorecard_by_token(token)

    if not scorecard and scorecard_id:
        scorecard = gradebook_db.get_scorecard(scorecard_id)

    if not scorecard:
        scorecard = gradebook_db.get_latest_scorecard()

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
            message=f"Question '{qid}' not found in scorecard.",
            recovery_guidance=f"Choose from: {valid_qids}",
            valid_options=valid_qids,
        ).model_dump()

    previous_total = target_item.total_awarded

    if dim == "correctness":
        if resolved_score > target_item.correctness.max_score or resolved_score < 0:
            return ToolRecoveryResponse(
                error_code="SCORE_OUT_OF_BOUNDS",
                message=f"Score {resolved_score} out of bounds for correctness (0.0 to {target_item.correctness.max_score}).",
                recovery_guidance=f"Provide a value between 0.0 and {target_item.correctness.max_score}.",
            ).model_dump()
        target_item.correctness.score = resolved_score
        target_item.total_awarded = target_item.correctness.score + target_item.writing_quality.score
    elif dim == "writing_quality":
        if resolved_score > target_item.writing_quality.max_score or resolved_score < 0:
            return ToolRecoveryResponse(
                error_code="SCORE_OUT_OF_BOUNDS",
                message=f"Score {resolved_score} out of bounds for writing quality (0.0 to {target_item.writing_quality.max_score}).",
                recovery_guidance=f"Provide a value between 0.0 and {target_item.writing_quality.max_score}.",
            ).model_dump()
        target_item.writing_quality.score = resolved_score
        target_item.total_awarded = target_item.correctness.score + target_item.writing_quality.score
    else:
        if resolved_score > target_item.total_possible or resolved_score < 0:
            return ToolRecoveryResponse(
                error_code="SCORE_OUT_OF_BOUNDS",
                message=f"Score {resolved_score} out of bounds for total (0.0 to {target_item.total_possible}).",
                recovery_guidance=f"Provide a value between 0.0 and {target_item.total_possible}.",
            ).model_dump()
        target_item.total_awarded = resolved_score
        half = resolved_score / 2.0
        target_item.correctness.score = min(half, target_item.correctness.max_score)
        target_item.writing_quality.score = target_item.total_awarded - target_item.correctness.score

    target_item.percentage = (target_item.total_awarded / target_item.total_possible) * 100.0
    target_item.teacher_override_applied = True
    target_item.override_note = f"Teacher Direct Override: {resolved_justification}"

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
        rationale=resolved_justification,
    )
    scorecard.audit_history.append(audit_entry)
    gradebook_db.save_scorecard(scorecard)
    gradebook_db.append_audit_entry(scorecard.scorecard_id, audit_entry)
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


# Backwards compatibility alias
override_section_score_with_audit = apply_teacher_score_override

__all__ = [
    "regrade_assessment_section",
    "apply_teacher_score_override",
    "override_section_score_with_audit",
]

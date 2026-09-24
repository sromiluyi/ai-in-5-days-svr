"""Human-in-the-Loop (HITL) Review Tools.

Implements native ADK confirmation hooks for high-stakes grading actions,
pausing execution when a student's score is <60%, >90%, or has a >30% discrepancy,
and requiring explicit human teacher confirmation before finalization.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from google.adk.tools import ToolContext

from app.config import config
from app.memory.session_service import scorecard_db
from app.models import AuditLogEntry, StudentScoreCard, ToolRecoveryResponse
from app.observability.logger import log_intent, log_outcome


def check_hitl_triggers(
    overall_percentage: float,
    correctness_percentage: float,
    quality_percentage: float,
) -> List[str]:
    """Check whether a score requires Human-in-the-Loop teacher review.

    Args:
        overall_percentage: Total exam score percentage.
        correctness_percentage: Factual correctness percentage.
        quality_percentage: Writing quality percentage.

    Returns:
        List of triggered reason codes (empty if auto-approved).
    """
    reasons: List[str] = []
    if overall_percentage < config.thresholds.low_score_threshold:
        reasons.append(
            f"SCORE_BELOW_PASSING_THRESHOLD: Overall score ({overall_percentage:.1f}%) is below {config.thresholds.low_score_threshold}% passing threshold (intervention required)."
        )

    if overall_percentage > config.thresholds.high_score_threshold:
        reasons.append(
            f"HIGH_HONORS_VERIFICATION: Overall score ({overall_percentage:.1f}%) exceeds {config.thresholds.high_score_threshold}% (honors/outlier validation required)."
        )

    discrepancy = abs(correctness_percentage - quality_percentage)
    if discrepancy > config.thresholds.discrepancy_threshold:
        reasons.append(
            f"SCORE_DIMENSION_DISCREPANCY: Discrepancy of {discrepancy:.1f}% between factual correctness ({correctness_percentage:.1f}%) and writing quality ({quality_percentage:.1f}%) exceeds {config.thresholds.discrepancy_threshold}% threshold."
        )

    return reasons


def finalize_student_grade_record(
    scorecard_dict: Dict[str, Any],
    tool_context: ToolContext,
) -> Dict[str, Any]:
    """Finalize and persist a student scorecard, pausing for teacher review if flagged.

    Follows ADK's native Human-in-the-Loop confirmation pattern using tool_context.request_confirmation.

    Args:
        scorecard_dict: Dictionary representation of StudentScoreCard.
        tool_context: ADK ToolContext providing session state and confirmation hooks.

    Returns:
        Dictionary with status of finalization or approval request hint.
    """
    try:
        scorecard = StudentScoreCard.model_validate(scorecard_dict)
    except Exception as exc:
        return ToolRecoveryResponse(
            error_code="INVALID_SCORECARD_SCHEMA",
            message=f"Failed to validate scorecard against schema: {exc}",
            recovery_guidance="Ensure all required fields (scorecard_id, student_token, total_score, question_scores) are provided.",
        ).model_dump()

    token = scorecard.student_token
    score = scorecard.overall_percentage
    reasons = check_hitl_triggers(
        score, scorecard.correctness_percentage, scorecard.quality_percentage
    )

    log_intent(
        "HITLTool",
        "FINALIZE_GRADE_RECORD",
        token,
        {"overall_percentage": score, "flag_count": len(reasons)},
        student_token=token,
    )

    # -------------------------------------------------------------------------
    # SCENARIO 1: Normal score without discrepancy - Auto-approve
    # -------------------------------------------------------------------------
    if not reasons:
        scorecard.hitL_review.is_flagged = False
        scorecard.hitL_review.teacher_decision = "APPROVED"
        scorecard.hitL_review.teacher_notes = "Auto-approved by assessment pipeline."

        scorecard_db.save_scorecard(scorecard)
        scorecard_db.append_audit_entry(
            scorecard.scorecard_id,
            AuditLogEntry(
                action="INITIAL_ASSESSMENT",
                target_section="OVERALL",
                new_score=score,
                actor="system",
                rationale="Automated grading within standard thresholds.",
            ),
        )

        log_outcome(
            "HITLTool",
            "FINALIZE_GRADE_RECORD",
            "AUTO_APPROVED",
            f"Scorecard auto-approved for token {token} ({score:.1f}%)",
            student_token=token,
        )
        return {
            "status": "auto_approved",
            "scorecard_id": scorecard.scorecard_id,
            "student_token": token,
            "overall_percentage": score,
            "letter_grade": scorecard.letter_grade,
            "message": f"Scorecard auto-approved with score {score:.1f}% ({scorecard.letter_grade}).",
        }

    # -------------------------------------------------------------------------
    # SCENARIO 2: Flagged for teacher review - PAUSE here on first call
    # -------------------------------------------------------------------------
    scorecard.hitL_review.is_flagged = True
    scorecard.hitL_review.flag_reasons = reasons

    if not tool_context.tool_confirmation:
        hint_text = (
            f"⚠️ Grade Flagged for Teacher Review: {token} scored {score:.1f}% ({scorecard.letter_grade}). "
            f"Reasons: {'; '.join(reasons)}. Do you confirm this grade?"
        )
        tool_context.request_confirmation(
            hint=hint_text,
            payload={
                "scorecard_id": scorecard.scorecard_id,
                "student_token": token,
                "overall_percentage": score,
                "reasons": reasons,
            },
        )
        log_outcome(
            "HITLTool",
            "FINALIZE_GRADE_RECORD",
            "PAUSED_FOR_HITL",
            f"Execution paused waiting for teacher approval on token {token}",
            student_token=token,
        )
        return {
            "status": "pending_teacher_review",
            "scorecard_id": scorecard.scorecard_id,
            "student_token": token,
            "flag_reasons": reasons,
            "message": hint_text,
        }

    # -------------------------------------------------------------------------
    # SCENARIO 3: Tool is resuming after human response - RESUME here
    # -------------------------------------------------------------------------
    if tool_context.tool_confirmation.confirmed:
        scorecard.hitL_review.teacher_decision = "APPROVED"
        scorecard.hitL_review.teacher_notes = "Confirmed by teacher via HITL hook."
        scorecard_db.save_scorecard(scorecard)
        scorecard_db.append_audit_entry(
            scorecard.scorecard_id,
            AuditLogEntry(
                action="HITL_APPROVAL",
                target_section="OVERALL",
                new_score=score,
                actor="teacher",
                rationale="Teacher explicitly approved flagged submission.",
            ),
        )

        log_outcome(
            "HITLTool",
            "FINALIZE_GRADE_RECORD",
            "TEACHER_APPROVED",
            f"Teacher confirmed flagged grade for token {token}",
            student_token=token,
        )
        return {
            "status": "teacher_approved",
            "scorecard_id": scorecard.scorecard_id,
            "student_token": token,
            "overall_percentage": score,
            "letter_grade": scorecard.letter_grade,
            "message": f"Teacher confirmed grade: {score:.1f}% ({scorecard.letter_grade}).",
        }
    else:
        scorecard.hitL_review.teacher_decision = "REVISED"
        scorecard.hitL_review.teacher_notes = "Teacher requested revisions or adjustments."
        scorecard_db.save_scorecard(scorecard)

        log_outcome(
            "HITLTool",
            "FINALIZE_GRADE_RECORD",
            "TEACHER_REJECTED",
            f"Teacher rejected score for token {token}, awaiting modifications",
            student_token=token,
        )
        return {
            "status": "teacher_rejected",
            "scorecard_id": scorecard.scorecard_id,
            "student_token": token,
            "message": "Grade rejected by teacher. Awaiting regrading or manual score override.",
        }

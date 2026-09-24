"""Scorecard Synthesizer and Evaluation Coordinator.

Aggregates factual correctness and writing quality evaluations across all exam questions,
calculates overall percentages, assesses Human-in-the-Loop thresholds, and creates
the persistent StudentScoreCard.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from google.adk.tools import ToolContext

from app.memory.session_service import scorecard_db
from app.models import (
    CorrectnessEvaluation,
    HITLReviewFlag,
    QualityEvaluation,
    QuestionScoreItem,
    StudentScoreCard,
)
from app.observability.logger import log_intent, log_outcome
from app.tools.hitl_tools import check_hitl_triggers, finalize_student_grade_record


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


def synthesize_exam_scorecard(
    student_token: str,
    evaluations: List[Dict[str, Any]],
    exam_title: str = "The Hunger Games Unit Assessment (Book 1)",
    tool_context: Optional[ToolContext] = None,
) -> StudentScoreCard:
    """Synthesize evaluations into a complete StudentScoreCard and evaluate HITL review status.

    Args:
        student_token: Anonymized pseudonym token (e.g., 'STUDENT_ANON_7A1B').
        evaluations: List of per-question evaluation dictionaries.
        exam_title: Title of the exam.
        tool_context: Optional ADK ToolContext.

    Returns:
        Structured StudentScoreCard instance.
    """
    log_intent(
        "ScorecardSynthesizer",
        "SYNTHESIZE_SCORECARD",
        student_token,
        {"num_questions": len(evaluations)},
        student_token=student_token,
    )

    question_scores: List[QuestionScoreItem] = []
    total_awarded = 0.0
    total_possible = 0.0

    total_correctness_awarded = 0.0
    total_correctness_possible = 0.0
    total_quality_awarded = 0.0
    total_quality_possible = 0.0

    for ev in evaluations:
        qid = ev.get("question_id", "Q_UNKNOWN")
        prompt = ev.get("question_prompt", "")
        qtype = ev.get("question_type", "short_answer")

        corr_data = ev.get("correctness")
        qual_data = ev.get("writing_quality")

        corr = CorrectnessEvaluation.model_validate(corr_data)
        qual = QualityEvaluation.model_validate(qual_data)

        awarded = corr.score + qual.score
        possible = corr.max_score + qual.max_score
        pct = (awarded / possible) * 100.0 if possible > 0 else 0.0

        item = QuestionScoreItem(
            question_id=qid,
            question_prompt=prompt,
            question_type=qtype,
            correctness=corr,
            writing_quality=qual,
            total_awarded=awarded,
            total_possible=possible,
            percentage=pct,
        )
        question_scores.append(item)

        total_awarded += awarded
        total_possible += possible
        total_correctness_awarded += corr.score
        total_correctness_possible += corr.max_score
        total_quality_awarded += qual.score
        total_quality_possible += qual.max_score

    calc_possible = total_possible if total_possible > 0 else 100.0
    overall_pct = (total_awarded / calc_possible) * 100.0 if calc_possible > 0 else 0.0
    corr_pct = (
        (total_correctness_awarded / total_correctness_possible) * 100.0
        if total_correctness_possible > 0
        else 0.0
    )
    qual_pct = (
        (total_quality_awarded / total_quality_possible) * 100.0
        if total_quality_possible > 0
        else 0.0
    )
    discrepancy = abs(corr_pct - qual_pct)
    letter = _calculate_letter_grade(overall_pct)

    # Check Human-in-the-Loop review triggers (<60%, >90%, discrepancy >30%)
    flag_reasons = check_hitl_triggers(overall_pct, corr_pct, qual_pct)
    is_flagged = len(flag_reasons) > 0

    hitl_flag = HITLReviewFlag(
        is_flagged=is_flagged,
        flag_reasons=flag_reasons,
        teacher_decision="PENDING" if is_flagged else "APPROVED",
        teacher_notes="Flagged for human teacher verification." if is_flagged else "Auto-approved.",
    )

    feedback_summary = (
        f"Student demonstrated strong engagement with The Hunger Games. "
        f"Factual recall reached {corr_pct:.1f}%, while analytical writing scored {qual_pct:.1f}%."
    )
    growth_recommendation = (
        "Focus on embedding specific textual citations into analytical claims to strengthen essay rigor."
    )

    scorecard_id = f"SC-{student_token}-{uuid.uuid4().hex[:6]}"
    scorecard = StudentScoreCard(
        scorecard_id=scorecard_id,
        student_token=student_token,
        exam_title=exam_title,
        total_score=total_awarded,
        max_possible_score=calc_possible,
        overall_percentage=overall_pct,
        letter_grade=letter,
        correctness_percentage=corr_pct,
        quality_percentage=qual_pct,
        score_discrepancy=discrepancy,
        question_scores=question_scores,
        overall_pedagogical_feedback=feedback_summary,
        growth_recommendation=growth_recommendation,
        hitL_review=hitl_flag,
    )

    # Persist in database
    scorecard_db.save_scorecard(scorecard)

    log_outcome(
        "ScorecardSynthesizer",
        "SYNTHESIZE_SCORECARD",
        "SUCCESS",
        f"Synthesized scorecard {scorecard_id} for token {student_token} ({overall_pct:.1f}% - Grade {letter}, HITL: {is_flagged})",
        student_token=student_token,
    )

    return scorecard

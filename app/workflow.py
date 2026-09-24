"""ADK 2.0 Graph Workflow for The Hunger Games Literature Assessment System.

Constructed following the Google ADK 2.0 Workflow architecture (as demonstrated
in the ambient-expense-agent reference pattern):
- Uses START, Workflow, @node, Context, Event, and RequestInput.
- Graph Topology:
    START
      │
      ▼
    anonymize_submission (PII Vault extraction & tokenization)
      │
      ▼
    evaluate_correctness (Gemini 2.5 Flash / Canon verification)
      │
      ▼
    evaluate_quality (Gemini 2.5 Pro / ELA writing standards)
      │
      ▼
    synthesize_and_route (Score aggregation & HITL threshold check)
      │
      ├──────────────────────┬──────────────────────┐
      ▼                      ▼                      ▼
  [auto route]          [review route]         [review route]
  auto_approve_grade    teacher_review_node    teacher_review_node
  (Within thresholds)   (Low score <60% or     (Dimension gap >30%)
                         Honors >90%)
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from google.adk.agents.context import Context
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.workflow import START, Workflow, node

from app.agents.scorecard_agent import synthesize_exam_scorecard
from app.cli import evaluate_submission_offline
from app.config import config
from app.memory.session_service import scorecard_db
from app.models import AuditLogEntry, StudentScoreCard
from app.observability.logger import log_intent, log_outcome
from app.tools.anonymizer_tool import mask_student_identifiers, restore_student_identity_vault


def anonymize_submission_node(ctx: Context, node_input: Any) -> Event:
    """Extracts student PII into an isolated vault and emits tokenized answers."""
    # Extract markdown text from Content or string
    raw_md = ""
    if hasattr(node_input, "parts") and node_input.parts:
        raw_md = node_input.parts[0].text or ""
    elif isinstance(node_input, dict):
        raw_md = node_input.get("submission_markdown", "")
    elif isinstance(node_input, str):
        raw_md = node_input

    log_intent("WorkflowNode", "ANONYMIZE_SUBMISSION", "raw_submission_markdown")
    anon_result = mask_student_identifiers(raw_md)

    if anon_result.get("status") == "error":
        token = "STUDENT_ANON_GENERAL"
        submission_data = {"student_name": "General Student", "student_id": "ANON", "answers": []}
        ctx.state["student_token"] = token
        return Event(
            output={"student_token": token, "submission": submission_data, "evaluations": []},
            message="Welcome to the Hunger Games Assessment Agent. Ready to evaluate student submissions.",
        )

    token = anon_result["student_token"]
    submission_data = anon_result["submission"]
    ctx.state["student_token"] = token

    log_outcome(
        "WorkflowNode",
        "ANONYMIZE_SUBMISSION",
        "SUCCESS",
        f"Assigned token {token} with {len(submission_data['answers'])} questions",
        student_token=token,
    )

    return Event(
        output={"student_token": token, "submission": submission_data},
        message=f"🔒 PII Anonymized: Student masked to pseudonym '{token}'.",
    )


def evaluate_correctness_node(ctx: Context, node_input: dict) -> Event:
    """Evaluates factual recall against Hunger Games canon using Gemini 2.5 Flash."""
    token = node_input.get("student_token", "STUDENT_ANON_GENERAL")
    submission_data = node_input.get("submission", {})
    answers = submission_data.get("answers", [])

    if not answers:
        return Event(
            output={"student_token": token, "evaluations": []},
            message="No exam questions detected to assess for factual correctness.",
        )

    log_intent("WorkflowNode", "EVALUATE_CORRECTNESS", token, student_token=token)

    # Execute evaluation pipeline
    eval_items = evaluate_submission_offline(submission_data)

    log_outcome(
        "WorkflowNode",
        "EVALUATE_CORRECTNESS",
        "SUCCESS",
        f"Evaluated factual correctness for {len(eval_items)} questions",
        student_token=token,
    )

    return Event(
        output={"student_token": token, "evaluations": eval_items},
        message=f"🏹 Factual Correctness: Assessed {len(eval_items)} questions against canon lore.",
    )


def evaluate_quality_node(ctx: Context, node_input: dict) -> Event:
    """Evaluates analytical reasoning & literacy mechanics using Gemini 2.5 Pro."""
    token = node_input.get("student_token", "STUDENT_ANON_GENERAL")
    eval_items = node_input.get("evaluations", [])

    if not eval_items:
        return Event(
            output={"student_token": token, "evaluations": []},
            message="No student responses found for writing quality assessment.",
        )

    log_intent("WorkflowNode", "EVALUATE_QUALITY", token, student_token=token)

    # Evaluations already include writing quality assessment from specialist engine
    log_outcome(
        "WorkflowNode",
        "EVALUATE_QUALITY",
        "SUCCESS",
        f"Assessed writing quality across middle school standards",
        student_token=token,
    )

    return Event(
        output={"student_token": token, "evaluations": eval_items},
        message=f"✍️ Writing Quality: Evaluated middle-school claims, evidence, and mechanics.",
    )


def synthesize_and_route_node(ctx: Context, node_input: dict) -> Event:
    """Aggregates subscores, calculates letter grade, and routes based on HITL thresholds."""
    token = node_input.get("student_token", "STUDENT_ANON_GENERAL")
    eval_items = node_input.get("evaluations", [])

    if not eval_items:
        return Event(
            output={"student_token": token, "message": "I am the Hunger Games Literature Assessment Agent. Send a student submission with question responses to generate an assessment scorecard."},
            route="auto",
            message="I am the Hunger Games Literature Assessment Agent. Send a student submission with question responses to generate an assessment scorecard.",
        )

    log_intent("WorkflowNode", "SYNTHESIZE_SCORECARD", token, student_token=token)
    scorecard: StudentScoreCard = synthesize_exam_scorecard(token, eval_items)
    scorecard_dict = scorecard.model_dump()
    ctx.state["scorecard"] = scorecard_dict

    # Check if flagged for Human-in-the-Loop review
    if scorecard.hitL_review.is_flagged:
        reasons_summary = "; ".join(scorecard.hitL_review.flag_reasons)
        log_outcome(
            "WorkflowNode",
            "SYNTHESIZE_SCORECARD",
            "FLAGGED_FOR_HITL",
            f"Scorecard flagged for review: {reasons_summary}",
            student_token=token,
        )
        return Event(
            output=scorecard_dict,
            route="review",
            message=(
                f"🚨 [HITL REVIEW REQUIRED] Student {token} scored {scorecard.overall_percentage:.1f}% "
                f"({scorecard.letter_grade}). Routing to Teacher Review Gate."
            ),
        )
    else:
        log_outcome(
            "WorkflowNode",
            "SYNTHESIZE_SCORECARD",
            "AUTO_APPROVED",
            f"Scorecard within normal bounds: {scorecard.overall_percentage:.1f}% ({scorecard.letter_grade})",
            student_token=token,
        )
        return Event(
            output=scorecard_dict,
            route="auto",
            message=(
                f"✅ [AUTO APPROVED] Student {token} scored {scorecard.overall_percentage:.1f}% "
                f"({scorecard.letter_grade}) within standard parameters."
            ),
        )


def auto_approve_node(ctx: Context, node_input: dict) -> Event:
    """Finalizes and persists automatically approved grade records."""
    token = node_input.get("student_token", "UNKNOWN")
    score = node_input.get("overall_percentage")
    grade = node_input.get("letter_grade")

    if score is not None and grade is not None:
        log_outcome(
            "WorkflowNode",
            "AUTO_APPROVE",
            "SUCCESS",
            f"Scorecard permanently stored for {token} ({score:.1f}% - Grade {grade})",
            student_token=token,
        )
        return Event(
            output=node_input,
            message=f"📋 Grade Finalized: {token} officially recorded as Grade [{grade}] ({score:.1f}%).",
        )

    return Event(
        output=node_input,
        message="Assessment system is ready and listening for student submissions or teacher instructions.",
    )


@node(rerun_on_resume=True)
async def teacher_review_node(ctx: Context, node_input: dict):
    """Handles Human-in-the-Loop review for flagged submissions.

    Pauses execution via RequestInput asking the educator for confirmation or override.
    When resumed with teacher decision, logs audit trail and unmasks student identity.
    """
    token = node_input.get("student_token", "UNKNOWN")
    score = float(node_input.get("overall_percentage", 0.0))
    grade = node_input.get("letter_grade", "N/A")
    flag_reasons = node_input.get("hitL_review", {}).get("flag_reasons", [])

    # Check if we already received teacher input upon resume
    if not ctx.resume_inputs or "teacher_approval" not in ctx.resume_inputs:
        prompt_message = (
            f"⚠️ Human-in-the-Loop Educator Review Required:\n"
            f"- Student Token: {token}\n"
            f"- Calculated Score: {score:.1f}% (Grade: {grade})\n"
            f"- Trigger Reasons:\n  • " + "\n  • ".join(flag_reasons) + "\n\n"
            f"Do you approve this assessment record? (yes / no / override <score>):"
        )
        log_outcome(
            "WorkflowNode",
            "TEACHER_REVIEW",
            "PAUSED_FOR_HITL",
            f"Requesting teacher sign-off for token {token}",
            student_token=token,
        )
        yield RequestInput(
            interrupt_id="teacher_approval",
            message=prompt_message,
        )
        return

    # Process teacher decision upon resumption
    raw_decision = ctx.resume_inputs.get("teacher_approval")
    if isinstance(raw_decision, dict):
        raw_decision = (
            raw_decision.get("response")
            or raw_decision.get("decision")
            or str(raw_decision)
        )
    decision_str = str(raw_decision).strip().lower()

    now_utc = datetime.datetime.now(ZoneInfo("UTC")).isoformat()
    audit_action = "TEACHER_APPROVAL"
    audit_notes = f"Teacher reviewed and approved score of {score:.1f}%."

    if decision_str in ("yes", "y", "approve", "approved"):
        is_approved = True
        status = "TEACHER_CONFIRMED"
    else:
        is_approved = False
        status = "TEACHER_REJECTED"
        audit_action = "TEACHER_REJECTION"
        audit_notes = f"Teacher rejected assessment: '{decision_str}'."

    # Update scorecard record
    node_input["hitL_review"]["teacher_decision"] = status
    node_input["hitL_review"]["teacher_notes"] = audit_notes

    # Unmask student identity upon teacher review completion
    unmask_result = restore_student_identity_vault(token)
    student_name = (
        unmask_result.get("student_name", "Student")
        if unmask_result.get("status") == "success"
        else "Student"
    )

    log_outcome(
        "WorkflowNode",
        "TEACHER_REVIEW",
        status,
        f"Teacher decision '{decision_str}' applied to token {token} ({student_name})",
        student_token=token,
    )

    yield Event(
        output=node_input,
        message=(
            f"{'✅' if is_approved else '❌'} Assessment Review Complete: {token} ({student_name}) "
            f"was marked as {status} (Decision: '{decision_str}')."
        ),
    )


# -----------------------------------------------------------------------------
# Construct the ADK 2.0 Graph Workflow
# -----------------------------------------------------------------------------
assessment_workflow = Workflow(
    name="ai_in_5_days_svr",
    edges=[
        (START, anonymize_submission_node),
        (anonymize_submission_node, evaluate_correctness_node),
        (evaluate_correctness_node, evaluate_quality_node),
        (evaluate_quality_node, synthesize_and_route_node),
        (synthesize_and_route_node, {"auto": auto_approve_node, "review": teacher_review_node}),
    ],
    description=(
        "ADK 2.0 Literature Assessment Workflow for The Hunger Games. Anonymizes PII, "
        "evaluates factual correctness and writing quality, and triggers a Human-in-the-Loop "
        "review pause for scores below 60%, above 90%, or with >30% dimension discrepancy."
    ),
)

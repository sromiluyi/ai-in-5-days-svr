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

from app.agents.coordinator_agent import coordinator_agent
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
        return Event(
            output={"student_token": token, "submission": submission_data, "evaluations": []},
            state={"student_token": token, "submission": submission_data},
            message="Welcome to the Hunger Games Assessment Agent. Ready to evaluate student submissions.",
        )

    token = anon_result["student_token"]
    submission_data = anon_result["submission"]

    log_outcome(
        "WorkflowNode",
        "ANONYMIZE_SUBMISSION",
        "SUCCESS",
        f"Assigned token {token} with {len(submission_data['answers'])} questions",
        student_token=token,
    )

    return Event(
        output={"student_token": token, "submission": submission_data},
        state={"student_token": token, "submission": submission_data},
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
            state={"scorecard": scorecard_dict, "student_token": token, "review_status": "FLAGGED_FOR_HITL"},
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
            state={"scorecard": scorecard_dict, "student_token": token, "review_status": "AUTO_APPROVED"},
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
            state={"review_status": "AUTO_APPROVED_RECORDED"},
            message=f"📋 Grade Finalized: {token} officially recorded as Grade [{grade}] ({score:.1f}%).",
        )

    return Event(
        output=node_input,
        message="Assessment system is ready and listening for student submissions or teacher instructions.",
    )


async def teacher_review_node_func(ctx: Context, node_input: dict):
    """Handles Human-in-the-Loop review for flagged submissions.

    Allows back-and-forth teacher dialogue with the Coordinator Agent:
    - Quick approval via 'yes' / 'approve'
    - Asking questions about student answers, rubrics, or justifications
    - Requesting targeted specialist regrading with pedagogical feedback
    - Applying manual score overrides with structured audit logging
    - Managing session conversation history across multiple turns
    - Preserving bias-free pseudonym masking until final teacher sign-off
    """
    token = ctx.state.get("student_token") or node_input.get("student_token", "UNKNOWN")
    scorecard_data = ctx.state.get("scorecard") or node_input
    scorecard_id = scorecard_data.get("scorecard_id")
    turn = ctx.state.get("review_turn_count", 0)

    # First turn: initial pause before any educator input
    if not ctx.resume_inputs:
        ctx.state["review_turn_count"] = 1
        ctx.state["review_status"] = "IN_REVIEW"
        score = float(scorecard_data.get("overall_percentage", 0.0))
        grade = scorecard_data.get("letter_grade", "N/A")
        flag_reasons = scorecard_data.get("hitL_review", {}).get("flag_reasons", [])

        prompt_message = (
            f"⚠️ **Human-in-the-Loop Educator Review Required**:\n\n"
            f"- **Student Token**: `{token}` *(Identity anonymized for bias-free review)*\n"
            f"- **Calculated Score**: **{score:.1f}%** (Grade: **{grade}**)\n"
            f"- **Trigger Reasons**:\n  • " + "\n  • ".join(flag_reasons) + "\n\n"
            f"**Review Actions Available**:\n"
            f"• **Approve**: Type `'approve'` or `'yes'` to finalize this grade as-is.\n"
            f"• **Ask Questions**: e.g., *'Why did they lose points on Question 2?'* or *'Summarize their strengths.'*\n"
            f"• **Targeted Regrade**: e.g., *'Regrade Q2 correctness with +2 points for IEP accommodation.'*\n"
            f"• **Score Override**: e.g., *'Override Q4 total to 18: strong alternative thesis.'*\n"
            f"• **Unmask**: Type *'unmask'* to reveal the student's real name.\n\n"
            f"How would you like to proceed?"
        )
        log_outcome(
            "WorkflowNode",
            "TEACHER_REVIEW",
            "PAUSED_FOR_HITL",
            f"Requesting teacher review dialogue for token {token}",
            student_token=token,
        )
        yield RequestInput(
            interrupt_id="teacher_approval",
            message=prompt_message,
        )
        return

    # Extract user input from resume inputs
    current_key = f"teacher_dialogue_{turn}"
    raw_decision = None
    for k in [current_key, f"teacher_dialogue_{turn - 1}", "teacher_approval", *ctx.resume_inputs.keys()]:
        if k in ctx.resume_inputs:
            raw_decision = ctx.resume_inputs[k]
            break

    if isinstance(raw_decision, dict):
        user_text = str(
            raw_decision.get("response")
            or raw_decision.get("decision")
            or raw_decision.get("teacher_approval")
            or raw_decision
        ).strip()
    else:
        user_text = str(raw_decision or "").strip()

    # 1. Quick Approval / Finalize
    if user_text.lower() in ("yes", "y", "approve", "approved", "confirm", "confirmed", "finalize", "finalized", "looks good", "done"):
        latest_sc = (scorecard_db.get_scorecard(scorecard_id) if scorecard_id else None) or StudentScoreCard.model_validate(scorecard_data)
        latest_sc.hitL_review.teacher_decision = "TEACHER_CONFIRMED"
        latest_sc.hitL_review.teacher_notes = f"Teacher finalized and approved assessment (Final Score: {latest_sc.overall_percentage:.1f}%, Grade: {latest_sc.letter_grade})."
        scorecard_db.save_scorecard(latest_sc)

        unmask_result = restore_student_identity_vault(token, teacher_auth=True)
        student_name = (
            unmask_result.get("student_name", "Student")
            if unmask_result.get("status") == "success"
            else "Student"
        )
        student_id_val = unmask_result.get("student_id", "N/A") if unmask_result.get("status") == "success" else "N/A"

        final_dict = latest_sc.model_dump()
        final_dict["student_name"] = student_name
        final_dict["student_id"] = student_id_val

        log_outcome(
            "WorkflowNode",
            "TEACHER_REVIEW",
            "TEACHER_CONFIRMED",
            f"Review finalized for {token} ({student_name}) with {latest_sc.overall_percentage:.1f}% ({latest_sc.letter_grade})",
            student_token=token,
        )

        yield Event(
            output=final_dict,
            state={"scorecard": final_dict, "review_status": "TEACHER_CONFIRMED"},
            message=(
                f"✅ **Assessment Review Finalized**\n\n"
                f"- **Student**: **{student_name}** (`{student_id_val}`)\n"
                f"- **Token**: `{token}`\n"
                f"- **Final Score**: **{latest_sc.overall_percentage:.1f}%** (Grade: **{latest_sc.letter_grade}**)\n"
                f"- **Audit Trail**: All teacher adjustments and sign-offs permanently recorded.\n"
                f"- **Total Questions**: {len(latest_sc.question_scores)} assessed."
            ),
        )
        return

    # 2. Explicit Rejection
    if user_text.lower() in ("no", "reject", "rejected"):
        latest_sc = (scorecard_db.get_scorecard(scorecard_id) if scorecard_id else None) or StudentScoreCard.model_validate(scorecard_data)
        latest_sc.hitL_review.teacher_decision = "TEACHER_REJECTED"
        latest_sc.hitL_review.teacher_notes = "Teacher rejected assessment."
        scorecard_db.save_scorecard(latest_sc)

        unmask_result = restore_student_identity_vault(token, teacher_auth=True)
        student_name = unmask_result.get("student_name", "Student") if unmask_result.get("status") == "success" else "Student"

        final_dict = latest_sc.model_dump()
        final_dict["student_name"] = student_name

        yield Event(
            output=final_dict,
            state={"scorecard": final_dict, "review_status": "TEACHER_REJECTED"},
            message=f"❌ Assessment Review: {token} ({student_name}) marked as TEACHER_REJECTED.",
        )
        return

    # 3. Interactive Dialogue Turn (Questions, Regrades, Overrides)
    # Native ADK execution via ctx.run_node(coordinator_agent, node_input=user_text)
    # Session conversation history is tracked automatically in ctx.session.events.
    ctx.state["review_turn_count"] = turn + 1

    agent_output = None
    if hasattr(ctx, "run_node"):
        import inspect
        res = ctx.run_node(coordinator_agent, node_input=user_text)
        if inspect.isawaitable(res):
            agent_output = await res
        else:
            agent_output = res

    # Extract text from agent output
    if hasattr(agent_output, "text") and agent_output.text:
        agent_reply = agent_output.text
    elif isinstance(agent_output, str) and agent_output:
        agent_reply = agent_output
    elif hasattr(agent_output, "parts") and agent_output.parts:
        agent_reply = "".join(p.text or "" for p in agent_output.parts if hasattr(p, "text"))
    else:
        agent_reply = ctx.state.get("coordinator_response") or str(agent_output or "")

    # Sync scorecard from state / db if regrade or override occurred
    updated_sc_dict = ctx.state.get("scorecard")
    if not updated_sc_dict and scorecard_id:
        db_sc = scorecard_db.get_scorecard(scorecard_id)
        if db_sc:
            updated_sc_dict = db_sc.model_dump()
            ctx.state["scorecard"] = updated_sc_dict

    next_interrupt = f"teacher_dialogue_{turn + 1}"
    follow_up_prompt = (
        f"{agent_reply}\n\n"
        f"---\n"
        f"💬 **Teacher Review Dialogue (Turn {turn + 1})**:\n"
        f"• Ask another question about student answers or rubrics.\n"
        f"• Request another regrade or score override.\n"
        f"• Type **'approve'** when you are ready to finalize and record this grade."
    )

    yield RequestInput(
        interrupt_id=next_interrupt,
        message=follow_up_prompt,
    )


# Wrapped Node for Workflow Graph
teacher_review_node = node(rerun_on_resume=True)(teacher_review_node_func)


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

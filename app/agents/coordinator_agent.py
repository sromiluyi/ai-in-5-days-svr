"""Teacher Dialogue Coordinator Agent.

Provides an interactive conversational interface allowing teachers to discuss
student scorecards, review HITL flagged assessments, command targeted agent
regrades, apply manual score overrides with audit logging, and reveal identities.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional
from google.adk.agents import LlmAgent
from google.adk.models.google_llm import Gemini

from app.config import config
from app.constitution import COORDINATOR_INSTRUCTIONS, PEDAGOGICAL_CONSTITUTION
from app.mcp_server.canon_server import get_canon_mcp_toolset
from app.memory.session_service import scorecard_db
from app.models import StudentScoreCard
from app.observability.logger import log_intent, log_outcome
from app.tools.anonymizer_tool import restore_student_identity_vault
from app.tools.hitl_tools import finalize_student_grade_record
from app.tools.regrade_tools import apply_teacher_score_override, regrade_assessment_section


def create_coordinator_agent(model_override: Optional[str] = None) -> LlmAgent:
    """Create the Teacher Dialogue Coordinator Agent.

    Uses Gemini 2.5 Pro for strategic model routing (conversational reasoning,
    pedagogical justification, and teacher coordination). Uses ADK native
    dynamic state injection for {student_token} and {scorecard}.

    Args:
        model_override: Optional model override.

    Returns:
        Configured ADK LlmAgent.
    """
    model_name = model_override or config.pro_model
    canon_mcp_toolset = get_canon_mcp_toolset()

    full_instruction = f"""
{PEDAGOGICAL_CONSTITUTION}

---

{COORDINATOR_INSTRUCTIONS}

Current Session State:
- Student Token: {{student_token}}
- Scorecard Context: {{scorecard}}

Instructions for Educator Dialogue:
1. You converse with the classroom teacher about this student's assessment.
2. Answer their questions about specific exam questions, rubric criteria, or Suzanne Collins' The Hunger Games lore.
3. If the teacher asks to regrade a question, invoke `regrade_assessment_section`.
4. If the teacher specifies an override score, invoke `apply_teacher_score_override`.
5. If the teacher asks to unmask or reveal the student's name, invoke `restore_student_identity_vault`.
6. Provide clear, supportive pedagogical explanations for all evaluations and score changes.
"""

    return LlmAgent(
        name="teacher_dialogue_coordinator",
        description="Coordinates back-and-forth teacher conversation, targeted regrades, score overrides, and HITL approvals.",
        model=Gemini(model=model_name),
        instruction=full_instruction,
        tools=[
            regrade_assessment_section,
            apply_teacher_score_override,
            finalize_student_grade_record,
            restore_student_identity_vault,
            canon_mcp_toolset,
        ],
        output_key="coordinator_response",
    )


async def handle_coordinator_dialogue_turn(
    scorecard_id: Optional[str],
    student_token: str,
    teacher_input: str,
    chat_history: List[Dict[str, str]],
    fallback_scorecard: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Process an interactive teacher inquiry, regrade, or override turn.

    Maintains session history, supports multi-turn dialogue, executes targeted
    regrades and overrides, answers rubric/student performance questions, and
    preserves bias-free masking until final confirmation.

    Args:
        scorecard_id: ID of the scorecard being reviewed.
        student_token: Pseudonym token of the student.
        teacher_input: The raw message or command from the educator.
        chat_history: Accumulated conversation turns for multi-turn context.
        fallback_scorecard: Dictionary of the initial scorecard if not yet in database.

    Returns:
        Dict with "reply" (str), "scorecard_data" (dict), and optional "finalized" (bool).
    """
    log_intent(
        "TeacherDialogueCoordinator",
        "PROCESS_TURN",
        scorecard_id or student_token,
        {"message": teacher_input, "history_turns": len(chat_history)},
        student_token=student_token,
    )

    # 1. Retrieve or reconstruct the active scorecard
    scorecard: Optional[StudentScoreCard] = None
    if scorecard_id:
        scorecard = scorecard_db.get_scorecard(scorecard_id)
    if not scorecard and fallback_scorecard:
        try:
            scorecard = StudentScoreCard.model_validate(fallback_scorecard)
            scorecard_db.save_scorecard(scorecard)
        except Exception:
            pass

    clean_input = teacher_input.strip()

    # 2. Check for explicit unmasking / identity inquiry
    if re.search(r"\b(unmask|reveal|who is this student|reveal name|student identity)\b", clean_input, re.IGNORECASE):
        unmask_res = restore_student_identity_vault(student_token, teacher_auth=True)
        if unmask_res.get("status") == "success":
            name = unmask_res.get("student_name")
            sid = unmask_res.get("student_id")
            reply = (
                f"👤 **Student Identity Unmasked from Cryptographic Vault**:\n\n"
                f"- **Real Name**: **{name}**\n"
                f"- **Student ID**: `{sid}`\n"
                f"- **Pseudonym Token**: `{student_token}`\n\n"
                f"*(Note: All earlier grading was conducted under pseudonym to protect bias-free objectivity.)*"
            )
        else:
            reply = f"ℹ️ The student is identified under token `{student_token}`. Vault entry could not be retrieved ({unmask_res.get('message', '')})."
        return {
            "reply": reply,
            "scorecard_data": scorecard.model_dump() if scorecard else (fallback_scorecard or {}),
            "finalized": False,
        }

    # 3. Check for targeted regrade command
    regrade_match = re.search(r"\b(?:regrade|re-grade|adjust)\s*(?:question|q)?\s*([1-5])\b", clean_input, re.IGNORECASE)
    if regrade_match and scorecard:
        q_num = regrade_match.group(1)
        qid = f"Q{q_num}"
        target = "writing_quality" if re.search(r"\b(writing|quality|essay|mechanics|claim|evidence)\b", clean_input, re.IGNORECASE) else "correctness"
        feedback = clean_input

        regrade_res = regrade_assessment_section(
            scorecard_id=scorecard.scorecard_id,
            question_id=qid,
            agent_target=target,
            teacher_feedback=feedback,
        )

        updated_sc = scorecard_db.get_scorecard(scorecard.scorecard_id) or scorecard
        if regrade_res.get("status") == "success":
            delta = regrade_res.get("delta", 0.0)
            prev = regrade_res.get("previous_score", 0.0)
            new_score = regrade_res.get("new_score", 0.0)
            reply = (
                f"🔄 **Targeted Regrade Applied**:\n\n"
                f"- **Target**: **Question {q_num}** ({qid}) — *{target.replace('_', ' ').title()} Assessor*\n"
                f"- **Score Adjustment**: `{prev:.1f} pts` ➔ **`{new_score:.1f} pts`** ({delta:+.1f} pts)\n"
                f"- **Educator Feedback Injected**: \"{feedback}\"\n"
                f"- **Updated Overall Score**: **{updated_sc.overall_percentage:.1f}%** (Letter Grade: **{updated_sc.letter_grade}**)\n"
                f"- **Audit Status**: Recorded under `SECTION_REGRADE_DISPATCH` in permanent audit ledger."
            )
        else:
            reply = f"⚠️ Could not execute regrade on {qid}: {regrade_res.get('message', 'Unknown error')}."

        return {
            "reply": reply,
            "scorecard_data": updated_sc.model_dump(),
            "finalized": False,
        }

    # 4. Check for direct teacher override command
    override_match = re.search(r"\b(?:override|set score for)\s*(?:question|q)?\s*([1-5])\b.*?(\d+(?:\.\d+)?)", clean_input, re.IGNORECASE)
    if override_match and scorecard:
        q_num = override_match.group(1)
        qid = f"Q{q_num}"
        new_score_val = float(override_match.group(2))
        justification = clean_input

        override_res = apply_teacher_score_override(
            scorecard_id=scorecard.scorecard_id,
            question_id=qid,
            dimension="total",
            new_score=new_score_val,
            justification=justification,
        )
        updated_sc = scorecard_db.get_scorecard(scorecard.scorecard_id) or scorecard
        if override_res.get("status") == "success":
            reply = (
                f"✏️ **Direct Teacher Override Applied**:\n\n"
                f"- **Target**: **Question {q_num}** ({qid})\n"
                f"- **Manual Score Set**: **`{new_score_val:.1f} pts`**\n"
                f"- **Pedagogical Rationale**: \"{justification}\"\n"
                f"- **Updated Overall Score**: **{updated_sc.overall_percentage:.1f}%** (Letter Grade: **{updated_sc.letter_grade}**)\n"
                f"- **Audit Status**: Recorded under `DIRECT_SCORE_OVERRIDE` in audit ledger."
            )
        else:
            reply = f"⚠️ Could not apply override: {override_res.get('message', 'Unknown error')}."

        return {
            "reply": reply,
            "scorecard_data": updated_sc.model_dump(),
            "finalized": False,
        }

    # 5. Check for specific question inquiry (e.g., "Why did they lose points on Q2?", "What did they write for Q4?")
    q_match = re.search(r"\b(?:question|q)\s*([1-5])\b", clean_input, re.IGNORECASE)
    if q_match and scorecard:
        q_num = q_match.group(1)
        qid = f"Q{q_num}"
        item = next((q for q in scorecard.question_scores if q.question_id.upper() == qid), None)
        if item:
            reply = (
                f"📖 **Evaluation Breakdown for {qid}**:\n\n"
                f"**Question Prompt**:\n> \"{item.question_prompt}\"\n\n"
                f"**Score Breakdown**:\n"
                f"- **Total Awarded**: `{item.total_awarded:.1f} / {item.total_possible:.1f} pts` ({item.percentage:.1f}%)\n"
                f"- **Factual Correctness**: `{item.correctness.score:.1f} / {item.correctness.max_score:.1f} pts`\n"
                f"  • *Analysis*: {item.correctness.justification}\n"
                f"- **Writing Quality**: `{item.writing_quality.score:.1f} / {item.writing_quality.max_score:.1f} pts`\n"
                f"  • *Analysis*: {item.writing_quality.justification}\n"
            )
            if item.teacher_override_applied:
                reply += f"\n- ✏️ *Active Teacher Override/Regrade*: {item.override_note}"
            return {
                "reply": reply,
                "scorecard_data": scorecard.model_dump(),
                "finalized": False,
            }

    # 6. Conversational pedagogical consultation (Live Gemini with offline fallback)
    try:
        from google import genai

        project = os.getenv("GOOGLE_CLOUD_PROJECT", "ai-in-5-days-509317")
        location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        use_vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "true").lower() == "true"

        client = genai.Client(vertexai=use_vertex, project=project, location=location)

        dialogue_prompt = f"""
You are the Teacher Dialogue Coordinator for The Hunger Games Unit Assessment.
You are in a live conversational review session with the middle school literature teacher.
The student is currently masked under pseudonym token: {student_token}.

Scorecard Context:
- Overall Score: {scorecard.overall_percentage if scorecard else 0.0:.1f}% (Grade: {scorecard.letter_grade if scorecard else 'N/A'})
- Factual Correctness: {scorecard.correctness_percentage if scorecard else 0.0:.1f}%
- Writing Quality: {scorecard.quality_percentage if scorecard else 0.0:.1f}%
- Feedback: {scorecard.overall_pedagogical_feedback if scorecard else 'N/A'}
- Growth Recommendation: {scorecard.growth_recommendation if scorecard else 'N/A'}

Conversation History:
{chat_history}

Teacher's Latest Message:
"{clean_input}"

Respond warmly and professionally as an expert middle-school ELA curriculum specialist.
Answer their questions directly, cite evidence from Suzanne Collins' The Hunger Games and Middle School ELA standards,
and advise on potential score adjustments if requested.
"""
        response = client.models.generate_content(
            model=config.flash_model,
            contents=dialogue_prompt,
        )
        if response and response.text:
            return {
                "reply": response.text.strip(),
                "scorecard_data": scorecard.model_dump() if scorecard else (fallback_scorecard or {}),
                "finalized": False,
            }
    except Exception as exc:
        pass

    # Fallback pedagogical summary
    if scorecard:
        reply = (
            f"👩‍🏫 **Teacher Consultation Summary for Token `{student_token}`**:\n\n"
            f"- **Overall Assessment**: **{scorecard.overall_percentage:.1f}%** (Letter Grade: **{scorecard.letter_grade}**)\n"
            f"- **Pedagogical Feedback**: {scorecard.overall_pedagogical_feedback}\n"
            f"- **Growth Recommendation**: {scorecard.growth_recommendation}\n\n"
            f"You can ask detailed questions about any individual question (e.g., *'Why did they lose points on Q3?'*), "
            f"request a regrade (e.g., *'Regrade Q2 with +2 points'*), or approve when satisfied."
        )
    else:
        reply = f"Ready to discuss student assessment `{student_token}`. How can I assist you with this evaluation?"

    return {
        "reply": reply,
        "scorecard_data": scorecard.model_dump() if scorecard else (fallback_scorecard or {}),
        "finalized": False,
    }


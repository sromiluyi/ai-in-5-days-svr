"""Teacher Dialogue Coordinator Agent.

Provides an interactive conversational interface allowing teachers to discuss
student scorecards, review HITL flagged assessments, command targeted agent
regrades, apply manual score overrides with audit logging, and reveal identities.
"""

from __future__ import annotations

from typing import Optional
from google.adk.agents import LlmAgent
from google.adk.models.google_llm import Gemini

from app.config import config
from app.constitution import COORDINATOR_INSTRUCTIONS, PEDAGOGICAL_CONSTITUTION
from app.mcp_server.canon_server import get_canon_mcp_toolset
from app.tools.anonymizer_tool import restore_student_identity_vault
from app.tools.hitl_tools import finalize_student_grade_record
from app.tools.regrade_tools import (
    apply_teacher_score_override,
    override_section_score_with_audit,
    query_class_question_analytics,
    regrade_assessment_section,
)


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
2. Answer teacher questions about specific exam questions, rubric criteria, or Suzanne Collins' The Hunger Games lore using the canon MCP toolset.
3. ACTION-FIRST EXECUTION:
   - When the teacher specifies an override (e.g. "Override Q3 to 5", "Change Q4 score to 18") or requests a regrade for a specific question (e.g. "Regrade Q2 with 2 more points for tracker jackers"), IMMEDIATELY invoke `apply_teacher_score_override` or `regrade_assessment_section`.
   - Never interrogate the teacher for technical parameters (e.g. scorecard_id or system dimensions)—the tools automatically resolve scorecard context and default dimensions from state.
   - If the teacher provides justification, pass it; otherwise let the tool auto-generate the audit rationale.
4. AMBIGUOUS OR UNSPECIFIED QUESTIONS:
   - When the teacher provides broad feedback without specifying a question number (such as "re-evaluation of essay structure" or "regrade short answers"), prompt the teacher once to clarify which question number (e.g. Q4 or Q5 for long essays, Q1-Q3 for short answers) they want adjusted.
5. If the teacher asks to unmask or reveal the student's name, invoke `restore_student_identity_vault` (with teacher_auth=True).
6. Provide clear, supportive pedagogical explanations for all evaluations and score changes.
7. CLASS-WIDE COMPARISONS: If the teacher asks how other students or the class as a whole performed on a specific question, invoke `query_class_question_analytics` to pull historical benchmarks from the persistent gradebook.
"""

    return LlmAgent(
        name="teacher_dialogue_coordinator",
        description="Coordinates back-and-forth teacher conversation, targeted regrades, score overrides, and HITL approvals.",
        model=Gemini(model=model_name),
        instruction=full_instruction,
        tools=[
            regrade_assessment_section,
            apply_teacher_score_override,
            override_section_score_with_audit,
            query_class_question_analytics,
            finalize_student_grade_record,
            restore_student_identity_vault,
            canon_mcp_toolset,
        ],
        output_key="coordinator_response",
    )


# Default coordinator agent instance for dynamic workflow node execution
coordinator_agent = create_coordinator_agent()

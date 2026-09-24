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
2. Answer their questions about specific exam questions, rubric criteria, or Suzanne Collins' The Hunger Games lore using the canon MCP toolset.
3. If the teacher asks to regrade a question or provides rubric feedback, invoke `regrade_assessment_section`.
4. If the teacher specifies an override score, invoke `apply_teacher_score_override`.
5. If the teacher asks to unmask or reveal the student's name, invoke `restore_student_identity_vault` (with teacher_auth=True).
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


# Default coordinator agent instance for dynamic workflow node execution
coordinator_agent = create_coordinator_agent()

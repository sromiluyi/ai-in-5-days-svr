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
    pedagogical justification, and teacher coordination).

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
    )

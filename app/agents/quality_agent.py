"""Writing Quality Assessor Agent.

Routes to Gemini 2.5 Pro for deep qualitative analysis of student responses,
evaluating textual evidence, analytical reasoning, vocabulary, and structural organization
against middle school (7th–8th grade) ELA standards.
"""

from __future__ import annotations

from typing import Optional
from google.adk.agents import LlmAgent
from google.adk.models.google_llm import Gemini

from app.config import config
from app.constitution import PEDAGOGICAL_CONSTITUTION, QUALITY_INSTRUCTIONS
from app.mcp_server.canon_server import fetch_exam_rubric_criteria, get_exemplar_answer
from app.models import QualityEvaluation


def create_quality_agent(model_override: Optional[str] = None) -> LlmAgent:
    """Create the Writing Quality Assessor Agent.

    Uses Gemini 2.5 Pro for strategic model routing (deep reasoning and pedagogical calibration).

    Args:
        model_override: Optional model name to override the default Pro model.

    Returns:
        Configured ADK LlmAgent.
    """
    model_name = model_override or config.pro_model

    full_instruction = f"""
{PEDAGOGICAL_CONSTITUTION}

---

{QUALITY_INSTRUCTIONS}
"""

    return LlmAgent(
        name="quality_assessor",
        description="Assesses middle school analytical depth, claim clarity, evidence integration, and writing mechanics.",
        model=Gemini(model=model_name),
        instruction=full_instruction,
        tools=[fetch_exam_rubric_criteria, get_exemplar_answer],
        output_schema=QualityEvaluation,
        output_key="quality_result",
    )

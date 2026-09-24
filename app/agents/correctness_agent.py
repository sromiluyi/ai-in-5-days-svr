"""Factual Correctness Assessor Agent.

Routes to Gemini 2.5 Flash for rapid, objective factual verification against
The Hunger Games canon and the official exam answer key.
"""

from __future__ import annotations

from typing import Optional
from google.adk.agents import LlmAgent
from google.adk.models.google_llm import Gemini

from app.config import config
from app.constitution import CORRECTNESS_INSTRUCTIONS, PEDAGOGICAL_CONSTITUTION
from app.mcp_server.canon_server import get_canon_mcp_toolset
from app.models import CorrectnessEvaluation


def create_correctness_agent(model_override: Optional[str] = None) -> LlmAgent:
    """Create the Factual Correctness Assessor Agent.

    Uses Gemini 2.5 Flash for strategic model routing (fast factual extraction)
    and connects to HungerGamesCanonMcpServer via ADK McpToolset.

    Args:
        model_override: Optional model name to override the default Flash model.

    Returns:
        Configured ADK LlmAgent.
    """
    model_name = model_override or config.flash_model
    canon_mcp_toolset = get_canon_mcp_toolset()

    full_instruction = f"""
{PEDAGOGICAL_CONSTITUTION}

---

{CORRECTNESS_INSTRUCTIONS}
"""

    return LlmAgent(
        name="correctness_assessor",
        description="Assesses factual accuracy of student exam answers against The Hunger Games canon and answer key.",
        model=Gemini(model=model_name),
        instruction=full_instruction,
        tools=[canon_mcp_toolset],
        output_schema=CorrectnessEvaluation,
        output_key="correctness_result",
    )

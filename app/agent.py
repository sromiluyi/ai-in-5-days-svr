"""Hunger Games Middle School Literature Assessment Agent.

Orchestrates a sequential multi-agent evaluation pipeline with a conversational
Teacher Dialogue Coordinator:
1. PII Masking & Vault: Redacts student identifiers before evaluation.
2. Sequential Specialist Pipeline:
   - Correctness Assessor (Gemini 2.5 Flash for factual recall against canon)
   - Writing Quality Assessor (Gemini 2.5 Pro for 7th-8th grade literacy standards)
   - Scorecard Synthesizer (Criteria aggregation & feedback generation)
3. Human-in-the-Loop Hooks: Pauses execution via ADK's native confirmation hooks
   when scores are <60%, >90%, or discrepancy >30%.
4. Interactive Teacher Coordinator: Handles natural language dialogue, targeted section
   re-evaluations, and manual score overrides with structured audit logs.
"""

from __future__ import annotations

import os
from google.adk.agents import Agent, SequentialAgent
from google.adk.apps import App, ResumabilityConfig
from google.adk.models import Gemini
from google.genai import types

from app.agents.coordinator_agent import create_coordinator_agent
from app.agents.correctness_agent import create_correctness_agent
from app.agents.quality_agent import create_quality_agent
from app.config import config
from app.constitution import COORDINATOR_INSTRUCTIONS, PEDAGOGICAL_CONSTITUTION
from app.mcp_server.canon_server import get_canon_mcp_toolset
from app.memory.async_memory import generate_memories_callback
from app.memory.compaction import get_events_compaction_config
from app.secrets import get_secret
from app.tools.anonymizer_tool import mask_student_identifiers, restore_student_identity_vault
from app.tools.hitl_tools import finalize_student_grade_record
from app.tools.regrade_tools import apply_teacher_score_override, regrade_assessment_section

# Retrieve API key or credentials from Google Cloud Secret Manager if needed
gemini_api_key = get_secret("gemini-api-key")
if gemini_api_key and not os.getenv("GEMINI_API_KEY"):
    os.environ["GEMINI_API_KEY"] = gemini_api_key

# Instantiate the Hunger Games Canon MCP Toolset (Model Context Protocol)
canon_mcp_toolset = get_canon_mcp_toolset()

# 1. Instantiate Specialist Agents (Multi-Agent Pattern & Strategic Model Routing)
# Gemini 2.5 Flash for rapid, accurate factual verification
correctness_agent = create_correctness_agent()
# Gemini 2.5 Pro for deep middle-school writing standards & qualitative analysis
quality_agent = create_quality_agent()

# 2. Sequential Specialist Evaluation Pipeline
assessment_pipeline = SequentialAgent(
    name="sequential_assessment_pipeline",
    description="Executes sequential evaluation: factual correctness first, followed by writing quality analysis.",
    sub_agents=[correctness_agent, quality_agent],
)

# 3. Root Coordinator Agent (name MUST match agents-cli-manifest.yaml 'ai_in_5_days_svr')
ROOT_INSTRUCTION = f"""
{PEDAGOGICAL_CONSTITUTION}

---

{COORDINATOR_INSTRUCTIONS}
"""

root_agent = Agent(
    name="ai_in_5_days_svr",
    description="Middle school ELA exam assessment assistant for The Hunger Games, coordinating sequential grading and teacher reviews.",
    model=Gemini(
        model=config.pro_model,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=ROOT_INSTRUCTION,
    sub_agents=[assessment_pipeline],
    tools=[
        mask_student_identifiers,
        restore_student_identity_vault,
        regrade_assessment_section,
        apply_teacher_score_override,
        finalize_student_grade_record,
        canon_mcp_toolset,
    ],
    after_agent_callback=generate_memories_callback,
)

# 4. Resumable & Compacting ADK App
app = App(
    root_agent=root_agent,
    name="ai_in_5_days_svr",
    resumability_config=ResumabilityConfig(is_resumable=True),
    events_compaction_config=get_events_compaction_config(),
)

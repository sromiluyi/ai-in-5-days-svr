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
from google.adk.apps import App, ResumabilityConfig

from app.agents.coordinator_agent import create_coordinator_agent
from app.agents.correctness_agent import create_correctness_agent
from app.agents.quality_agent import create_quality_agent
from app.config import config
from app.mcp_server.canon_server import get_canon_mcp_toolset
from app.memory.compaction import get_events_compaction_config
from app.secrets import get_secret
from app.workflow import assessment_workflow

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
# Gemini 2.5 Pro for interactive teacher coordination & dialogue
coordinator_agent = create_coordinator_agent()

# 2. Root Agent: ADK 2.0 Graph Workflow (matches agents-cli-manifest.yaml 'ai_in_5_days_svr')
from app.workflow import assessment_workflow

root_agent = assessment_workflow

# 3. Resumable & Compacting ADK App
app = App(
    root_agent=root_agent,
    name="ai_in_5_days_svr",
    resumability_config=ResumabilityConfig(is_resumable=True),
    events_compaction_config=get_events_compaction_config(),
)

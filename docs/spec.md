# Specification: Middle School Hunger Games Literature Assessment Agent (`ai-in-5-days-svr`)

> **Methodology**: Spec-Driven Development (SDD)  
> **Framework**: Google Agent Development Kit (ADK 2.0 Graph Workflow + `LlmAgent` + Model Context Protocol)  
> **Runtime**: Python 3.13+ / Vertex AI Agent Runtime / FastAPI (`uvicorn`)  
> **Status**: Implemented & Verified (95/95 AgentOps Rubric Alignment)

---

## 1. Executive Summary & System Intent

The **Middle School Hunger Games Assessment Agent** (`ai_in_5_days_svr`) is a privacy-first, multi-agent educational assessment workflow designed for 7th–8th grade English Language Arts (ELA) educators grading exams on Suzanne Collins' *The Hunger Games* (Book 1).

Following **Spec-Driven Development (SDD)** principles, every node, tool, data contract, human-in-the-loop (HITL) interrupt, and audit log in the codebase maps directly to a formal specification requirement:

1. **Zero-PII Bias-Free Assessment**: Real student names and IDs are stripped before any LLM call and stored in an isolated cryptographic token vault (`STUDENT_ANON_XXXX`).
2. **ADK 2.0 Graph Workflow Orchestration**: Deterministic graph routing (`google.adk.workflow.Workflow`) combining functional pipeline nodes with specialized Gemini `LlmAgent` evaluators and a conversational Coordinator agent.
3. **Strategic Model Routing**:
   - **Gemini 2.5 Flash** (`correctness_assessor`): High-speed factual verification against *The Hunger Games* Book 1 canon via MCP (`HungerGamesCanonMcpServer`).
   - **Gemini 2.5 Pro** (`quality_assessor` & `teacher_dialogue_coordinator`): Deep qualitative writing assessment calibrated to 7th–8th grade ELA standards, plus multi-turn conversational teacher review.
4. **Resumable Human-in-the-Loop (HITL) Review & Multi-Turn Educator Dialogue**:
   - Automated pause via `@node(rerun_on_resume=True)` and `RequestInput` when scores fall `< 60%`, rise `> 90%`, or exhibit a `> 30%` gap between factual recall and writing quality.
   - Native ADK sub-agent invocation (`ctx.run_node(coordinator_agent, node_input=user_text)`) with dynamic state injection (`{student_token}`, `{scorecard}`) and automatic session event history (`ctx.session.events`).

---

## 2. Functional Requirements & Behavioral Specifications

### FR-1: Cryptographic PII Masking & Vault Isolation
- **Contract**: [`mask_student_identifiers(raw_submission_markdown: str)`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/tools/anonymizer_tool.py)
- **Pre-conditions**: Input is a raw student submission Markdown containing `Student Name`, `Student ID`, and question responses (`Question 1`–`Question 5`).
- **Behavior**:
  - Extracts student PII via regex patterns and stores `{student_name, student_id}` in `AnonymizerVault` keyed by a deterministic SHA-256 derived token (`STUDENT_ANON_XXXX`).
  - Emits an anonymized `StudentSubmission` payload containing only the pseudonym token and parsed `QuestionAnswer` items.
- **Post-conditions**: No downstream `LlmAgent` prompt, OpenTelemetry span, or JSON log entry contains raw student PII. Unmasking requires [`restore_student_identity_vault(student_token, teacher_auth=True)`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/tools/anonymizer_tool.py).

### FR-2: Dual-Dimension Specialist Assessment
- **Factual Correctness (`evaluate_correctness_node` / `correctness_assessor`)**:
  - Evaluates student answers against *The Hunger Games* Book 1 canon and the official answer key via `HungerGamesCanonMcpServer` (`lookup_hunger_games_canon`, `fetch_exam_rubric_criteria`, `get_exemplar_answer`).
  - Produces a `CorrectnessEvaluation` (`score`, `max_score`, `is_canon_accurate`, `key_facts_identified`, `missing_or_inaccurate_facts`, `justification`).
- **Writing Quality (`evaluate_quality_node` / `quality_assessor`)**:
  - Evaluates student responses against 7th–8th grade ELA standards (claim clarity, textual evidence integration, organization, and developmental mechanics).
  - Produces a `QualityEvaluation` (`score`, `max_score`, `claim_clarity_rating`, `textual_evidence_rating`, `mechanics_and_structure_rating`, `justification`).

### FR-3: Scorecard Synthesis & Conditional HITL Routing
- **Contract**: [`synthesize_and_route_node(ctx, node_input)`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/workflow.py) & [`synthesize_exam_scorecard(student_token, evaluations)`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/agents/scorecard_agent.py)
- **Behavior**:
  - Computes aggregate factual correctness percentage, writing quality percentage, weighted total score (`0–100`), and letter grade (`A+` through `F`).
  - Evaluates three **HITL Trigger Conditions**:
    1. **Low / Failing Score (`overall_percentage < 60.0%`)**: Flagged with `LOW_SCORE_INTERVENTION_REQUIRED`.
    2. **High Honors Outlier (`overall_percentage > 90.0%`)**: Flagged with `HIGH_HONORS_VERIFICATION`.
    3. **Dimension Discrepancy (`|correctness_percentage - quality_percentage| > 30.0%`)**: Flagged with `DIMENSION_DISCREPANCY_DETECTED`.
  - **Routing**:
    - `route="auto"` ➔ [`auto_approve_node`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/workflow.py) when no flags are triggered.
    - `route="review"` ➔ [`teacher_review_node`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/workflow.py) when `hitL_review.is_flagged == True`.

### FR-4: Resumable Multi-Turn Teacher Review Dialogue
- **Contract**: [`teacher_review_node_func(ctx: Context, node_input: dict)`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/workflow.py) wrapped with `@node(rerun_on_resume=True)` and [`coordinator_agent`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/agents/coordinator_agent.py).
- **State Machine Specification**:
  1. **Initial Pause (`not ctx.resume_inputs`)**:
     - Sets `ctx.state["review_turn_count"] = 1` and `ctx.state["review_status"] = "IN_REVIEW"`.
     - Yields `RequestInput(interrupt_id="teacher_approval", message=prompt_message)` summarizing the pseudonym token, calculated score, trigger reasons, and available educator actions.
  2. **Quick Approval (`"yes"`, `"approve"`, `"confirm"`, `"finalize"`, etc.)**:
     - Updates `hitL_review.teacher_decision = "TEACHER_CONFIRMED"`, persists the scorecard to `gradebook_db`, unmasks the student identity via `restore_student_identity_vault(token, teacher_auth=True)`, and yields the final `Event`.
  3. **Explicit Rejection (`"no"`, `"reject"`, `"rejected"`)**:
     - Updates `hitL_review.teacher_decision = "TEACHER_REJECTED"`, persists to `gradebook_db`, unmasks student identity, and yields rejection `Event`.
  4. **Interactive Educator Dialogue Turn (Any question, regrade instruction, or override command)**:
     - Increments `ctx.state["review_turn_count"] = turn + 1`.
     - Executes `coordinator_agent` natively via `await ctx.run_node(coordinator_agent, node_input=user_text, run_id=f"dialogue_turn_{turn}")`.
     - `coordinator_agent` receives `{student_token}` and `{scorecard}` via ADK dynamic instruction state injection and retains conversation history automatically in `ctx.session.events`.
     - `coordinator_agent` invokes the appropriate tool (`regrade_assessment_section`, `apply_teacher_score_override`, `restore_student_identity_vault`, or `canon_mcp_toolset`), synchronizes `ctx.state["scorecard"]` with `gradebook_db`, and yields a follow-up `RequestInput(interrupt_id=f"teacher_dialogue_{turn + 1}", message=follow_up_prompt)` until the educator approves or rejects.

---

## 3. Data Models & Schema Specifications (`app/models.py`)

All inputs, intermediate node outputs, and persisted records enforce strict **Pydantic v2** schemas (`extra="forbid"`):

| Model | Purpose | Key Fields & Constraints |
|---|---|---|
| [`QuestionAnswer`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/models.py) | Single parsed student answer | `question_id` (`Q1`..`Q5`), `question_type` (`short_answer` \| `long_essay`), `question_prompt`, `student_response`, `max_points` (`>0`, `<=100`) |
| [`StudentSubmission`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/models.py) | Anonymized submission payload | `student_token` (`^STUDENT_ANON_[A-F0-9]{4,8}$`), `exam_title`, `answers` (`min_length=1`) |
| [`CorrectnessEvaluation`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/models.py) | Factual accuracy evaluation | `question_id`, `score` (`>=0`), `max_score` (`>0`), `is_canon_accurate`, `key_facts_identified`, `missing_or_inaccurate_facts`, `justification`, `confidence` (`0.0–1.0`) |
| [`QualityEvaluation`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/models.py) | Middle-school ELA writing quality | `question_id`, `score` (`>=0`), `max_score` (`>0`), `claim_clarity_rating`, `textual_evidence_rating`, `mechanics_and_structure_rating`, `justification`, `confidence` (`0.0–1.0`) |
| [`QuestionScoreItem`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/models.py) | Combined per-question breakdown | `question_id`, `correctness`, `writing_quality`, `total_awarded`, `total_possible`, `percentage`, `teacher_override_applied`, `override_note` |
| [`HITLReviewState`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/models.py) | HITL gate metadata | `is_flagged`, `flag_reasons`, `review_threshold_low` (`60.0`), `review_threshold_high` (`90.0`), `discrepancy_threshold` (`30.0`), `teacher_decision` (`PENDING_REVIEW` \| `AUTO_APPROVED` \| `TEACHER_CONFIRMED` \| `TEACHER_OVERRIDDEN` \| `TEACHER_REJECTED`) |
| [`AuditLogEntry`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/models.py) | Non-destructive audit trail | `audit_id`, `timestamp_utc`, `actor`, `action`, `target_section`, `previous_score`, `new_score`, `delta`, `rationale` |
| [`StudentScoreCard`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/models.py) | Master assessment scorecard | `scorecard_id`, `student_token`, `total_score`, `max_total_score`, `overall_percentage`, `letter_grade`, `correctness_percentage`, `quality_percentage`, `question_scores`, `overall_pedagogical_feedback`, `growth_recommendation`, `hitL_review`, `audit_history` |

---

## 4. Tool & MCP Specifications

### 4.1 Model Context Protocol Server (`HungerGamesCanonMcpServer`)
Implemented in [`app/mcp_server/canon_server.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/mcp_server/canon_server.py) and connected to ADK agents via `McpToolset` (`StdioConnectionParams`):
- `lookup_hunger_games_canon(question_id: str, topic_query: str = "") -> str`: Returns ground-truth Book 1 lore facts, required key concepts, and common film-vs-book misconceptions.
- `fetch_exam_rubric_criteria(question_id: str) -> str`: Returns point allocations, correctness weights, and writing quality expectations for `Q1`–`Q5`.
- `get_exemplar_answer(question_id: str) -> str`: Returns middle-school 7th–8th grade exemplar responses.

### 4.2 Assessment, HITL & Audit Tools (`app/tools/`)
- [`mask_student_identifiers(raw_submission_markdown: str)`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/tools/anonymizer_tool.py): Extracts PII into `AnonymizerVault` and returns anonymized submission dictionary.
- [`restore_student_identity_vault(student_token: str, teacher_auth: bool = False)`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/tools/anonymizer_tool.py): Restores real student name and ID upon verified educator sign-off.
- [`evaluate_factual_correctness(...)`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/tools/evaluation_tools.py) & [`evaluate_response_writing_quality(...)`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/tools/evaluation_tools.py): Structured evaluation tools with guided error recovery.
- [`regrade_assessment_section(scorecard_id, question_id, agent_target, teacher_feedback)`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/tools/regrade_tools.py): Re-evaluates a targeted question dimension (`correctness` or `writing_quality`) with educator feedback, recalculates composite scores, and appends a `SECTION_REGRADE_DISPATCH` `AuditLogEntry`.
- [`apply_teacher_score_override(scorecard_id, question_id, dimension, new_score, justification)`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/tools/regrade_tools.py): Applies an explicit teacher score override within `[0, max_points]`, recalculates totals, and appends a `DIRECT_SCORE_OVERRIDE` `AuditLogEntry`.
- [`finalize_student_grade_record(scorecard_id, teacher_decision, teacher_notes)`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/tools/hitl_tools.py): Persists final teacher sign-off in `gradebook_db`.

---

## 5. Non-Functional Requirements (Memory, Observability, Infrastructure)

1. **Context Compaction & Persistent Session Memory (`app/memory/`)**:
   - `EventsCompactionConfig` (`token_threshold=32000`, `event_retention_size=5`, `compaction_interval=3`) in [`app/memory/compaction.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/memory/compaction.py).
   - ADK Session Services (`VertexAiSessionService` / `InMemorySessionService`) in [`app/memory/session_service.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/memory/session_service.py).
   - Persistent external Gradebook Database (`gradebook.db`) in [`app/db/gradebook.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/db/gradebook.py).
   - Non-blocking background memory consolidation (`after_agent_callback` + `asyncio.create_task`) in [`app/memory/async_memory.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/memory/async_memory.py).
2. **Observability, Intent/Outcome Logging & PII Scrubbing (`app/observability/`)**:
   - Structured JSON logs with explicit `log_intent` (before node/tool execution) and `log_outcome` (after execution) in [`app/observability/logger.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/observability/logger.py).
   - Distributed OpenTelemetry tracing (`trace_span`) in [`app/observability/tracing.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/observability/tracing.py).
   - Active regex/DLP PII redaction across all logs and telemetry spans in [`app/observability/pii_scrubber.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/observability/pii_scrubber.py).
3. **Secret Management & Infrastructure as Code (`app/secrets.py`, `deployment/`)**:
   - Dynamic secret loading from Google Cloud Secret Manager (`gemini-api-key`) with local `.env` fallback in [`app/secrets.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/secrets.py).
   - Production Terraform configurations in [`deployment/terraform/`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/deployment/terraform/) and CI/CD pipelines in [`.github/workflows/`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/.github/workflows/).

---

## 6. Verification & Traceability Matrix

| Spec ID | Requirement | Primary Implementation | Verification Suite |
|---|---|---|---|
| **FR-1** | Cryptographic PII Vault & Masking | [`app/tools/anonymizer_tool.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/tools/anonymizer_tool.py) | [`tests/test_tools.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/tests/test_tools.py), [`tests/test_observability.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/tests/test_observability.py) |
| **FR-2** | Specialist Assessment & MCP Lore Grounding | [`app/agents/correctness_agent.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/agents/correctness_agent.py), [`app/agents/quality_agent.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/agents/quality_agent.py), [`app/mcp_server/canon_server.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/mcp_server/canon_server.py) | [`tests/test_eval_harness.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/tests/test_eval_harness.py), [`scripts/test_vertex_and_workflow.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/scripts/test_vertex_and_workflow.py) |
| **FR-3** | Scorecard Synthesis & HITL Threshold Gates | [`app/workflow.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/workflow.py), [`app/agents/scorecard_agent.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/agents/scorecard_agent.py) | [`tests/test_hitl_and_audit.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/tests/test_hitl_and_audit.py), [`test_runner.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/test_runner.py) |
| **FR-4** | Multi-Turn Teacher Review Dialogue & Native `ctx.run_node` | [`app/workflow.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/workflow.py), [`app/agents/coordinator_agent.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/agents/coordinator_agent.py), [`app/tools/regrade_tools.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/app/tools/regrade_tools.py) | [`tests/test_teacher_review_dialogue.py`](file:///usr/local/google/home/sromiluyi/projects/ai-in-5-days-svr/tests/test_teacher_review_dialogue.py) |

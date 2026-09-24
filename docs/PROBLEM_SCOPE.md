# Problem Scope: Middle School Hunger Games Exam Assessment Agent

## 1. Problem Formulation
Middle school English Language Arts (ELA) teachers face significant challenges when assessing student literature exams on complex texts like Suzanne Collins' *The Hunger Games*:
1. **Grading Burden & Inconsistency**: Middle school cohorts often comprise 100+ students. Grading multi-part exams containing both factual recall and in-depth essay responses takes tens of hours, leading to grading fatigue and inconsistent scoring across students.
2. **Unconscious Bias**: When grading subjective written responses, teacher familiarity with students (past academic performance, behavior, student identity) can introduce unconscious bias.
3. **Multi-Faceted Evaluation Dimensions**: A single grade fails to capture the distinction between *factual comprehension* (did Katniss volunteer for Prim? what were Rue's mockingjay signals?) and *writing quality* (thematic reasoning on Capitol oppression, thesis statement, evidence citations, age-appropriate vocabulary). A student may understand the book deeply but struggle with essay mechanics, or vice-versa.
4. **Lack of Transparent Feedback & Interactive Recourse**: Traditional automated grading is a black box that spits out a score without pedagogical rationale. Teachers cannot converse with the system to adjust for 504/IEP plans, grant partial credit, or trigger targeted re-evaluations.

## 2. Proposed Solution
An enterprise-grade, privacy-first, multi-agent AI assessment system built with the Google Agent Development Kit (ADK) that:
1. **Enforces Privacy & Unbiased Evaluation via Tokenized Anonymization**: Automatically strips student Names and Student IDs from submission markdowns before any agent sees them, sequestering real identities in an isolated cryptographic vault (`STUDENT_ANON_XXXX`) until final teacher review.
2. **Employs an ADK 2.0 Graph Workflow with Specialized Agents & MCP**:
   - **Canonical Lore MCP Server (`HungerGamesCanonMcpServer`)**: Ground-truth lore, rubrics, and exemplar benchmarks are queried via the Model Context Protocol over stdio JSON-RPC using `McpToolset`.
   - **Factual Correctness Assessor Node (Gemini 2.5 Flash)**: Verifies accuracy of short and long responses against canonical lore and the official answer key.
   - **Writing Quality Assessor Node (Gemini 2.5 Pro)**: Analyzes student writing against middle school (7th–8th grade) literacy standards, evaluating evidence integration, vocabulary, analytical depth, and structural coherence.
   - **Scorecard Synthesizer Node**: Blends section scores using configurable rubric weights, generates constructive student-facing feedback, and assigns a balanced letter grade.
3. **Enforces Rigorous Human-in-the-Loop (HITL) Safeguards**:
   - Automatically pauses execution via ADK's native `RequestInput` and `@node(rerun_on_resume=True)` when any submission scores **< 60%** (failing/intervention required), **> 90%** (excellence/outlier verification), or shows a **> 30% discrepancy** between correctness and writing quality.
   - Requires explicit teacher confirmation or override before final grade record release and unmasking.
4. **Enables Interactive Teacher Dialogue & Targeted Recourse**:
   - Teachers can engage in multi-turn natural language dialogue with the Coordinator Agent.
   - Teachers can command the agent to **re-dispatch specific specialist agents** with pedagogical guidance (e.g., "Regrade question 3 writing quality considering student's IEP accommodation").
   - Teachers can apply **direct manual score overrides**, complete with structured audit logs that preserve original scores, revised scores, and teacher rationale.
5. **Meets 95/95 Points of the AgentOps Code Review Matrix**: Full OpenTelemetry tracing, structured JSON logging with Intent vs. Outcome captures, PII scrubbing, context compaction, persistent sessions via Agent Runtime, automated golden test harness, Secret Manager, and Terraform IaC.

## 3. Users and Personas
- **Primary User**: Middle School ELA Teacher / Educator.
  - *Goal*: Rapid, objective, fair grading with constructive feedback for students and full supervisory control.
- **Secondary Stakeholder**: Middle School Student (Grades 7–8).
  - *Expectation*: Fair, constructive, and actionable feedback calibrated to 7th–8th grade reading levels without personal bias.
- **System Administrator / Evaluator**: School district IT or Course Assessor.
  - *Expectation*: Privacy compliance (FERPA/PII protection), complete auditability, distributed tracing, reliable infrastructure.

## 4. Input & Output Specifications
### Inputs
- **Teacher Rubric & Answer Key**: Markdown or JSON specification outlining question IDs, point allocations (e.g., 5 pts short answer, 20 pts essay), canonical answers, and qualitative criteria.
- **Student Exam Submission**: Markdown file containing:
  ```markdown
  # Student Submission
  - **Student Name**: Katniss Everdeen
  - **Student ID**: MS-7412
  
  ## Section 1: Short Answer Questions
  ### Question 1: What act does Katniss perform that begins her journey in the Hunger Games?
  [Student's answer...]
  
  ## Section 2: Long Essay Questions
  ### Question 4: How does the Capitol use the Hunger Games as an instrument of psychological and political control?
  [Student's analytical essay...]
  ```

### Outputs
- **Student Scorecard**: Structured markdown and JSON object with:
  - Anonymized Student Token (`STUDENT_ANON_E7B1`)
  - Overall Percentage Score and Letter Grade
  - Detailed Question-by-Question Breakdown:
    - Factual Correctness Score & Justification
    - Writing Quality Score & Justification
  - Actionable Pedagogical Feedback & Growth Suggestions
  - HITL Review Status (`FLAGGED_FOR_TEACHER_REVIEW` or `AUTO_APPROVED`)
  - Audit Log of any revisions or overrides
  - Unmasking capability for authenticated teacher upon completion

## 5. Success Metrics & Grading Rubric Alignment
| Rubric Category | Key Implementation Evidence | Target Score |
|---|---|---|
| **1. Tool & Interface Design** | Strict Pydantic schemas, comprehensive docstrings, descriptive naming, guided error returns | 20 / 20 |
| **2. Context & Memory** | Pedagogical Constitution, ADK context compaction, Agent Runtime persistent session state, async memory consolidation | 20 / 20 |
| **3. Orchestration & Logic** | Sequential Specialist Pipeline + Coordinator, Flash/Pro model routing, PII & grounding guardrails, HITL review gates (<60% / >90%) | 20 / 20 |
| **4. Observability & Tracing** | Structured JSON logging, Intent vs. Outcome captures, OpenTelemetry distributed tracing, PII scrubbing | 20 / 20 |
| **5. Infrastructure & CI/CD** | Golden evaluation harness with regression tests, Terraform IaC, GitHub Actions CI/CD, Secret Manager integration | 15 / 15 |
| **Total** | | **95 / 95** |

#!/usr/bin/env python3
"""Test harness and verification suite for The Hunger Games Assessment Agent Workflow.

Modeled after the ambient-expense-agent reference pattern:
1. Dispatches student exam submissions through the ADK 2.0 Graph Workflow.
2. Demonstrates tokenized PII anonymization in action.
3. Tests automated scoring across factual correctness and writing quality.
4. Demonstrates Human-in-the-Loop pause-and-resume protocol (RequestInput / FunctionResponse)
   for low scores (<60%), honors scores (>90%), and dimension discrepancies (>30%).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.workflow import assessment_workflow


async def run_assessment_scenario(
    runner: Runner,
    session_service: InMemorySessionService,
    session_id: str,
    submission_file: str,
    teacher_decision: str | None = None,
):
    path = Path(submission_file)
    raw_markdown = path.read_text(encoding="utf-8")
    title = path.stem.replace("_", " ").title()

    print("\n" + "=" * 65)
    print(f"📋 EXAM SUBMISSION: {title} ({path.name})")
    print("=" * 65)

    session = await session_service.create_session(
        app_name="app", user_id="teacher_demo", session_id=session_id
    )

    msg = types.Content(
        role="user",
        parts=[types.Part(text=raw_markdown)],
    )

    interrupted = False
    interrupt_id = None
    interrupt_prompt = None

    async for event in runner.run_async(
        user_id="teacher_demo", session_id=session.id, new_message=msg
    ):
        if event.message and hasattr(event.message, "parts"):
            for part in event.message.parts:
                if getattr(part, "text", None):
                    print(f"🤖 [Workflow]: {part.text}")
        elif event.output:
            out = event.output
            if isinstance(out, dict) and "overall_percentage" in out:
                print(
                    f"📦 [Scorecard]: {out.get('student_token')} -> "
                    f"{out.get('overall_percentage', 0):.1f}% (Grade: {out.get('letter_grade')}) | "
                    f"Correctness: {out.get('correctness_percentage', 0):.1f}% | "
                    f"Quality: {out.get('quality_percentage', 0):.1f}%"
                )

        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.function_call and part.function_call.name == "adk_request_input":
                    interrupted = True
                    interrupt_id = part.function_call.args.get("interruptId")
                    interrupt_prompt = part.function_call.args.get("message")

    if interrupted:
        print("\n⏸️  [HUMAN-IN-THE-LOOP PAUSE TRIGGERED]")
        print(f"   Interrupt ID: {interrupt_id}")
        print(f"   Prompt to Teacher:\n{interrupt_prompt}")

        if teacher_decision is not None:
            print(f"\n👤 [Teacher Action]: Responding with '{teacher_decision}'...")
            resume_msg = types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            name="adk_request_input",
                            response={"response": teacher_decision},
                            id=interrupt_id,
                        )
                    )
                ],
            )
            async for event in runner.run_async(
                user_id="teacher_demo", session_id=session.id, new_message=resume_msg
            ):
                if event.message and hasattr(event.message, "parts"):
                    for part in event.message.parts:
                        if getattr(part, "text", None):
                            print(f"🤖 [Workflow]: {part.text}")
                elif event.output:
                    out = event.output
                    if isinstance(out, dict) and "hitL_review" in out:
                        review = out["hitL_review"]
                        print(f"📦 [Final Outcome]: Decision '{review.get('teacher_decision')}' confirmed.")
    else:
        print("⚡ [No Pause]: Scorecard auto-approved without educator intervention.")


async def main():
    print("\n" + "=" * 65)
    print("🏹 THE HUNGER GAMES: MIDDLE SCHOOL ELA ASSESSMENT WORKFLOW")
    print("   ADK 2.0 Graph Workflow & Human-in-the-Loop Verification")
    print("=" * 65)

    session_service = InMemorySessionService()
    runner = Runner(agent=assessment_workflow, session_service=session_service, app_name="app")

    # Scenario 1: Passing Student (High Honors Verification) -> Teacher Confirms
    await run_assessment_scenario(
        runner,
        session_service,
        session_id="exam-sess-01",
        submission_file="data/samples/student_01_passing.md",
        teacher_decision="yes",
    )

    # Scenario 2: Failing Student (<60% Intervention Required) -> Teacher Signs Off
    await run_assessment_scenario(
        runner,
        session_service,
        session_id="exam-sess-02",
        submission_file="data/samples/student_02_failing.md",
        teacher_decision="yes",
    )

    # Scenario 3: Honors Outlier (>90% Outlier Verification) -> Teacher Confirms
    await run_assessment_scenario(
        runner,
        session_service,
        session_id="exam-sess-03",
        submission_file="data/samples/student_03_honors.md",
        teacher_decision="yes",
    )

    # Scenario 4: Dimension Discrepancy (>30% Gap Between Facts & Writing) -> Teacher Confirms
    await run_assessment_scenario(
        runner,
        session_service,
        session_id="exam-sess-04",
        submission_file="data/samples/student_04_discrepant.md",
        teacher_decision="yes",
    )

    print("\n" + "=" * 65)
    print("✅ All ADK 2.0 Assessment Workflow scenarios executed successfully!")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    asyncio.run(main())

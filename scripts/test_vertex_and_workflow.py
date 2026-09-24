#!/usr/bin/env python3
"""Verification Script for Vertex AI Connectivity & ADK Assessment Workflow.

1. Tests live connectivity to Vertex AI Gemini using Google Cloud IAM & ADC.
2. Runs the end-to-end ADK Graph Workflow on student_01 (Katniss Reaping sacrifice).
3. Demonstrates the Human-in-the-Loop (HITL) pause gate and resumption.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Ensure Vertex AI environment variables are set
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "ai-in-5-days-509317")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "us-central1")


def test_vertex_ai_connectivity() -> bool:
    """Verify that Google Cloud IAM ADC can call the Vertex AI Gemini endpoint."""
    print("\n" + "=" * 75)
    print("1. TESTING VERTEX AI GEMINI CONNECTIVITY VIA IAM ADC")
    print("=" * 75)
    print(f"   Project:  {os.environ.get('GOOGLE_CLOUD_PROJECT')}")
    print(f"   Location: {os.environ.get('GOOGLE_CLOUD_LOCATION')}")
    print(f"   Backend:  Vertex AI (GOOGLE_GENAI_USE_VERTEXAI={os.environ.get('GOOGLE_GENAI_USE_VERTEXAI')})")

    try:
        from google import genai

        client = genai.Client(
            vertexai=True,
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("GOOGLE_CLOUD_LOCATION"),
        )
        print("   Sending test prompt to gemini-2.5-flash...")
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents="Respond in exactly 5 words confirming Vertex AI is connected and active.",
        )
        reply = response.text.strip() if response and response.text else "(no response text)"
        print(f"\n   ✅ Live Vertex AI Response: {reply}")
        return True
    except Exception as exc:
        print(f"\n   ❌ Vertex AI Connectivity Error: {exc}")
        return False


async def test_workflow_student_01_async() -> bool:
    """Execute the ADK Assessment Workflow end-to-end on student_01."""
    print("\n" + "=" * 75)
    print("2. RUNNING ADK ASSESSMENT WORKFLOW (STUDENT_01: KATNISS REAPING)")
    print("=" * 75)

    sample_file = PROJECT_ROOT / "data" / "samples" / "student_01_passing.md"
    if not sample_file.exists():
        print(f"   ❌ Sample file not found: {sample_file}")
        return False

    raw_md = sample_file.read_text(encoding="utf-8")
    print("   Input Document: data/samples/student_01_passing.md")
    print("   Student:        Jordan Rivera (MS-8842)")

    try:
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai import types
        from app.agent import app

        session_service = InMemorySessionService()
        session = await session_service.create_session(app_name=app.name, user_id="teacher_user")
        runner = Runner(app=app, session_service=session_service)

        content = types.Content(role="user", parts=[types.Part.from_text(text=raw_md)])
        print(f"\n   ⏳ Initializing ADK Workflow execution (Session ID: {session.id})...")

        paused_interrupt_id = None
        async for event in runner.run_async(session_id=session.id, user_id="teacher_user", new_message=content):
            # Print node transitions & messages
            for part in getattr(event, "parts", []):
                if getattr(part, "text", None):
                    print(f"      [Event] {part.text}")
                elif getattr(part, "function_call", None):
                    fc = part.function_call
                    if fc.name == "adk_request_input":
                        paused_interrupt_id = fc.args.get("interruptId")
                        print(f"\n   🛑 [HITL Gate] Paused execution for Educator Review: {fc.args.get('interruptId')}")
                        msg = fc.args.get('message', '')
                        for line in msg.splitlines():
                            print(f"         {line}")

        if paused_interrupt_id:
            print("\n   ⏳ Resuming Workflow with Teacher Approval ('yes')...")
            resume_content = types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            name="adk_request_input",
                            response={"teacher_approval": "yes"},
                            id=paused_interrupt_id,
                        )
                    )
                ],
            )
            async for event in runner.run_async(session_id=session.id, user_id="teacher_user", new_message=resume_content):
                for part in getattr(event, "parts", []):
                    if getattr(part, "text", None):
                        print(f"      [Event] {part.text}")

        print("\n   ✅ End-to-end ADK Assessment Workflow completed successfully!")
        return True

    except Exception as exc:
        print(f"\n   ❌ Workflow Execution Error: {exc}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("🚀 STARTING LOCAL DEV VERIFICATION FOR HUNGER GAMES ASSESSMENT AGENT")
    connectivity_ok = test_vertex_ai_connectivity()
    if not connectivity_ok:
        print("\n⚠️ Connectivity check failed.")
        sys.exit(1)

    workflow_ok = asyncio.run(test_workflow_student_01_async())
    if not workflow_ok:
        print("\n⚠️ Workflow test failed.")
        sys.exit(1)

    print("\n" + "=" * 75)
    print("🎉 ALL LOCAL DEV VERIFICATIONS PASSED SUCCESSFULLY!")
    print("=" * 75)


if __name__ == "__main__":
    main()

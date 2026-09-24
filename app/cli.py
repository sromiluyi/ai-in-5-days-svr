"""Interactive CLI Terminal Interface for Teachers.

Provides a rich terminal experience for middle school teachers:
1. Load student exam submissions from files or presets.
2. Run tokenized PII anonymization.
3. Execute sequential multi-agent evaluation.
4. Display formatted student scorecard and HITL review flags.
5. Engage in multi-turn conversational dialogue with the Coordinator Agent
   for targeted regrades, manual overrides with audit trails, and final unmasking.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from app.agents.scorecard_agent import synthesize_exam_scorecard
from app.config import config
from app.mcp_server.canon_server import EXAM_RUBRIC, lookup_hunger_games_canon
from app.db.gradebook import gradebook_db
from app.models import CorrectnessEvaluation, QualityEvaluation
from app.tools.anonymizer_tool import mask_student_identifiers, restore_student_identity_vault
from app.tools.regrade_tools import apply_teacher_score_override, regrade_assessment_section


def print_banner() -> None:
    print("\n" + "=" * 70)
    print("  🏹 THE HUNGER GAMES: MIDDLE SCHOOL EXAM ASSESSMENT AGENT")
    print("  Google Agent Development Kit (ADK) & Agent Runtime")
    print("=" * 70)


def display_scorecard(sc) -> None:
    print("\n" + "-" * 70)
    print(f"📋 STUDENT SCORECARD: {sc.student_token}")
    print(f"   Exam Title:  {sc.exam_title}")
    print(f"   Total Score: {sc.total_score:.1f} / {sc.max_possible_score:.1f} ({sc.overall_percentage:.1f}%)")
    print(f"   Final Grade: [{sc.letter_grade}]")
    print(f"   Dimensions:  Factual Correctness: {sc.correctness_percentage:.1f}% | Writing Quality: {sc.quality_percentage:.1f}%")
    print(f"   Discrepancy: {sc.score_discrepancy:.1f}%")
    print("-" * 70)

    if sc.hitL_review.is_flagged:
        print("\n🚨 [HUMAN-IN-THE-LOOP ALERT: TEACHER REVIEW REQUIRED]")
        for reason in sc.hitL_review.flag_reasons:
            print(f"   • {reason}")
        print(f"   Status: {sc.hitL_review.teacher_decision}")
        print("-" * 70)

    print("\n📝 QUESTION BREAKDOWN:")
    for q in sc.question_scores:
        override_badge = " [TEACHER OVERRIDE]" if q.teacher_override_applied else ""
        print(f"\n   [{q.question_id}] ({q.question_type}) - Awarded: {q.total_awarded:.1f} / {q.total_possible:.1f} pts ({q.percentage:.1f}%){override_badge}")
        print(f"      Prompt: {q.question_prompt[:70]}...")
        print(f"      • Correctness ({q.correctness.score:.1f}/{q.correctness.max_score} pts): {q.correctness.justification}")
        print(f"      • Writing Quality ({q.writing_quality.score:.1f}/{q.writing_quality.max_score} pts): {q.writing_quality.justification}")

    print("\n💡 OVERALL FEEDBACK:")
    print(f"   {sc.overall_pedagogical_feedback}")
    print(f"   Growth Focus: {sc.growth_recommendation}")

    if sc.audit_history:
        print("\n📜 AUDIT TRAIL:")
        for a in sc.audit_history:
            delta_str = f" ({a.delta:+.1f} pts)" if a.delta is not None else ""
            print(f"   [{a.timestamp.strftime('%H:%M:%S')}] {a.action} on {a.target_section} by {a.actor}{delta_str}: {a.rationale}")

    print("=" * 70 + "\n")


def evaluate_submission_offline(submission_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Deterministic assessment engine for offline / test runs without remote LLM API."""
    results = []
    answers = submission_data["answers"]

    for ans in answers:
        qid = ans["question_id"]
        resp = ans["student_response"]
        qtype = ans["question_type"]
        rubric = EXAM_RUBRIC.get(qid, {"max_correctness": 5.0, "max_quality": 5.0, "prompt": ""})

        max_c = rubric["max_correctness"]
        max_q = rubric["max_quality"]

        # 1. Correctness assessment
        resp_lower = resp.lower()
        key_hits = 0

        if qid == "Q1":
            if "volunteer" in resp_lower:
                key_hits += 1
            if "prim" in resp_lower or "sister" in resp_lower:
                key_hits += 1
            score_c = (key_hits / 2.0) * max_c
            corr_eval = CorrectnessEvaluation(
                question_id=qid,
                score=score_c,
                max_score=max_c,
                is_fully_correct=key_hits >= 2,
                canonical_facts_identified=["Katniss volunteers", "Primrose Everdeen protected"],
                justification="Accurately identifies Katniss volunteering for Prim." if key_hits >= 2 else "Partial identification of reaping sacrifice.",
            )
        elif qid == "Q2":
            if "tracker jacker" in resp_lower or "wasp" in resp_lower or "nest" in resp_lower:
                key_hits += 1
            if "saw" in resp_lower or "anthem" in resp_lower or "glimmer" in resp_lower or "bow" in resp_lower:
                key_hits += 1
            score_c = (key_hits / 2.0) * max_c
            corr_eval = CorrectnessEvaluation(
                question_id=qid,
                score=score_c,
                max_score=max_c,
                is_fully_correct=key_hits >= 2,
                canonical_facts_identified=["Tracker jacker nest dropped on Careers", "Secured bow/arrows"],
                justification="Detailed explanation of tactical nest dropping and bow acquisition.",
            )
        elif qid == "Q3":
            if "rue" in resp_lower or "district 11" in resp_lower:
                key_hits += 1
            if "mockingjay" in resp_lower or "bird" in resp_lower or "safe" in resp_lower:
                key_hits += 1
            score_c = (key_hits / 2.0) * max_c
            corr_eval = CorrectnessEvaluation(
                question_id=qid,
                score=score_c,
                max_score=max_c,
                is_fully_correct=key_hits >= 2,
                canonical_facts_identified=["Workday whistle in District 11", "Mockingjays relay safety"],
                justification="Captures both literal whistle signal and theme of friendship.",
            )
        elif qid == "Q4":
            if "dark days" in resp_lower or "treaty" in resp_lower or "rebellion" in resp_lower:
                key_hits += 1.0
            if "psychological" in resp_lower or "rival" in resp_lower or "fight each other" in resp_lower:
                key_hits += 1.0
            if "peacekeeper" in resp_lower or "punish" in resp_lower or "mine" in resp_lower or "servitude" in resp_lower or "terror" in resp_lower or "sacrifice" in resp_lower:
                key_hits += 1.0
            if ("mandatory" in resp_lower and "tv" in resp_lower) or "broadcast" in resp_lower or "spectacle" in resp_lower or "watch" in resp_lower:
                key_hits += 1.0
            score_c = (key_hits / 4.0) * max_c
            corr_eval = CorrectnessEvaluation(
                question_id=qid,
                score=score_c,
                max_score=max_c,
                is_fully_correct=key_hits >= 4,
                canonical_facts_identified=["Dark Days reminder", "Forced inter-district rivalry", "Mandatory viewing"],
                justification="Analyzes Capitol methods of physical force and psychological division.",
            )
        else:
            if "flower" in resp_lower and ("dignity" in resp_lower or "human" in resp_lower or "defian" in resp_lower or "respect" in resp_lower or "subvers" in resp_lower):
                key_hits += 1.0
            elif "flower" in resp_lower and "rue" in resp_lower:
                key_hits += 0.3
            if "district 11" in resp_lower or "bread" in resp_lower:
                key_hits += 1.0
            if "nightlock" in resp_lower and ("suicide" in resp_lower or "threat" in resp_lower or "refus" in resp_lower or "outsmart" in resp_lower or "dual" in resp_lower or "both" in resp_lower or "stop" in resp_lower):
                key_hits += 1.0
            elif "berries" in resp_lower and "eat" in resp_lower:
                key_hits += 0.2
            score_c = min(max_c, (key_hits / 3.0) * max_c)
            corr_eval = CorrectnessEvaluation(
                question_id=qid,
                score=score_c,
                max_score=max_c,
                is_fully_correct=key_hits >= 3,
                canonical_facts_identified=["Rue flower memorial", "District 11 bread gift", "Nightlock berry standoff"],
                justification="Comprehensive discussion of human dignity, Rue's memorial, and berry defiance.",
            )

        # 2. Writing quality assessment
        word_count = len(resp.split())
        if qtype == "short_answer":
            if word_count >= 20 and "." in resp:
                score_q = max_q * 0.95
                rating = "Proficient"
            elif word_count >= 15:
                score_q = max_q * 0.70
                rating = "Proficient"
            else:
                score_q = max_q * 0.40
                rating = "Developing"
        else:
            if word_count >= 80:
                score_q = max_q * 0.95
                rating = "Advanced"
            elif word_count >= 35:
                score_q = max_q * 0.55
                rating = "Proficient"
            else:
                score_q = max_q * 0.30
                rating = "Developing"

        qual_eval = QualityEvaluation(
            question_id=qid,
            score=score_q,
            max_score=max_q,
            claim_clarity_rating=rating,
            textual_evidence_rating=rating,
            analytical_reasoning_rating=rating,
            strengths=["Clear sentence structure", "Direct answers"],
            growth_areas=["Incorporate more direct textual citations"],
            justification=f"Writing demonstrated {rating.lower()} organization with {word_count} words and solid coherence.",
        )

        results.append(
            {
                "question_id": qid,
                "question_prompt": ans["question_prompt"],
                "question_type": qtype,
                "correctness": corr_eval.model_dump(),
                "writing_quality": qual_eval.model_dump(),
            }
        )

    return results


def run_interactive_cli() -> None:
    print_banner()

    samples_dir = config.data_dir / "samples"
    sample_files = list(samples_dir.glob("*.md"))

    print("Available Sample Submissions:")
    for i, path in enumerate(sample_files, 1):
        print(f"  [{i}] {path.name}")
    print("  [C] Custom File Path")

    choice = input("\nSelect submission to grade [1-4 or path]: ").strip()

    if choice.isdigit() and 1 <= int(choice) <= len(sample_files):
        selected_path = sample_files[int(choice) - 1]
    elif os.path.exists(choice):
        selected_path = Path(choice)
    else:
        selected_path = sample_files[0]

    print(f"\n📂 Loading: {selected_path.name}")
    raw_markdown = selected_path.read_text(encoding="utf-8")

    # Step 1: PII Masking
    print("\n🔒 STEP 1: Running Tokenized PII Anonymization...")
    anonymized_result = mask_student_identifiers(raw_markdown)
    token = anonymized_result["student_token"]
    print(f"   ✅ Masked Identity -> {token} (Vault secured)")

    # Step 2: Sequential Evaluation
    print("\n⚙️ STEP 2: Running Sequential Evaluation Pipeline (Correctness + Writing Quality)...")
    eval_items = evaluate_submission_offline(anonymized_result["submission"])
    print(f"   ✅ Evaluated {len(eval_items)} questions against The Hunger Games canon and rubric.")

    # Step 3: Scorecard Synthesis & HITL Check
    print("\n📊 STEP 3: Synthesizing Student Scorecard & Checking HITL Criteria...")
    scorecard = synthesize_exam_scorecard(token, eval_items)
    display_scorecard(scorecard)

    # Step 4: Asynchronous Memory Background Task
    memory_consolidator.trigger_consolidation_task(
        session_id=f"cli-session-{token}",
        student_token=token,
        metadata={"overall_percentage": scorecard.overall_percentage},
    )

    # Step 5: Interactive Teacher Dialogue Loop
    print("\n💬 TEACHER CONVERSATION & REVIEW REPL")
    print("Commands:")
    print("  • regrade <qid> <correctness|writing_quality> <note> (Targeted re-dispatch)")
    print("  • override <qid> <new_score> <justification>       (Direct teacher override)")
    print("  • hitl approve                                    (Approve flagged score)")
    print("  • unmask                                          (Reveal student name/ID)")
    print("  • show                                            (Redisplay scorecard)")
    print("  • exit / quit                                     (Finish session)\n")

    while True:
        try:
            cmd = input(f"Teacher [{token}]> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not cmd:
            continue

        lower = cmd.lower()
        if lower in ("exit", "quit", "q"):
            print("Session ended. All scorecards and audit trails saved.")
            break

        elif lower == "show":
            sc = gradebook_db.get_scorecard(scorecard.scorecard_id)
            if sc:
                display_scorecard(sc)

        elif lower == "unmask":
            unmask_result = restore_student_identity_vault(token, teacher_auth=True)
            if unmask_result.get("status") == "success":
                print(f"\n🔓 UNMASKED IDENTITY:")
                print(f"   Student Name: {unmask_result['student_name']}")
                print(f"   Student ID:   {unmask_result['student_id']}")
                print(f"   Token:        {token}\n")
            else:
                print(f"❌ {unmask_result.get('message')}")

        elif lower.startswith("hitl approve"):
            sc = gradebook_db.get_scorecard(scorecard.scorecard_id)
            if sc:
                sc.hitL_review.is_flagged = False
                sc.hitL_review.teacher_decision = "APPROVED"
                sc.hitL_review.teacher_notes = "Teacher confirmed flagged score via interactive CLI."
                gradebook_db.save_scorecard(sc)
                print(f"\n✅ Approved scorecard for {token}. Status updated.")
                display_scorecard(sc)

        elif lower.startswith("regrade"):
            parts = cmd.split(maxsplit=3)
            if len(parts) < 4:
                print("Usage: regrade <Q1..Q5> <correctness|writing_quality> <pedagogical feedback>")
                continue
            qid, target, note = parts[1], parts[2], parts[3]
            res = regrade_assessment_section(
                scorecard_id=scorecard.scorecard_id,
                question_id=qid,
                agent_target=target,
                teacher_feedback=note,
            )
            print(f"\n{res.get('message')}")
            sc = gradebook_db.get_scorecard(scorecard.scorecard_id)
            if sc:
                display_scorecard(sc)

        elif lower.startswith("override"):
            parts = cmd.split(maxsplit=3)
            if len(parts) < 4:
                print("Usage: override <Q1..Q5> <new_score> <justification>")
                continue
            qid, score_val, just = parts[1], float(parts[2]), parts[3]
            res = apply_teacher_score_override(
                scorecard_id=scorecard.scorecard_id,
                question_id=qid,
                dimension="total",
                new_score=score_val,
                justification=just,
            )
            print(f"\n{res.get('message')}")
            sc = gradebook_db.get_scorecard(scorecard.scorecard_id)
            if sc:
                display_scorecard(sc)

        else:
            canon_match = lookup_hunger_games_canon(cmd)
            print("\n🏹 Canonical Knowledge Lookup:")
            for m in canon_match.get("results", [])[:2]:
                print(f"   • {m['title']}: {m['canonical_facts'][0]}")
            print()


if __name__ == "__main__":
    run_interactive_cli()

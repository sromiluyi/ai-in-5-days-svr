"""Automated Evaluation Harness for Agent Regression Testing.

Runs the assessment pipeline against tests/golden_dataset.json to statically
measure agent accuracy, scoring tolerances, and Human-in-the-Loop trigger regressions.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from app.agents.scorecard_agent import synthesize_exam_scorecard
from app.cli import evaluate_submission_offline
from app.tools.anonymizer_tool import mask_student_identifiers


@pytest.fixture(scope="module")
def golden_dataset():
    data_path = Path(__file__).parent / "golden_dataset.json"
    assert data_path.exists(), "golden_dataset.json not found"
    return json.loads(data_path.read_text(encoding="utf-8"))


def test_golden_evaluation_dataset_harness(golden_dataset):
    """Execute evaluation harness over all golden dataset test cases."""
    repo_root = Path(__file__).parent.parent
    total_cases = len(golden_dataset)
    passed_cases = 0

    for case in golden_dataset:
        file_path = repo_root / case["submission_file"]
        assert file_path.exists(), f"Submission file {file_path} not found"

        raw_md = file_path.read_text(encoding="utf-8")

        # 1. PII Anonymization
        anon_result = mask_student_identifiers(raw_md)
        assert anon_result["status"] == "success"
        token = anon_result["student_token"]
        assert token.startswith("STUDENT_ANON_")
        assert case["student_name"] not in str(anon_result["submission"])
        assert case["student_id"] not in str(anon_result["submission"])

        # 2. Evaluation
        eval_items = evaluate_submission_offline(anon_result["submission"])
        assert len(eval_items) == 5, f"Expected 5 questions evaluated, got {len(eval_items)}"

        # 3. Scorecard Synthesis & HITL Validation
        scorecard = synthesize_exam_scorecard(token, eval_items)

        # Assert score falls within expected golden range
        assert case["expected_min_score"] <= scorecard.overall_percentage <= case["expected_max_score"], (
            f"Case {case['submission_file']}: score {scorecard.overall_percentage:.1f}% "
            f"outside expected range [{case['expected_min_score']}, {case['expected_max_score']}]"
        )

        # Assert letter grade matches
        assert scorecard.letter_grade == case["expected_grade"], (
            f"Case {case['submission_file']}: expected grade {case['expected_grade']}, got {scorecard.letter_grade}"
        )

        # Assert subscore benchmarks
        assert scorecard.correctness_percentage >= case["expected_correctness_min"], (
            f"Correctness {scorecard.correctness_percentage:.1f}% below min {case['expected_correctness_min']}%"
        )
        assert scorecard.quality_percentage >= case["expected_quality_min"], (
            f"Writing quality {scorecard.quality_percentage:.1f}% below min {case['expected_quality_min']}%"
        )

        # Assert HITL Review triggers
        if case["expected_hitl_flag"]:
            assert scorecard.hitL_review.is_flagged is True, (
                f"Case {case['submission_file']} expected HITL review flag but was not flagged"
            )
            assert any(case["expected_hitl_reason"] in r for r in scorecard.hitL_review.flag_reasons), (
                f"Expected reason {case['expected_hitl_reason']} in {scorecard.hitL_review.flag_reasons}"
            )
        else:
            assert scorecard.hitL_review.is_flagged is False

        passed_cases += 1

    accuracy = (passed_cases / total_cases) * 100.0
    print(f"\n[Golden Evaluation Harness] Accuracy: {accuracy:.1f}% ({passed_cases}/{total_cases} passed)")
    assert accuracy == 100.0


def test_teacher_review_eval_dataset_schema_and_cases():
    """Verify that tests/eval/datasets/teacher-review-dataset.json conforms to EvalCase schema."""
    dataset_file = Path(__file__).parent / "eval" / "datasets" / "teacher-review-dataset.json"
    assert dataset_file.exists(), "teacher-review-dataset.json not found"

    dataset = json.loads(dataset_file.read_text(encoding="utf-8"))
    assert "eval_cases" in dataset
    cases = dataset["eval_cases"]
    assert len(cases) >= 3

    expected_case_ids = {
        "teacher_direct_score_override_action_first",
        "teacher_targeted_regrade_action_first",
        "teacher_unspecified_question_clarification",
    }
    actual_case_ids = {c["eval_case_id"] for c in cases}
    assert expected_case_ids.issubset(actual_case_ids)

    for case in cases:
        # Validate multi-turn structure (Shape B continued conversation)
        assert "eval_case_id" in case
        assert "agent_data" in case
        agent_data = case["agent_data"]
        assert "turns" in agent_data
        assert len(agent_data["turns"]) >= 1

        last_turn = agent_data["turns"][-1]
        events = last_turn["events"]
        # Last event must be the user prompt / instruction triggering the next agent action
        assert events[-1]["author"] == "user"
        user_text = events[-1]["content"]["parts"][0]["text"]
        assert len(user_text) > 0

        # Validate reference ground truth
        assert "reference" in case
        assert "response" in case["reference"]

        # Validate rubric groups
        assert "rubric_groups" in case
        rubric_groups = case["rubric_groups"]
        assert len(rubric_groups) > 0
        for group_name, group in rubric_groups.items():
            assert "rubrics" in group
            for rubric in group["rubrics"]:
                assert "rubric_id" in rubric
                assert "description" in rubric
                assert len(rubric["description"]) > 10


"""Tests for Category 1: Tool & Interface Design.

Validates:
1. Comprehensive Tool Docstrings (detailed docstrings on all tool functions).
2. Descriptive Naming (explicit, self-documenting naming conventions).
3. Explicit JSON Schemas (strict Pydantic input and output validation).
4. Guided Error Handling (structured recovery instructions returned on errors).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import (
    CorrectnessEvaluation,
    QualityEvaluation,
    QuestionAnswer,
    ToolRecoveryResponse,
)
from app.mcp_server.canon_server import (
    fetch_exam_rubric_criteria,
    get_exemplar_answer,
    lookup_hunger_games_canon,
)
from app.tools.anonymizer_tool import mask_student_identifiers, restore_student_identity_vault
from app.tools.hitl_tools import finalize_student_grade_record
from app.tools.regrade_tools import apply_teacher_score_override, regrade_assessment_section


def test_tool_docstrings_comprehensive():
    """Verify all tool functions contain human-readable purpose, args, and returns."""
    tools = [
        mask_student_identifiers,
        restore_student_identity_vault,
        finalize_student_grade_record,
        regrade_assessment_section,
        apply_teacher_score_override,
        lookup_hunger_games_canon,
        fetch_exam_rubric_criteria,
        get_exemplar_answer,
    ]

    for tool in tools:
        doc = tool.__doc__
        assert doc is not None, f"Tool {tool.__name__} missing docstring"
        assert len(doc.strip()) > 30, f"Tool {tool.__name__} docstring is too short"
        assert "Args:" in doc, f"Tool {tool.__name__} docstring missing 'Args:' section"
        assert "Returns:" in doc, f"Tool {tool.__name__} docstring missing 'Returns:' section"


def test_tool_descriptive_naming():
    """Verify tool names follow descriptive, unambiguous conventions."""
    tool_names = [
        "mask_student_identifiers",
        "restore_student_identity_vault",
        "finalize_student_grade_record",
        "regrade_assessment_section",
        "apply_teacher_score_override",
        "lookup_hunger_games_canon",
        "fetch_exam_rubric_criteria",
    ]

    for name in tool_names:
        assert len(name) > 10
        assert "_" in name


def test_explicit_json_schemas_validation():
    """Verify strict Pydantic schemas reject invalid payloads and validate constraints."""
    # Valid model instantiation
    valid_qa = QuestionAnswer(
        question_id="Q1",
        question_type="short_answer",
        question_prompt="What act does Katniss perform?",
        student_response="She volunteers for Prim.",
    )
    assert valid_qa.question_id == "Q1"

    # Invalid question_id pattern
    with pytest.raises(ValidationError):
        QuestionAnswer(
            question_id="Invalid ID With Spaces!",
            question_type="short_answer",
            question_prompt="Prompt",
            student_response="Response",
        )

    # Score cannot exceed max_score
    with pytest.raises(ValidationError):
        CorrectnessEvaluation(
            question_id="Q1",
            score=15.0,
            max_score=5.0,  # score > max_score violates validator
            is_fully_correct=True,
            justification="Exceeded maximum possible score.",
        )


def test_guided_error_handling_invalid_inputs():
    """Verify tools return descriptive recovery instructions back to the LLM on error."""
    # 1. Invalid question ID in rubric fetch
    bad_rubric_res = fetch_exam_rubric_criteria("INVALID_Q99")
    assert bad_rubric_res["status"] == "error"
    assert bad_rubric_res["error_code"] == "INVALID_QUESTION_ID"
    assert "recovery_guidance" in bad_rubric_res
    assert "valid_questions" in bad_rubric_res
    assert "Q1" in bad_rubric_res["valid_questions"]

    # 2. Invalid agent target in regrade tool
    bad_regrade_res = regrade_assessment_section(
        scorecard_id="dummy",
        question_id="Q1",
        agent_target="non_existent_specialist",
        teacher_feedback="note",
    )
    assert bad_regrade_res["status"] == "error"
    assert bad_regrade_res["error_code"] == "INVALID_AGENT_TARGET"
    assert "correctness" in bad_regrade_res["recovery_guidance"]

    # 3. Empty submission markdown
    bad_mask_res = mask_student_identifiers("")
    assert bad_mask_res["status"] == "error"
    assert bad_mask_res["error_code"] == "INVALID_SUBMISSION_FORMAT"
    assert "recovery_guidance" in bad_mask_res


def test_pii_vault_anonymization_and_unmask():
    """Verify two-way tokenized vault isolates student identity and prevents leaks."""
    raw_markdown = """# Student Submission
- **Student Name**: Katniss Test
- **Student ID**: ID-9988

### Question 1: Test Prompt
Test student response.
"""
    anon_res = mask_student_identifiers(raw_markdown)
    assert anon_res["status"] == "success"
    token = anon_res["student_token"]
    assert token.startswith("STUDENT_ANON_")
    assert "Katniss Test" not in str(anon_res["submission"])
    assert "ID-9988" not in str(anon_res["submission"])

    # Unauthorized unmask rejected
    unauth = restore_student_identity_vault(token, teacher_auth=False)
    assert unauth["status"] == "error"
    assert unauth["error_code"] == "UNAUTHORIZED_UNMASK_ATTEMPT"

    # Authorized unmask succeeds
    auth = restore_student_identity_vault(token, teacher_auth=True)
    assert auth["status"] == "success"
    assert auth["student_name"] == "Katniss Test"
    assert auth["student_id"] == "ID-9988"

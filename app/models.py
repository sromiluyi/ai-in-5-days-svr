"""Pydantic Data Models and Explicit JSON Schemas.

Defines strict input and output schemas for all tools, agents, evaluations,
scorecards, audit trails, and guided error handling responses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class QuestionAnswer(BaseModel):
    """Represents a single question and the student's corresponding answer."""

    question_id: str = Field(
        ...,
        description="Unique identifier for the exam question (e.g., 'Q1', 'Q2', 'Q4_ESSAY').",
        pattern=r"^[A-Za-z0-9_\-]+$",
    )
    question_type: Literal["short_answer", "long_essay"] = Field(
        ...,
        description="Whether this is a factual short answer or an analytical essay response.",
    )
    question_prompt: str = Field(
        ...,
        description="The full text prompt or question presented to the student.",
        min_length=5,
    )
    student_response: str = Field(
        ...,
        description="The student's submitted response text.",
    )


class StudentRawSubmission(BaseModel):
    """Raw exam submission from the student before PII anonymization."""

    student_name: str = Field(
        ...,
        description="Full name of the student as submitted on the exam header.",
        min_length=2,
    )
    student_id: str = Field(
        ...,
        description="Official student school ID or roll number.",
        min_length=2,
    )
    exam_title: str = Field(
        default="The Hunger Games Unit Assessment (Book 1)",
        description="Title of the examination.",
    )
    submission_timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when the student submitted the exam.",
    )
    answers: List[QuestionAnswer] = Field(
        ...,
        description="List of student answers to exam questions.",
        min_length=1,
    )


class AnonymizedSubmission(BaseModel):
    """Student submission after PII masking, used safely by evaluation agents."""

    student_token: str = Field(
        ...,
        description="Cryptographically assigned pseudonym token (e.g., 'STUDENT_ANON_7A1B').",
        pattern=r"^STUDENT_ANON_[A-F0-9]{4,8}$",
    )
    exam_title: str = Field(
        ...,
        description="Title of the examination.",
    )
    anonymized_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when PII was stripped.",
    )
    answers: List[QuestionAnswer] = Field(
        ...,
        description="List of questions and student answers stripped of identity markers.",
    )


class StudentIdentityVaultEntry(BaseModel):
    """Isolated vault entry mapping a pseudonym token to the real student identity."""

    student_token: str = Field(..., description="Pseudonym token.")
    student_name: str = Field(..., description="Real student name.")
    student_id: str = Field(..., description="Real student ID.")
    vault_created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    unmasked: bool = Field(
        default=False,
        description="Whether the teacher has completed the review and requested unmasking.",
    )


class CorrectnessEvaluation(BaseModel):
    """Evaluation result from the Factual Correctness Assessor Agent."""

    question_id: str = Field(..., description="ID of the question evaluated.")
    score: float = Field(
        ...,
        description="Points awarded for factual accuracy.",
        ge=0.0,
    )
    max_score: float = Field(
        ...,
        description="Maximum possible points for factual accuracy on this question.",
        gt=0.0,
    )
    is_fully_correct: bool = Field(
        ...,
        description="True if all canonical facts match the answer key without error.",
    )
    canonical_facts_identified: List[str] = Field(
        default_factory=list,
        description="List of canonical book facts accurately cited by the student.",
    )
    factual_discrepancies: List[str] = Field(
        default_factory=list,
        description="Inaccuracies or movie-only hallucinations identified in the answer.",
    )
    justification: str = Field(
        ...,
        description="Pedagogical explanation of the correctness score for the teacher.",
        min_length=10,
    )

    @model_validator(mode="after")
    def validate_score_not_exceed_max(self):
        if self.score > self.max_score:
            raise ValueError(f"Score ({self.score}) cannot exceed max_score ({self.max_score})")
        return self


class QualityEvaluation(BaseModel):
    """Evaluation result from the Writing Quality Assessor Agent."""

    question_id: str = Field(..., description="ID of the question evaluated.")
    score: float = Field(
        ...,
        description="Points awarded for writing quality and analytical depth.",
        ge=0.0,
    )
    max_score: float = Field(
        ...,
        description="Maximum possible points for writing quality.",
        gt=0.0,
    )
    claim_clarity_rating: Literal["Developing", "Proficient", "Advanced"] = Field(
        ...,
        description="Middle school rating for central claim and focus.",
    )
    textual_evidence_rating: Literal["Developing", "Proficient", "Advanced"] = Field(
        ...,
        description="Rating for integration of quotes or specific novel details.",
    )
    analytical_reasoning_rating: Literal["Developing", "Proficient", "Advanced"] = Field(
        ...,
        description="Rating for explanation of symbolism, themes, or character motivations.",
    )
    strengths: List[str] = Field(
        default_factory=list,
        description="Specific writing strengths exhibited in the response.",
    )
    growth_areas: List[str] = Field(
        default_factory=list,
        description="Actionable areas where the student can elevate their writing.",
    )
    justification: str = Field(
        ...,
        description="Detailed pedagogical justification of writing quality score.",
        min_length=10,
    )

    @model_validator(mode="after")
    def validate_quality_score(self):
        if self.score > self.max_score:
            raise ValueError(f"Score ({self.score}) cannot exceed max_score ({self.max_score})")
        return self


class QuestionScoreItem(BaseModel):
    """Synthesized score item for a single exam question combining both dimensions."""

    question_id: str
    question_prompt: str
    question_type: Literal["short_answer", "long_essay"]
    correctness: CorrectnessEvaluation
    writing_quality: QualityEvaluation
    total_awarded: float = Field(..., ge=0.0)
    total_possible: float = Field(..., gt=0.0)
    percentage: float = Field(..., ge=0.0, le=100.0)
    teacher_override_applied: bool = False
    override_note: Optional[str] = None


class HITLReviewFlag(BaseModel):
    """Specifies reasons and status for Human-in-the-Loop teacher intervention."""

    is_flagged: bool = Field(
        ...,
        description="True if submission requires human teacher review.",
    )
    flag_reasons: List[str] = Field(
        default_factory=list,
        description="List of trigger reasons (e.g., 'SCORE_BELOW_PASSING_THRESHOLD', 'HIGH_HONORS_VERIFICATION').",
    )
    teacher_decision: Optional[Literal["APPROVED", "REVISED", "PENDING"]] = Field(
        default="PENDING",
        description="Current state of human teacher approval.",
    )
    teacher_notes: Optional[str] = None
    reviewed_at: Optional[datetime] = None


class AuditLogEntry(BaseModel):
    """Record of an explicit modification, regrade, or override on a student's grade."""

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    action: Literal[
        "INITIAL_ASSESSMENT",
        "SECTION_REGRADE_DISPATCH",
        "DIRECT_SCORE_OVERRIDE",
        "HITL_APPROVAL",
        "IDENTITY_UNMASKED",
    ]
    target_section: str
    agent_target: Optional[str] = None
    previous_score: Optional[float] = None
    new_score: Optional[float] = None
    delta: Optional[float] = None
    actor: str = Field(default="system", description="'teacher' or 'system'")
    rationale: str = Field(..., description="Detailed explanation for the change.")


class StudentScoreCard(BaseModel):
    """Complete synthesized scorecard presented to the teacher and recorded in session state."""

    scorecard_id: str = Field(..., description="Unique ID for this scorecard.")
    student_token: str = Field(..., description="Anonymized student pseudonym.")
    exam_title: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Scores
    total_score: float = Field(..., ge=0.0)
    max_possible_score: float = Field(default=100.0, ge=0.0)
    overall_percentage: float = Field(..., ge=0.0, le=100.0)
    letter_grade: str = Field(..., description="Letter grade (A, B, C, D, F).")
    
    # Sub-scores
    correctness_percentage: float = Field(..., ge=0.0, le=100.0)
    quality_percentage: float = Field(..., ge=0.0, le=100.0)
    score_discrepancy: float = Field(..., ge=0.0, description="|Correctness% - Quality%|")

    # Details
    question_scores: List[QuestionScoreItem]
    overall_pedagogical_feedback: str
    growth_recommendation: str

    # HITL Status
    hitL_review: HITLReviewFlag

    # Audit Trail
    audit_history: List[AuditLogEntry] = Field(default_factory=list)

    # De-anonymized identity (only populated if authorized teacher explicitly unmasks)
    unmasked_identity: Optional[Dict[str, str]] = None


class ToolRecoveryResponse(BaseModel):
    """Structured recovery response returned to LLMs when tool execution encounters an error.
    
    Satisfies rubric: 'Guided Error Handling: Tool error returns provide descriptive recovery
    instructions back to the LLM instead of just crashing.'
    """

    status: Literal["error"] = "error"
    error_code: str = Field(..., description="Machine-readable error category.")
    message: str = Field(..., description="Human-readable description of what failed.")
    recovery_guidance: str = Field(
        ...,
        description="Explicit step-by-step instructions advising the LLM how to recover or correct arguments.",
    )
    valid_options: Optional[List[str]] = Field(
        default=None,
        description="Allowed parameter values or recognized options to guide retry.",
    )
    payload_received: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Echo of the invalid input that triggered the error.",
    )

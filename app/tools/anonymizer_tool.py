"""PII Anonymization and Tokenized Vault Tool.

Enforces privacy and bias-free evaluation by extracting student Name and Student ID
into an isolated cryptographic vault and replacing them with a pseudonym token
(e.g., 'STUDENT_ANON_7A1B') before any grading agent processes the submission.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from typing import Any, Dict, Optional

from app.models import (
    AnonymizedSubmission,
    QuestionAnswer,
    StudentIdentityVaultEntry,
    ToolRecoveryResponse,
)
from app.observability.logger import log_intent, log_outcome


class StudentIdentityVault:
    """Isolated memory vault storing real student identity mapped to pseudonym tokens."""

    def __init__(self):
        self._vault: Dict[str, StudentIdentityVaultEntry] = {}

    def store(self, token: str, name: str, student_id: str) -> None:
        self._vault[token] = StudentIdentityVaultEntry(
            student_token=token,
            student_name=name,
            student_id=student_id,
            unmasked=False,
        )

    def lookup(self, token: str) -> Optional[StudentIdentityVaultEntry]:
        return self._vault.get(token)

    def unmask(self, token: str) -> Optional[Dict[str, str]]:
        entry = self._vault.get(token)
        if entry:
            entry.unmasked = True
            return {
                "student_name": entry.student_name,
                "student_id": entry.student_id,
                "student_token": entry.student_token,
            }
        return None

    def clear(self) -> None:
        self._vault.clear()


vault = StudentIdentityVault()


NAME_REGEX = re.compile(
    r"(?:\*\*Student Name\*\*|Student Name|Name)[:=]\s*([A-Za-z\s.'-]+?)(?:\n|\r|\*|$)",
    re.IGNORECASE,
)
ID_REGEX = re.compile(
    r"(?:\*\*Student ID\*\*|Student ID|ID)[:=]\s*([A-Za-z0-9\-_]+)",
    re.IGNORECASE,
)
QUESTION_BLOCK_REGEX = re.compile(
    r"###\s*(Question\s*([0-9A-Za-z_\-]+)[:.]?\s*([^\n]+))\n([\s\S]*?)(?=(?:###\s*Question|\Z))",
    re.IGNORECASE,
)


def mask_student_identifiers(submission_markdown: str) -> Dict[str, Any]:
    """Extract and mask student PII from exam submission markdown.

    Args:
        submission_markdown: Raw markdown string containing student header and answers.

    Returns:
        Dictionary with anonymized submission data or guided error recovery response.
    """
    log_intent("AnonymizerTool", "MASK_STUDENT_IDENTIFIERS", "raw_submission_markdown")

    if not isinstance(submission_markdown, str) or len(submission_markdown.strip()) < 10:
        log_outcome(
            "AnonymizerTool",
            "MASK_STUDENT_IDENTIFIERS",
            "FAILED",
            "Submission markdown empty or too short",
        )
        return ToolRecoveryResponse(
            error_code="INVALID_SUBMISSION_FORMAT",
            message="The provided submission markdown is empty or too short to contain exam questions.",
            recovery_guidance="Please provide a valid markdown submission containing student metadata and Question blocks.",
            valid_options=["Markdown with header: - **Student Name**: ... and ### Question blocks"],
        ).model_dump()

    # 1. Extract student Name
    name_match = NAME_REGEX.search(submission_markdown)
    student_name = name_match.group(1).strip() if name_match else "Unknown Student"

    # 2. Extract student ID
    id_match = ID_REGEX.search(submission_markdown)
    student_id = id_match.group(1).strip() if id_match else "UNKNOWN_ID"

    # 3. Generate deterministic yet isolated pseudonym token
    token_seed = f"{student_name}:{student_id}:{secrets.token_hex(4)}"
    token_hash = hashlib.sha256(token_seed.encode("utf-8")).hexdigest()[:6].upper()
    student_token = f"STUDENT_ANON_{token_hash}"

    # 4. Store in isolated vault
    vault.store(token=student_token, name=student_name, student_id=student_id)

    # 5. Extract questions and answers
    answers: list[QuestionAnswer] = []
    question_matches = list(QUESTION_BLOCK_REGEX.finditer(submission_markdown))

    if not question_matches:
        # Fallback question parser for simple formats
        lines = submission_markdown.splitlines()
        current_qid = None
        current_prompt = ""
        current_ans_lines = []

        for line in lines:
            if line.strip().startswith(("# Question", "## Question", "### Question")):
                if current_qid:
                    answers.append(
                        QuestionAnswer(
                            question_id=current_qid,
                            question_type="long_essay" if current_qid in ["Q4", "Q5"] else "short_answer",
                            question_prompt=current_prompt,
                            student_response="\n".join(current_ans_lines).strip(),
                        )
                    )
                parts = line.strip().split(":", 1)
                current_qid = parts[0].replace("#", "").strip()
                # Standardize to Q1, Q2, etc.
                if "1" in current_qid:
                    current_qid = "Q1"
                elif "2" in current_qid:
                    current_qid = "Q2"
                elif "3" in current_qid:
                    current_qid = "Q3"
                elif "4" in current_qid:
                    current_qid = "Q4"
                elif "5" in current_qid:
                    current_qid = "Q5"
                current_prompt = parts[1].strip() if len(parts) > 1 else "Question Prompt"
                current_ans_lines = []
            elif current_qid:
                current_ans_lines.append(line)

        if current_qid:
            answers.append(
                QuestionAnswer(
                    question_id=current_qid,
                    question_type="long_essay" if current_qid in ["Q4", "Q5"] else "short_answer",
                    question_prompt=current_prompt,
                    student_response="\n".join(current_ans_lines).strip(),
                )
            )
    else:
        for match in question_matches:
            qid_raw = match.group(2).strip().upper()
            prompt = match.group(3).strip()
            response_text = match.group(4).strip()
            # Strip any section headers accidentally captured before the next question block
            response_text = re.split(r"\n##+\s+", response_text)[0].strip()

            # Normalize QID
            if not qid_raw.startswith("Q"):
                qid = f"Q{qid_raw}"
            else:
                qid = qid_raw

            qtype = "long_essay" if qid in ["Q4", "Q5"] else "short_answer"

            answers.append(
                QuestionAnswer(
                    question_id=qid,
                    question_type=qtype,
                    question_prompt=prompt,
                    student_response=response_text,
                )
            )

    anonymized_submission = AnonymizedSubmission(
        student_token=student_token,
        exam_title="The Hunger Games Unit Assessment (Book 1)",
        answers=answers,
    )

    log_outcome(
        "AnonymizerTool",
        "MASK_STUDENT_IDENTIFIERS",
        "SUCCESS",
        f"Masked student identity into token '{student_token}' with {len(answers)} parsed answers",
        student_token=student_token,
    )

    return {
        "status": "success",
        "student_token": student_token,
        "submission": anonymized_submission.model_dump(),
    }


def restore_student_identity_vault(token: str, teacher_auth: bool = False) -> Dict[str, Any]:
    """Retrieve real student identity from the vault upon authorized teacher review.

    Args:
        token: The pseudonym token (e.g. 'STUDENT_ANON_E7B1').
        teacher_auth: Must be True to authorize de-masking.

    Returns:
        Dictionary with student identity or rejection response.
    """
    log_intent("AnonymizerTool", "RESTORE_IDENTITY", token, {"teacher_auth": teacher_auth})

    if not teacher_auth:
        return ToolRecoveryResponse(
            error_code="UNAUTHORIZED_UNMASK_ATTEMPT",
            message="Teacher authorization required to reveal student identity.",
            recovery_guidance="Pass teacher_auth=True after completing final grading review.",
        ).model_dump()

    entry = vault.unmask(token)
    if not entry:
        return ToolRecoveryResponse(
            error_code="TOKEN_NOT_FOUND_IN_VAULT",
            message=f"Pseudonym token '{token}' was not found in the identity vault.",
            recovery_guidance="Verify the token format. Valid tokens follow 'STUDENT_ANON_XXXX'.",
        ).model_dump()

    log_outcome("AnonymizerTool", "RESTORE_IDENTITY", "SUCCESS", f"Unmasked token '{token}'")
    return {
        "status": "success",
        "student_token": token,
        "student_name": entry["student_name"],
        "student_id": entry["student_id"],
    }

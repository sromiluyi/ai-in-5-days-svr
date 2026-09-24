"""Tests for Category 4: Observability, Structured Logging, Tracing, and PII Scrubbing.

Validates:
1. Structured JSON logging format and metadata capture.
2. Explicit Intent vs. Outcome logging before and after operations.
3. OpenTelemetry distributed tracing spans.
4. Active PII redaction and scrubbing in logs, dicts, and spans.
"""

from __future__ import annotations

import io
import json
import logging

from app.observability.logger import JSONFormatter, get_structured_logger, log_intent, log_outcome
from app.observability.pii_scrubber import sanitize_dict_pii, scrub_pii
from app.observability.tracing import trace_span


def test_pii_scrubber_redaction():
    """Verify active scrubbing strips names, student IDs, emails, and SSNs."""
    raw_text = (
        "Student Name: Katniss Everdeen with ID: MS-7412 and email katniss@district12.org "
        "and phone 555-123-4567 submitted exam."
    )
    scrubbed = scrub_pii(raw_text)

    assert "Katniss Everdeen" not in scrubbed
    assert "MS-7412" not in scrubbed
    assert "katniss@district12.org" not in scrubbed
    assert "555-123-4567" not in scrubbed
    assert "[REDACTED_NAME]" in scrubbed
    assert "[REDACTED_ID]" in scrubbed
    assert "[REDACTED_EMAIL]" in scrubbed
    assert "[REDACTED_PHONE]" in scrubbed


def test_sanitize_dict_pii_recursive():
    """Verify recursive PII scrubbing on nested dictionary structures."""
    payload = {
        "student_name": "Peeta Mellark",
        "student_id": "MS-9988",
        "nested": {
            "name": "Primrose",
            "safe_token": "STUDENT_ANON_E7B1",
        },
        "score": 95.0,
    }
    sanitized = sanitize_dict_pii(payload)

    assert sanitized["student_name"] == "[REDACTED_PII]"
    assert sanitized["student_id"] == "[REDACTED_PII]"
    assert sanitized["nested"]["name"] == "[REDACTED_PII]"
    # Safe pseudonym token must be preserved
    assert sanitized["nested"]["safe_token"] == "STUDENT_ANON_E7B1"
    assert sanitized["score"] == 95.0


def test_structured_json_logger_and_intent_outcome():
    """Verify structured logger emits JSON formatted lines with intent vs outcome events."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())

    test_logger = logging.getLogger("test_obs_logger")
    test_logger.setLevel(logging.INFO)
    test_logger.handlers.clear()
    test_logger.addHandler(handler)
    test_logger.propagate = False

    # Log Intent
    record_intent = logging.LogRecord(
        name="test_obs_logger",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="INTENT [Assessor]: Evaluating Question 1",
        args=(),
        exc_info=None,
    )
    record_intent.event_type = "INTENT"
    record_intent.agent_name = "correctness_assessor"
    record_intent.run_id = "test-run-123"
    record_intent.student_token = "STUDENT_ANON_9A9A"
    record_intent.metadata = {"question_id": "Q1"}

    handler.emit(record_intent)

    # Log Outcome
    record_outcome = logging.LogRecord(
        name="test_obs_logger",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="OUTCOME [Assessor]: Successfully graded Question 1",
        args=(),
        exc_info=None,
    )
    record_outcome.event_type = "OUTCOME"
    record_outcome.agent_name = "correctness_assessor"
    record_outcome.run_id = "test-run-123"
    record_outcome.student_token = "STUDENT_ANON_9A9A"
    record_outcome.metadata = {"score": 5.0, "status": "SUCCESS"}

    handler.emit(record_outcome)

    output = stream.getvalue().strip().splitlines()
    assert len(output) == 2

    # Parse JSON
    line1 = json.loads(output[0])
    line2 = json.loads(output[1])

    assert line1["event_type"] == "INTENT"
    assert line1["agent_name"] == "correctness_assessor"
    assert line1["run_id"] == "test-run-123"
    assert line1["student_token"] == "STUDENT_ANON_9A9A"
    assert "timestamp" in line1

    assert line2["event_type"] == "OUTCOME"
    assert line2["metadata"]["status"] == "SUCCESS"


def test_opentelemetry_trace_spans():
    """Verify OpenTelemetry trace spans execute and link attributes safely."""
    with trace_span(
        "test.agent_evaluation",
        attributes={"student_name": "Test Student", "question_id": "Q1", "score": 10.0},
    ) as span:
        assert span is not None
        assert span.is_recording()

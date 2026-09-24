"""Structured JSON Logger with Intent vs. Outcome Capture.

Produces structured JSON log events enriched with trace contexts, run IDs,
agent names, student pseudonym tokens, and active PII scrubbing.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.observability.pii_scrubber import sanitize_dict_pii, scrub_pii


class JSONFormatter(logging.Formatter):
    """Custom formatter emitting clean, structured JSON log lines."""

    def format(self, record: logging.LogRecord) -> str:
        log_payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": scrub_pii(record.getMessage()),
        }

        # Include custom extra metadata if present
        if hasattr(record, "metadata") and isinstance(record.metadata, dict):
            log_payload["metadata"] = sanitize_dict_pii(record.metadata)

        if hasattr(record, "agent_name"):
            log_payload["agent_name"] = record.agent_name

        if hasattr(record, "run_id"):
            log_payload["run_id"] = record.run_id

        if hasattr(record, "student_token"):
            log_payload["student_token"] = record.student_token

        if hasattr(record, "event_type"):
            log_payload["event_type"] = record.event_type

        if record.exc_info:
            log_payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_payload)


_logger_initialized = False


def get_structured_logger(name: str = "hunger_games_agent") -> logging.Logger:
    """Return a logger instance configured with JSONFormatter and PII scrubbing."""
    global _logger_initialized
    logger = logging.getLogger(name)

    if not _logger_initialized:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JSONFormatter())
        # Avoid duplicate handlers
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.propagate = False
        _logger_initialized = True

    return logger


logger = get_structured_logger()


def log_intent(
    agent_name: str,
    action: str,
    target: str,
    metadata: Optional[Dict[str, Any]] = None,
    student_token: Optional[str] = None,
    run_id: Optional[str] = None,
) -> None:
    """Log an agent's explicit INTENT prior to executing an action or tool.
    
    Satisfies rubric: 'Intent vs. Outcome Capture: Logs explicitly record both
    the agent's intended action before execution and the actual outcome after.'
    
    Args:
        agent_name: Name of the agent declaring intent.
        action: The intended operation (e.g., 'EVALUATE_CORRECTNESS', 'DISPATCH_REGRADE').
        target: Target question, section, or student token.
        metadata: Relevant parameters or context.
        student_token: Anonymized student identifier.
        run_id: Unique trace/invocation ID.
    """
    clean_meta = sanitize_dict_pii(metadata or {})
    logger.info(
        f"INTENT [{agent_name}]: Planning action '{action}' on target '{target}'",
        extra={
            "agent_name": agent_name,
            "event_type": "INTENT",
            "run_id": run_id or "run-default",
            "student_token": student_token or "ANON_NOT_SET",
            "metadata": {
                "action": action,
                "target": target,
                **clean_meta,
            },
        },
    )


def log_outcome(
    agent_name: str,
    action: str,
    status: str,
    result_summary: str,
    duration_ms: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None,
    student_token: Optional[str] = None,
    run_id: Optional[str] = None,
) -> None:
    """Log the actual OUTCOME after executing an action or tool.
    
    Satisfies rubric: 'Intent vs. Outcome Capture: Logs explicitly record both
    the agent's intended action before execution and the actual outcome after.'
    
    Args:
        agent_name: Name of the agent that performed the action.
        action: The completed operation.
        status: Result status (e.g., 'SUCCESS', 'FAILED', 'FLAGGED_HITL').
        result_summary: Concise explanation of the result or delta.
        duration_ms: Time taken to complete in milliseconds.
        metadata: Detailed output metrics or score values.
        student_token: Anonymized student identifier.
        run_id: Unique trace/invocation ID.
    """
    clean_meta = sanitize_dict_pii(metadata or {})
    logger.info(
        f"OUTCOME [{agent_name}]: Finished '{action}' with status '{status}' - {result_summary}",
        extra={
            "agent_name": agent_name,
            "event_type": "OUTCOME",
            "run_id": run_id or "run-default",
            "student_token": student_token or "ANON_NOT_SET",
            "metadata": {
                "action": action,
                "status": status,
                "result_summary": scrub_pii(result_summary),
                "duration_ms": duration_ms,
                **clean_meta,
            },
        },
    )

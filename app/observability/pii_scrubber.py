"""PII Scrubbing and Redaction Pipeline.

Protects sensitive student information by actively scrubbing names, student IDs,
emails, phone numbers, and other identifying patterns before logging, tracing,
or storing events in persistent memory.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Union

# Common PII Regex Patterns
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b")
PHONE_PATTERN = re.compile(r"\b(?:\+?1[-. ]?)?\(?[0-9]{3}\)?[-. ]?[0-9]{3}[-. ]?[0-9]{4}\b")
STUDENT_ID_PATTERN = re.compile(
    r"\b(?:STUDENT[-_ ]?ID|ID)[:=]?\s*([A-Za-z0-9\-_]{3,15})\b", re.IGNORECASE
)
NAME_HEADER_PATTERN = re.compile(
    r"(?:Student Name|Name)[:=]\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", re.IGNORECASE
)
SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def scrub_pii(text: str) -> str:
    """Scrub sensitive PII from a text string, replacing with redact placeholders.
    
    Args:
        text: The raw text string to sanitize.
        
    Returns:
        Sanitized string with student IDs, emails, phone numbers, and names redacted.
    """
    if not isinstance(text, str) or not text:
        return text

    sanitized = text
    sanitized = EMAIL_PATTERN.sub("[REDACTED_EMAIL]", sanitized)
    sanitized = PHONE_PATTERN.sub("[REDACTED_PHONE]", sanitized)
    sanitized = SSN_PATTERN.sub("[REDACTED_SSN]", sanitized)
    sanitized = STUDENT_ID_PATTERN.sub("STUDENT_ID: [REDACTED_ID]", sanitized)
    sanitized = NAME_HEADER_PATTERN.sub("Student Name: [REDACTED_NAME]", sanitized)

    return sanitized


def sanitize_dict_pii(data: Union[Dict[str, Any], List[Any], Any]) -> Any:
    """Recursively scrub PII across dictionary keys and nested values.
    
    Args:
        data: Arbitrary JSON-serializable structure (dict, list, primitive).
        
    Returns:
        A deeply sanitized copy of the structure with PII fields redacted.
    """
    sensitive_keys = {
        "student_name",
        "name",
        "student_id",
        "id",
        "ssn",
        "email",
        "phone",
        "phone_number",
    }

    if isinstance(data, dict):
        sanitized_dict = {}
        for key, value in data.items():
            if str(key).lower() in sensitive_keys and isinstance(value, str):
                # If it's already a safe pseudonym token, keep it
                if str(value).startswith("STUDENT_ANON_"):
                    sanitized_dict[key] = value
                else:
                    sanitized_dict[key] = "[REDACTED_PII]"
            else:
                sanitized_dict[key] = sanitize_dict_pii(value)
        return sanitized_dict
    elif isinstance(data, list):
        return [sanitize_dict_pii(item) for item in data]
    elif isinstance(data, str):
        return scrub_pii(data)
    else:
        return data

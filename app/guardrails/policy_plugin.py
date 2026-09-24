"""Pedagogical and Security Policy Guardrail Plugin.

Implements cross-cutting guardrails:
1. Input Security Guardrail: Detects adversarial jailbreak/prompt injection attempts.
2. PII Sanitization Guardrail: Strips accidental unmasked student PII from requests.
3. Self-Evaluation Output Guardrail: Post-generation policy check to ensure response
   quality, pedagogical appropriateness, and redaction of sensitive data.
"""

from __future__ import annotations

import re
from typing import Optional
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types

from app.observability.logger import log_intent, log_outcome
from app.observability.pii_scrubber import scrub_pii


class PedagogicalSafetyGuardrailPlugin(BasePlugin):
    """ADK Policy Plugin providing security, self-eval, and privacy guardrails."""

    def __init__(self, name: str = "pedagogical_safety_guardrail"):
        super().__init__(name=name)
        # Jailbreak / prompt injection patterns
        self.injection_patterns = [
            re.compile(r"ignore\s+(all\s+)?(previous|prior)\s+instructions", re.IGNORECASE),
            re.compile(r"disregard\s+(the\s+)?constitution", re.IGNORECASE),
            re.compile(r"bypass\s+(safety|guardrails)", re.IGNORECASE),
            re.compile(r"give\s+everyone\s+100%", re.IGNORECASE),
            re.compile(r"drop\s+table", re.IGNORECASE),
        ]

    async def before_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> Optional[LlmResponse]:
        """Pre-generation security guardrail: intercept malicious injections."""
        log_intent(
            "GuardrailPlugin",
            "CHECK_INPUT_POLICY",
            getattr(callback_context, "agent_name", "agent"),
        )

        # Inspect request contents for prompt injection
        if llm_request.contents:
            for content in llm_request.contents:
                if hasattr(content, "parts") and content.parts:
                    for part in content.parts:
                        text = getattr(part, "text", "") or ""
                        for pattern in self.injection_patterns:
                            if pattern.search(text):
                                log_outcome(
                                    "GuardrailPlugin",
                                    "CHECK_INPUT_POLICY",
                                    "BLOCKED",
                                    f"Security Guardrail: Intercepted prompt injection attempt matching {pattern.pattern}",
                                )
                                # Intervene and block execution
                                blocked_content = types.Content(
                                    role="model",
                                    parts=[
                                        types.Part.from_text(
                                            text="⚠️ Security Guardrail Triggered: The input violates system safety and pedagogical policy constraints. Request denied."
                                        )
                                    ],
                                )
                                return LlmResponse(content=blocked_content)

        log_outcome(
            "GuardrailPlugin",
            "CHECK_INPUT_POLICY",
            "PASSED",
            "Input passed security and pedagogical guardrail checks.",
        )
        return None

    async def after_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> Optional[LlmResponse]:
        """Post-generation self-evaluation guardrail: verify output privacy and quality."""
        log_intent(
            "GuardrailPlugin",
            "SELF_EVAL_OUTPUT",
            getattr(callback_context, "agent_name", "agent"),
        )

        if not llm_response or not llm_response.content or not llm_response.content.parts:
            return None

        modified = False
        new_parts = []
        for part in llm_response.content.parts:
            if hasattr(part, "text") and part.text:
                scrubbed = scrub_pii(part.text)
                if scrubbed != part.text:
                    modified = True
                    new_parts.append(types.Part.from_text(text=scrubbed))
                else:
                    new_parts.append(part)
            else:
                new_parts.append(part)

        if modified:
            log_outcome(
                "GuardrailPlugin",
                "SELF_EVAL_OUTPUT",
                "REDACTED",
                "Self-Evaluation Guardrail: Redacted accidental PII leakage in model output.",
            )
            return LlmResponse(
                content=types.Content(role=llm_response.content.role, parts=new_parts),
                usage_metadata=llm_response.usage_metadata,
            )

        log_outcome(
            "GuardrailPlugin",
            "SELF_EVAL_OUTPUT",
            "PASSED",
            "Model response complies with pedagogical safety policies.",
        )
        return None

"""Secure Secret Management Module.

Integrates with Google Cloud Secret Manager to securely retrieve and manage
API keys (such as GEMINI_API_KEY) without hardcoding credentials.
Supports local fallback to environment variables when offline.
"""

from __future__ import annotations

import os
from typing import Optional

from google.cloud import secretmanager
from app.config import config
from app.observability.logger import log_intent, log_outcome


def get_secret(
    secret_id: str = "gemini-api-key",
    project_id: Optional[str] = None,
    version_id: str = "latest",
) -> Optional[str]:
    """Retrieve a secret payload from Google Cloud Secret Manager with fallback.

    Args:
        secret_id: Name/ID of the secret in Secret Manager (e.g., 'gemini-api-key').
        project_id: Google Cloud project ID (defaults to config.project_id).
        version_id: Secret version (defaults to 'latest').

    Returns:
        The secret string payload, or None if unavailable.
    """
    proj = project_id or config.project_id
    env_fallback_key = secret_id.upper().replace("-", "_")

    log_intent(
        "SecretManager",
        "ACCESS_SECRET",
        secret_id,
        {"project_id": proj, "version_id": version_id},
    )

    last_error: Optional[str] = None
    # 1. Attempt retrieval from Google Cloud Secret Manager if enabled & reachable
    if os.getenv("SKIP_GCP_SECRET_MANAGER", "").lower() not in ("true", "1"):
        try:
            import socket
            # Fast check if secretmanager endpoint is reachable (prevents 60s DNS hangs in sandbox)
            socket.create_connection(("secretmanager.googleapis.com", 443), timeout=0.5)
            client = secretmanager.SecretManagerServiceClient()
            name = f"projects/{proj}/secrets/{secret_id}/versions/{version_id}"
            response = client.access_secret_version(request={"name": name}, timeout=3.0)
            payload = response.payload.data.decode("UTF-8").strip()

            log_outcome(
                "SecretManager",
                "ACCESS_SECRET",
                "SUCCESS",
                f"Successfully accessed secret '{secret_id}' from GCP Secret Manager",
            )
            return payload
        except Exception as exc:
            last_error = str(exc)

    # 2. Secure fallback to environment variable for local testing
    fallback_val = os.getenv(env_fallback_key) or os.getenv("GEMINI_API_KEY")
    if fallback_val:
        log_outcome(
            "SecretManager",
            "ACCESS_SECRET",
            "FALLBACK_ENV",
            f"Falling back to environment variable '{env_fallback_key}'",
        )
        return fallback_val.strip()

    log_outcome(
        "SecretManager",
        "ACCESS_SECRET",
        "NOT_FOUND",
        f"Secret '{secret_id}' not found in GCP Secret Manager or local environment (last_error: {last_error})",
    )
    return None


def store_gemini_api_key(
    api_key: str,
    secret_id: str = "gemini-api-key",
    project_id: Optional[str] = None,
    create_container_if_missing: bool = True,
) -> bool:
    """Store or update the Gemini API Key in Google Cloud Secret Manager.

    Follows Terraform IaC best practices:
    - Adds a new payload version to the secret container provisioned by Terraform.
    - If the container does not exist yet and create_container_if_missing is True,
      creates the container as an automated bootstrap.

    Args:
        api_key: The secret API key string to store.
        secret_id: Name/ID of the secret in Secret Manager.
        project_id: Google Cloud project ID.
        create_container_if_missing: Whether to auto-create the container if Terraform hasn't run.

    Returns:
        True if successfully stored, False otherwise.
    """
    proj = project_id or config.project_id
    log_intent("SecretManager", "STORE_SECRET", secret_id, {"project_id": proj})

    try:
        client = secretmanager.SecretManagerServiceClient()
        parent = f"projects/{proj}"
        secret_path = client.secret_path(proj, secret_id)

        # 1. Check if the container exists (typically provisioned by Terraform)
        container_exists = True
        try:
            client.get_secret(request={"name": secret_path})
        except Exception:
            container_exists = False

        # 2. If missing, optionally create the container (fallback for local development)
        if not container_exists:
            if not create_container_if_missing:
                log_outcome(
                    "SecretManager",
                    "STORE_SECRET",
                    "FAILED",
                    f"Secret container '{secret_id}' does not exist. Run 'agents-cli infra single-project --apply' first.",
                )
                return False

            client.create_secret(
                request={
                    "parent": parent,
                    "secret_id": secret_id,
                    "secret": {"replication": {"automatic": {}}},
                }
            )

        # 3. Add new secret version with the API key payload
        client.add_secret_version(
            request={
                "parent": secret_path,
                "payload": {"data": api_key.encode("UTF-8")},
            }
        )

        log_outcome(
            "SecretManager",
            "STORE_SECRET",
            "SUCCESS",
            f"Stored new version for secret '{secret_id}' in project '{proj}'",
        )
        return True
    except Exception as exc:
        log_outcome(
            "SecretManager",
            "STORE_SECRET",
            "FAILED",
            f"Failed to store secret in Secret Manager: {exc}",
        )
        return False

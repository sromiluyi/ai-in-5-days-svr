#!/usr/bin/env python3
"""CLI utility to manage the Gemini API key in Google Cloud Secret Manager.

Provides a secure, non-echoing prompt so that API keys are never written to
shell history (.bash_history) or hardcoded in configuration files.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import config
from app.secrets import get_secret, store_gemini_api_key


def set_secret_command(project_id: str, secret_id: str) -> None:
    print("\n" + "=" * 65)
    print("🔐 STORE GEMINI API KEY IN GOOGLE CLOUD SECRET MANAGER")
    print(f"   Project: {project_id}")
    print(f"   Secret:  {secret_id}")
    print("=" * 65)

    api_key = getpass.getpass("🔑 Enter your Gemini API Key (input will be hidden): ").strip()
    if not api_key:
        print("❌ Error: API key cannot be empty.")
        sys.exit(1)

    print("\n⏳ Uploading to Google Cloud Secret Manager...")
    success = store_gemini_api_key(api_key=api_key, secret_id=secret_id, project_id=project_id)

    if success:
        print(f"✅ Successfully stored '{secret_id}' in Google Cloud Secret Manager!")
        print("   The agent and workflow will now automatically retrieve this key at runtime.")
    else:
        print(f"❌ Failed to store secret in Secret Manager.")
        print("   Please ensure Google Cloud authentication is active:")
        print("     gcloud auth application-default login")
        print(f"   and that Secret Manager API is enabled on project '{project_id}':")
        print(f"     gcloud services enable secretmanager.googleapis.com --project {project_id}")
        sys.exit(1)


def get_secret_command(project_id: str, secret_id: str) -> None:
    print("\n" + "=" * 65)
    print("🔍 VERIFY GEMINI API KEY IN GOOGLE CLOUD SECRET MANAGER")
    print(f"   Project: {project_id}")
    print(f"   Secret:  {secret_id}")
    print("=" * 65)

    print("⏳ Accessing secret version 'latest'...")
    val = get_secret(secret_id=secret_id, project_id=project_id)

    if val:
        masked = val[:6] + "..." + val[-4:] if len(val) > 10 else "***"
        print(f"✅ Secret '{secret_id}' retrieved successfully!")
        print(f"   Masked Value: {masked} (length: {len(val)} characters)")
    else:
        print(f"⚠️ Secret '{secret_id}' could not be accessed.")
        print("   Run 'python scripts/manage_secrets.py set' to store it.")


def main():
    parser = argparse.ArgumentParser(description="Manage Gemini API Key in GCP Secret Manager.")
    parser.add_argument(
        "action",
        choices=["set", "get"],
        help="'set' to securely store key, 'get' to test retrieval",
    )
    parser.add_argument(
        "--project",
        default=config.project_id,
        help=f"GCP Project ID (default: {config.project_id})",
    )
    parser.add_argument(
        "--secret-id",
        default="gemini-api-key",
        help="Secret ID (default: gemini-api-key)",
    )

    args = parser.parse_args()

    if args.action == "set":
        set_secret_command(args.project, args.secret_id)
    elif args.action == "get":
        get_secret_command(args.project, args.secret_id)


if __name__ == "__main__":
    main()

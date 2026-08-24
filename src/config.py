"""Configuration management for the PR Review Bot.

Parses configuration from GitHub Action inputs (prefixed with INPUT_),
standard environment variables, and local .env files with sensible defaults.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load local .env if present
load_dotenv()


def get_env_var(name: str, default: str | None = None) -> str | None:
    """Retrieve environment variable, checking both GitHub Action INPUT_ prefix and standard name."""
    action_input_name = f"INPUT_{name.upper()}"
    val = os.getenv(action_input_name)
    if val is not None and val.strip() != "":
        return val.strip()
    standard_val = os.getenv(name.upper())
    if standard_val is not None and standard_val.strip() != "":
        return standard_val.strip()
    return default


class BotConfig(BaseModel):
    """Configuration options for the PR Review Bot."""

    # API Keys and Secrets
    groq_api_key: str = Field(
        default_factory=lambda: get_env_var("GROQ_API_KEY", "") or "",
        description="Groq Cloud API Key for LLM inference",
    )
    github_token: str = Field(
        default_factory=lambda: get_env_var("GITHUB_TOKEN", "") or "",
        description="GitHub personal access token or GITHUB_TOKEN from Actions",
    )

    # LLM Settings
    model: str = Field(
        default_factory=lambda: get_env_var("MODEL", "llama-3.3-70b-versatile")
        or "llama-3.3-70b-versatile",
        description="Groq model ID to use for code review",
    )
    temperature: float = Field(
        default=0.1,
        description="Sampling temperature (low temperature for deterministic code analysis)",
    )
    max_tool_calls: int = Field(
        default_factory=lambda: int(get_env_var("MAX_TOOL_CALLS", "15") or "15"),
        description="Safety ceiling on the total number of tool invocations per PR review",
    )

    # Review Behavior
    enable_inline_comments: bool = Field(
        default_factory=lambda: (get_env_var("ENABLE_INLINE_COMMENTS", "true") or "true").lower()
        in ("true", "1", "yes"),
        description="Whether to post inline comments directly on lines of code in the PR diff",
    )
    repo_root: Path = Field(
        default_factory=lambda: Path(os.getenv("GITHUB_WORKSPACE", os.getcwd())),
        description="Local root directory of the checked-out repository",
    )
    log_level: str = Field(
        default_factory=lambda: get_env_var("LOG_LEVEL", "INFO") or "INFO",
        description="Log output level",
    )

    # GitHub Context (populated automatically in GitHub Actions)
    github_repository: str | None = Field(
        default_factory=lambda: get_env_var("GITHUB_REPOSITORY", None),
        description="Repository in 'owner/repo' format",
    )
    github_event_path: str | None = Field(
        default_factory=lambda: get_env_var("GITHUB_EVENT_PATH", None),
        description="Path to the JSON file containing the GitHub event payload",
    )
    pr_number: int | None = Field(
        default=None,
        description="Explicit PR number (can be parsed from event payload)",
    )

    def validate_for_live_run(self) -> None:
        """Validate required secrets before connecting to external APIs."""
        if not self.groq_api_key:
            raise ValueError(
                "Missing GROQ_API_KEY. Please provide a valid Groq API key via env or action input."
            )
        if not self.github_token and self.github_repository:
            raise ValueError(
                "Missing GITHUB_TOKEN while GITHUB_REPOSITORY is set. Cannot authenticate with GitHub."
            )

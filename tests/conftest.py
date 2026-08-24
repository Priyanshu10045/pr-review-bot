"""Pytest configuration, shared fixtures, and API response mocks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.github_client.client import GitHubClient
from src.github_client.models import PRMetadata


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def clean_diff_text(fixtures_dir: Path) -> str:
    return (fixtures_dir / "clean_pr_diff.diff").read_text(encoding="utf-8")


@pytest.fixture
def buggy_diff_text(fixtures_dir: Path) -> str:
    return (fixtures_dir / "buggy_pr_diff.diff").read_text(encoding="utf-8")


@pytest.fixture
def sample_event_payload(fixtures_dir: Path) -> dict[str, Any]:
    return json.loads((fixtures_dir / "sample_event.json").read_text(encoding="utf-8"))


@pytest.fixture
def mock_pr_metadata() -> PRMetadata:
    return PRMetadata(
        number=101,
        title="feat: Add authentication and token batch processing",
        body="Implements direct SQL lookup for user authentication.",
        author="octocat",
        base_branch="main",
        head_branch="feature/auth-speedup",
        head_sha="6dcb09b5b57875f334f61aebed695e2e4193db5e",
        base_sha="f95f852cc5f5575addc8431e6f71fb376b21db52",
        changed_files_count=1,
        additions=18,
        deletions=3,
        labels=["enhancement"],
    )


@pytest.fixture
def mock_github_client(mock_pr_metadata: PRMetadata) -> GitHubClient:
    client = GitHubClient(mock_mode=True)
    return client


def make_mock_tool_call(
    tool_name: str, arguments: dict[str, Any], call_id: str = "call_123"
) -> MagicMock:
    """Helper to create a mock tool call object matching Groq/OpenAI structure."""
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = tool_name
    tc.function.arguments = json.dumps(arguments)
    return tc


def make_mock_completion(
    content: str | None = None,
    tool_calls: list[MagicMock] | None = None,
) -> MagicMock:
    """Helper to construct a mock Groq ChatCompletion response."""
    response = MagicMock()
    choice = MagicMock()
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls or []
    choice.message = message
    response.choices = [choice]
    return response

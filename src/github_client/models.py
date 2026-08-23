"""Data models representing GitHub PR entities, diffs, and review comments."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PRMetadata(BaseModel):
    """Metadata for a GitHub Pull Request."""

    number: int
    title: str
    body: str | None = ""
    author: str
    base_branch: str
    head_branch: str
    head_sha: str
    base_sha: str
    changed_files_count: int = 0
    additions: int = 0
    deletions: int = 0
    labels: list[str] = Field(default_factory=list)
    state: str = "open"


class DiffHunk(BaseModel):
    """Information about a modified file in the PR diff."""

    filename: str
    status: str  # "added", "modified", "removed", "renamed"
    additions: int
    deletions: int
    changes: int
    patch: str | None = None
    previous_filename: str | None = None


class InlineComment(BaseModel):
    """An inline comment anchored to a specific file and line."""

    path: str
    line: int
    body: str
    side: str = "RIGHT"  # "LEFT" for deleted lines, "RIGHT" for added/modified


class ReviewSummary(BaseModel):
    """The overall assessment and summary for the PR."""

    risk_level: str = "LOW"  # LOW, MEDIUM, HIGH
    summary_text: str
    checklist: list[str] = Field(default_factory=list)
    files_reviewed: list[str] = Field(default_factory=list)

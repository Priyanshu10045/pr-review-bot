"""Tool: get_pr_diff - fetches full diff for the PR."""

from __future__ import annotations

from typing import Any

from src.github_client.client import GitHubClient
from src.tools.base import BaseTool, ToolResult


class GetPRDiffTool(BaseTool):
    """Tool that retrieves the unified git diff of all files changed in the pull request."""

    name = "get_pr_diff"
    description = (
        "Fetches the complete unified git diff for the Pull Request. "
        "Call this first to inspect all modified, added, or deleted files, line changes, and diff hunks."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "max_lines": {
                "type": "integer",
                "description": "Optional maximum lines of diff to return to prevent overflowing context.",
                "default": 1000,
            }
        },
        "required": [],
    }

    def __init__(self, github_client: GitHubClient, pr_number: int, local_diff: str | None = None):
        self.github_client = github_client
        self.pr_number = pr_number
        self.local_diff = local_diff

    def execute(self, max_lines: int = 1000, **kwargs) -> ToolResult:
        """Fetch the PR diff."""
        try:
            if self.local_diff is not None:
                diff_text = self.local_diff
            else:
                diff_text = self.github_client.get_pr_diff(self.pr_number)

            if not diff_text.strip():
                return ToolResult(
                    success=True,
                    data="The pull request has an empty diff or contains only binary changes.",
                )

            lines = diff_text.splitlines()
            if len(lines) > max_lines:
                truncated = "\n".join(lines[:max_lines])
                truncated += f"\n\n[Diff truncated: showing {max_lines}/{len(lines)} lines. Use get_file_content for specific files.]"
                return ToolResult(success=True, data=truncated)

            return ToolResult(success=True, data=diff_text)
        except Exception as err:
            return ToolResult(success=False, error=f"Failed to fetch PR diff: {err}")

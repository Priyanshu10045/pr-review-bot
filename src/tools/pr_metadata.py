"""Tool: get_pr_metadata - retrieves PR title, description, branches, and author."""

from __future__ import annotations

from typing import Any

from src.github_client.client import GitHubClient
from src.github_client.models import PRMetadata
from src.tools.base import BaseTool, ToolResult


class GetPRMetadataTool(BaseTool):
    """Tool that retrieves the Pull Request title, description, author, and branch context."""

    name = "get_pr_metadata"
    description = (
        "Retrieves metadata about the Pull Request, including PR title, description body, "
        "author username, base/head branches, and total lines added/removed. "
        "Use this tool early to understand the intended purpose and scope of the PR."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def __init__(
        self,
        github_client: GitHubClient,
        pr_number: int,
        mock_metadata: PRMetadata | None = None,
    ):
        self.github_client = github_client
        self.pr_number = pr_number
        self.mock_metadata = mock_metadata

    def execute(self, **kwargs) -> ToolResult:
        """Fetch the PR metadata."""
        try:
            if self.mock_metadata is not None:
                metadata = self.mock_metadata
            else:
                metadata = self.github_client.get_pr_metadata(self.pr_number)

            data = {
                "pr_number": metadata.number,
                "title": metadata.title,
                "description": metadata.body,
                "author": metadata.author,
                "base_branch": metadata.base_branch,
                "head_branch": metadata.head_branch,
                "head_sha": metadata.head_sha,
                "changed_files_count": metadata.changed_files_count,
                "additions": metadata.additions,
                "deletions": metadata.deletions,
                "labels": metadata.labels,
            }
            return ToolResult(success=True, data=data)
        except Exception as err:
            return ToolResult(success=False, error=f"Failed to fetch PR metadata: {err}")

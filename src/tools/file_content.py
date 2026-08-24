"""Tool: get_file_content - fetches full content of a file at a given commit/branch or locally."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.github_client.client import GitHubClient
from src.tools.base import BaseTool, ToolResult


class GetFileContentTool(BaseTool):
    """Tool that retrieves the complete content of a file to understand surrounding context."""

    name = "get_file_content"
    description = (
        "Fetches the full text content of a file from the repository at a given ref (branch or commit SHA). "
        "Use this tool when the diff snippet alone is insufficient to understand class context, imports, or whole functions."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative file path from the repository root (e.g., 'src/api/auth.py').",
            },
            "ref": {
                "type": "string",
                "description": "Optional Git branch name or commit SHA. Defaults to the PR head commit.",
            },
            "max_lines": {
                "type": "integer",
                "description": "Optional max lines to fetch. Default is 500.",
                "default": 500,
            },
        },
        "required": ["path"],
    }

    def __init__(
        self,
        github_client: GitHubClient,
        repo_root: Path | None = None,
        default_ref: str | None = None,
    ):
        self.github_client = github_client
        self.repo_root = repo_root or Path.cwd()
        self.default_ref = default_ref

    def execute(
        self,
        path: str,
        ref: str | None = None,
        max_lines: int = 500,
        **kwargs,
    ) -> ToolResult:
        """Fetch the full content of a file."""
        target_ref = ref or self.default_ref
        try:
            # First attempt via GitHub Client
            if not self.github_client.mock_mode:
                content = self.github_client.get_file_content(path, target_ref)
            else:
                # Local fallback for offline simulation
                local_path = self.repo_root / path
                if local_path.is_file():
                    content = local_path.read_text(encoding="utf-8", errors="replace")
                else:
                    return ToolResult(
                        success=False,
                        error=f"File '{path}' not found in repository root '{self.repo_root}'.",
                    )

            lines = content.splitlines()
            if len(lines) > max_lines:
                numbered_lines = [
                    f"{i + 1:4d} | {line}" for i, line in enumerate(lines[:max_lines])
                ]
                formatted = "\n".join(numbered_lines)
                formatted += f"\n\n[File truncated: showing {max_lines}/{len(lines)} lines]"
            else:
                numbered_lines = [f"{i + 1:4d} | {line}" for i, line in enumerate(lines)]
                formatted = "\n".join(numbered_lines)

            return ToolResult(success=True, data=formatted)
        except Exception as err:
            return ToolResult(success=False, error=f"Failed to fetch content for '{path}': {err}")

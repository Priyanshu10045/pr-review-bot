"""Tool: post_inline_comment - posts or stages a line-specific code review finding."""

from __future__ import annotations

import logging
from typing import Any

from src.github_client.client import GitHubClient
from src.github_client.models import InlineComment
from src.tools.base import BaseTool, ToolResult
from src.utils.diff_parser import DiffParser

logger = logging.getLogger("PRReviewBot.PostInlineCommentTool")


class PostInlineCommentTool(BaseTool):
    """Tool that creates an inline review comment on a specific file and line."""

    name = "post_inline_comment"
    description = (
        "Posts an inline review comment anchored to a specific file and line number in the PR diff. "
        "Use this for targeted findings: logic bugs, security vulnerabilities, edge-case null checks, "
        "missing error handlers, or concrete code improvements. Use GitHub suggestion blocks when recommending code changes."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file": {
                "type": "string",
                "description": "The exact relative file path where the issue was found (e.g., 'src/auth/jwt.py').",
            },
            "line": {
                "type": "integer",
                "description": "The exact line number in the new/modified file where the comment should be placed.",
            },
            "comment": {
                "type": "string",
                "description": "The constructive review comment explaining the issue, why it matters, and a suggested fix.",
            },
            "side": {
                "type": "string",
                "enum": ["RIGHT", "LEFT"],
                "description": "Optional side of the diff. Use 'RIGHT' for added/modified lines (default), 'LEFT' for deleted lines.",
                "default": "RIGHT",
            },
        },
        "required": ["file", "line", "comment"],
    }

    def __init__(
        self,
        github_client: GitHubClient,
        pr_number: int,
        head_sha: str,
        staged_comments: list[InlineComment] | None = None,
        immediate_post: bool = False,
        diff_text: str | None = None,
    ):
        self.github_client = github_client
        self.pr_number = pr_number
        self.head_sha = head_sha
        self.staged_comments = staged_comments if staged_comments is not None else []
        self.immediate_post = immediate_post
        self.diff_text = diff_text

    def _get_diff(self) -> str | None:
        """Lazily resolve the PR diff for validation."""
        if self.diff_text:
            return self.diff_text
        if self.github_client and not getattr(self.github_client, "mock_mode", False):
            try:
                self.diff_text = self.github_client.get_pr_diff(self.pr_number)
                return self.diff_text
            except Exception as err:
                logger.warning("Could not fetch PR diff for line validation: %s", err)
                return None
        return None

    def execute(
        self,
        file: str,
        line: int,
        comment: str,
        side: str = "RIGHT",
        **kwargs,
    ) -> ToolResult:
        """Stage or post an inline review comment with diff hunk boundary validation."""
        if not file or not file.strip():
            return ToolResult(success=False, error="Parameter 'file' cannot be empty.")
        if not isinstance(line, int) or line <= 0:
            return ToolResult(
                success=False, error=f"Invalid line number '{line}'. Must be a positive integer."
            )
        if not comment or not comment.strip():
            return ToolResult(success=False, error="Parameter 'comment' cannot be empty.")

        clean_file = file.strip().lstrip("./\\")
        clean_comment = comment.strip()

        # Validate line against diff hunks if diff is accessible
        diff = self._get_diff()
        if diff and diff.strip():
            changed_files = DiffParser.get_changed_files(diff)
            clean_changed = [f.lstrip("./\\") for f in changed_files]

            if clean_changed and clean_file not in clean_changed:
                return ToolResult(
                    success=False,
                    error=(
                        f"File '{clean_file}' is not modified in PR #{self.pr_number}. "
                        f"Modified files in this PR: {clean_changed}"
                    ),
                )

            valid_lines = DiffParser.get_commentable_lines(diff, clean_file)
            if valid_lines and line not in valid_lines:
                sorted_valid = sorted(list(valid_lines))
                preview = sorted_valid[:15]
                more_suffix = f" (and {len(sorted_valid) - 15} more)" if len(sorted_valid) > 15 else ""
                return ToolResult(
                    success=False,
                    error=(
                        f"Line {line} in '{clean_file}' is outside the modified diff hunks. "
                        f"GitHub review comments must be anchored to modified/added lines. "
                        f"Valid commentable lines for '{clean_file}': {preview}{more_suffix}."
                    ),
                )

        inline_obj = InlineComment(path=clean_file, line=line, body=clean_comment, side=side)
        self.staged_comments.append(inline_obj)

        if self.immediate_post:
            try:
                self.github_client.post_inline_comment(
                    pr_number=self.pr_number,
                    commit_sha=self.head_sha,
                    path=clean_file,
                    line=line,
                    body=clean_comment,
                    side=side,
                )
                return ToolResult(
                    success=True,
                    data=f"Successfully posted inline comment on {clean_file}:{line}.",
                )
            except Exception as err:
                logger.error("Failed to post inline comment immediately: %s", err)
                return ToolResult(
                    success=False,
                    error=f"Failed to post inline comment on {clean_file}:{line}: {err}",
                )

        return ToolResult(
            success=True,
            data=f"Inline comment staged for {clean_file}:{line}. It will be submitted with the final review batch.",
        )

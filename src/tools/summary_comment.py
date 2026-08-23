"""Tool: post_summary_comment - posts or stages the overall PR review assessment."""

from __future__ import annotations

import logging
from typing import Any

from src.github_client.client import GitHubClient
from src.github_client.models import ReviewSummary
from src.tools.base import BaseTool, ToolResult

logger = logging.getLogger("PRReviewBot.PostSummaryCommentTool")


class PostSummaryCommentTool(BaseTool):
    """Tool that records the final overall Pull Request summary review."""

    name = "post_summary_comment"
    description = (
        "Posts the comprehensive summary review for the Pull Request. "
        "Must include an executive summary of changes, overall risk assessment (LOW, MEDIUM, HIGH), "
        "and a checklist of items human reviewers should verify before merging. "
        "Calling this tool completes the review process."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "summary_text": {
                "type": "string",
                "description": "Executive summary of the PR, key changes, and design observations.",
            },
            "risk_level": {
                "type": "string",
                "enum": ["LOW", "MEDIUM", "HIGH"],
                "description": "Overall risk level assessment based on bugs, security, test coverage, or complexity.",
                "default": "LOW",
            },
            "checklist": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of specific verification items or edge cases for human reviewers to check.",
            },
        },
        "required": ["summary_text", "risk_level"],
    }

    def __init__(
        self,
        github_client: GitHubClient,
        pr_number: int,
        immediate_post: bool = False,
    ):
        self.github_client = github_client
        self.pr_number = pr_number
        self.immediate_post = immediate_post
        self.recorded_summary: ReviewSummary | None = None

    def execute(
        self,
        summary_text: str,
        risk_level: str = "LOW",
        checklist: list[str] | None = None,
        **kwargs,
    ) -> ToolResult:
        """Record and format the PR summary review."""
        if not summary_text or not summary_text.strip():
            return ToolResult(success=False, error="Parameter 'summary_text' cannot be empty.")

        checklist_items = checklist or []
        risk_level = risk_level.upper()
        if risk_level not in ("LOW", "MEDIUM", "HIGH"):
            risk_level = "LOW"

        self.recorded_summary = ReviewSummary(
            risk_level=risk_level,
            summary_text=summary_text.strip(),
            checklist=checklist_items,
        )

        formatted_comment = self.format_markdown(self.recorded_summary)

        if self.immediate_post:
            try:
                self.github_client.post_summary_comment(self.pr_number, formatted_comment)
                return ToolResult(
                    success=True,
                    data="Successfully posted PR summary review comment.",
                )
            except Exception as err:
                logger.error("Failed to post summary comment immediately: %s", err)
                return ToolResult(
                    success=False,
                    error=f"Failed to post PR summary comment: {err}",
                )

        return ToolResult(
            success=True,
            data="PR summary review recorded. It will be submitted as the final review body.",
        )

    @classmethod
    def format_markdown(cls, summary: ReviewSummary) -> str:
        """Format the summary into a clean GitHub markdown comment."""
        risk_badges = {
            "LOW": "🟢 **Risk Level: LOW**",
            "MEDIUM": "🟡 **Risk Level: MEDIUM**",
            "HIGH": "🔴 **Risk Level: HIGH**",
        }
        badge = risk_badges.get(summary.risk_level, "⚪ **Risk Level: UNKNOWN**")

        lines = [
            "## 🤖 AI Code Review Summary",
            "",
            badge,
            "",
            "### 📋 Key Changes & Analysis",
            summary.summary_text,
            "",
        ]

        if summary.checklist:
            lines.append("### ✅ Human Reviewer Checklist")
            for item in summary.checklist:
                lines.append(f"- [ ] {item}")
            lines.append("")

        lines.extend([
            "---",
            "*Review generated autonomously by [Groq-Powered PR Review Bot](https://github.com/marketplace). "
            "Always verify critical logic before merging.*",
        ])

        return "\n".join(lines)

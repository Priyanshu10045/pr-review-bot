"""Main entrypoint script executed inside GitHub Actions Docker runner or CLI."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from src.agent.groq_client import GroqClient
from src.agent.loop import AgentLoop
from src.config import BotConfig
from src.github_client.client import GitHubClient
from src.tools.codebase_search import SearchCodebaseTool
from src.tools.file_content import GetFileContentTool
from src.tools.inline_comment import PostInlineCommentTool
from src.tools.pr_diff import GetPRDiffTool
from src.tools.pr_metadata import GetPRMetadataTool
from src.tools.registry import ToolRegistry
from src.tools.summary_comment import PostSummaryCommentTool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("PRReviewBot.Entrypoint")


def extract_event_context(config: BotConfig) -> tuple[int | None, str | None, str | None]:
    """Extract PR number, repository name, and commit SHA from GitHub Actions event payload."""
    pr_number = config.pr_number
    repo_name = config.github_repository
    head_sha = None

    if config.github_event_path and Path(config.github_event_path).is_file():
        try:
            with open(config.github_event_path, encoding="utf-8") as f:
                event_data = json.load(f)

            if "pull_request" in event_data:
                pr_data = event_data["pull_request"]
                pr_number = pr_data.get("number", pr_number)
                head_sha = pr_data.get("head", {}).get("sha")
            elif "issue" in event_data and "pull_request" in event_data["issue"]:
                pr_number = event_data["issue"].get("number", pr_number)

            if "repository" in event_data:
                repo_name = event_data["repository"].get("full_name", repo_name)
        except Exception as err:
            logger.warning("Could not read event payload from %s: %s", config.github_event_path, err)

    return pr_number, repo_name, head_sha


def main() -> int:
    """Main execution flow for GitHub Action."""
    logger.info("Initializing AI-Powered PR Review Bot...")
    config = BotConfig()

    # Configure root log level
    log_level_num = getattr(logging, config.log_level.upper(), logging.INFO)
    logging.getLogger().setLevel(log_level_num)

    pr_number, repo_name, event_head_sha = extract_event_context(config)

    if not pr_number:
        logger.warning(
            "No pull request number identified from GITHUB_EVENT_PATH or inputs. "
            "PR Review Bot only runs on pull_request events. Exiting cleanly."
        )
        return 0

    if not repo_name:
        logger.error("GitHub repository name is missing. Set GITHUB_REPOSITORY or check action config.")
        return 1

    try:
        config.validate_for_live_run()
    except ValueError as val_err:
        logger.error("Configuration validation error: %s", val_err)
        return 1

    # 1. Initialize GitHub API Client
    github_client = GitHubClient(
        token=config.github_token,
        repository_name=repo_name,
        mock_mode=False,
    )

    # 2. Fetch initial PR metadata for SHA verification
    try:
        pr_meta = github_client.get_pr_metadata(pr_number)
        head_sha = pr_meta.head_sha or event_head_sha or "HEAD"
    except Exception as err:
        logger.error("Failed to retrieve PR #%d metadata from GitHub: %s", pr_number, err)
        return 1

    # 3. Initialize Groq LLM Client
    groq_client = GroqClient(
        api_key=config.groq_api_key,
        default_model=config.model,
        temperature=config.temperature,
    )

    # 4. Set up Tool Registry
    registry = ToolRegistry()
    registry.register(GetPRDiffTool(github_client=github_client, pr_number=pr_number))
    registry.register(GetPRMetadataTool(github_client=github_client, pr_number=pr_number))
    registry.register(GetFileContentTool(github_client=github_client, repo_root=config.repo_root, default_ref=head_sha))
    registry.register(SearchCodebaseTool(repo_root=config.repo_root))

    # Review comment tools (staged for final batch submission)
    inline_tool = PostInlineCommentTool(
        github_client=github_client,
        pr_number=pr_number,
        head_sha=head_sha,
        immediate_post=False,
    )
    summary_tool = PostSummaryCommentTool(
        github_client=github_client,
        pr_number=pr_number,
        immediate_post=False,
    )
    registry.register(inline_tool)
    registry.register(summary_tool)

    # 5. Execute Agent Loop
    agent_loop = AgentLoop(
        groq_client=groq_client,
        github_client=github_client,
        tool_registry=registry,
        pr_number=pr_number,
        repository=repo_name,
        max_tool_calls=config.max_tool_calls,
        model=config.model,
    )

    result = agent_loop.run()
    agent_loop.tracer.print_summary_table()

    # 6. Post Review to GitHub
    if result.summary:
        summary_markdown = PostSummaryCommentTool.format_markdown(result.summary)
        active_inline_comments = result.inline_comments if config.enable_inline_comments else []

        logger.info(
            "Submitting batch review to PR #%d (%d inline comments, risk=%s)",
            pr_number,
            len(active_inline_comments),
            result.summary.risk_level,
        )

        github_client.submit_batch_review(
            pr_number=pr_number,
            commit_sha=head_sha,
            summary_text=summary_markdown,
            comments=active_inline_comments,
            event="COMMENT",
        )

    logger.info("PR Review Bot completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

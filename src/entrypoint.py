"""Main entrypoint script executed inside GitHub Actions Docker runner or CLI."""

from __future__ import annotations

import argparse
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
        except (json.JSONDecodeError, OSError, KeyError) as err:
            logger.warning(
                "Could not read event payload from %s: %s", config.github_event_path, err
            )

    return pr_number, repo_name, head_sha


def parse_cli_arguments() -> argparse.Namespace:
    """Parse optional CLI flags for running directly against real GitHub PRs."""
    parser = argparse.ArgumentParser(description="AI-Powered PR Review Bot")
    parser.add_argument(
        "--repo", type=str, default=None, help="GitHub repository in 'owner/repo' format"
    )
    parser.add_argument("--pr", type=int, default=None, help="Pull Request number")
    parser.add_argument(
        "--api-key", type=str, default=None, help="Groq API Key (overrides GROQ_API_KEY env)"
    )
    parser.add_argument(
        "--token", type=str, default=None, help="GitHub Token (overrides GITHUB_TOKEN env)"
    )
    parser.add_argument(
        "--model", type=str, default=None, help="Groq Model ID (e.g., llama-3.1-8b-instant)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect and generate review without posting to GitHub",
    )
    return parser.parse_args()


def main() -> int:
    """Main execution flow for GitHub Action or direct live PR review."""
    logger.info("Initializing AI-Powered PR Review Bot...")
    cli_args = parse_cli_arguments()
    config = BotConfig()

    # Override config with CLI arguments if provided
    if cli_args.repo:
        config.github_repository = cli_args.repo
    if cli_args.pr:
        config.pr_number = cli_args.pr
    if cli_args.api_key:
        config.groq_api_key = cli_args.api_key
    if cli_args.token:
        config.github_token = cli_args.token
    if cli_args.model:
        config.model = cli_args.model

    log_level_num = getattr(logging, config.log_level.upper(), logging.INFO)
    logging.getLogger().setLevel(log_level_num)

    pr_number, repo_name, event_head_sha = extract_event_context(config)

    pr_number = pr_number or config.pr_number
    repo_name = repo_name or config.github_repository

    if not pr_number:
        logger.warning(
            "No pull request number provided. Specify --pr <number> or run via GitHub pull_request action."
        )
        return 1

    if not repo_name:
        logger.error(
            "GitHub repository name is missing. Specify --repo 'owner/name' or set GITHUB_REPOSITORY."
        )
        return 1

    try:
        config.validate_for_live_run()
    except ValueError as val_err:
        logger.error("Configuration validation error: %s", val_err)
        return 1

    github_client = GitHubClient(
        token=config.github_token,
        repository_name=repo_name,
        mock_mode=False,
    )
    github_client.check_rate_limit()

    try:
        pr_meta = github_client.get_pr_metadata(pr_number)
        head_sha = pr_meta.head_sha or event_head_sha or "HEAD"
        logger.info(
            "Targeting PR #%d on %s ('%s' by @%s)",
            pr_number,
            repo_name,
            pr_meta.title,
            pr_meta.author,
        )
    except Exception as err:
        logger.error("Failed to retrieve PR #%d metadata from GitHub: %s", pr_number, err)
        return 1

    groq_client = GroqClient(
        api_key=config.groq_api_key,
        default_model=config.model,
        temperature=config.temperature,
    )

    registry = ToolRegistry()
    registry.register(GetPRDiffTool(github_client=github_client, pr_number=pr_number))
    registry.register(GetPRMetadataTool(github_client=github_client, pr_number=pr_number))
    registry.register(
        GetFileContentTool(
            github_client=github_client, repo_root=config.repo_root, default_ref=head_sha
        )
    )
    registry.register(SearchCodebaseTool(repo_root=config.repo_root))

    # Review comment tools
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

        if not cli_args.dry_run:
            logger.info(
                "Submitting batch review to PR #%d on %s (%d inline comments, risk=%s)",
                pr_number,
                repo_name,
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
        else:
            logger.info("[DRY RUN] Review completed successfully without posting to GitHub.")

    logger.info("PR Review Bot completed successfully.")
    return 0 if result.completed_normally else 1


if __name__ == "__main__":
    sys.exit(main())

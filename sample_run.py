"""Standalone CLI script to test and demo the PR Review Bot locally without GitHub Actions.

Usage:
  # 1. Mock run (offline, no API keys required):
  python sample_run.py --diff tests/fixtures/buggy_pr_diff.diff --mock

  # 2. List available Groq models for your API key:
  python sample_run.py --api-key YOUR_GROQ_KEY --list-models

  # 3. Interactively select an available model:
  python sample_run.py --diff tests/fixtures/buggy_pr_diff.diff --api-key YOUR_GROQ_KEY --select-model

  # 4. Live run with a specific model:
  python sample_run.py --diff tests/fixtures/buggy_pr_diff.diff --api-key YOUR_GROQ_KEY --model llama-3.1-8b-instant
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from src.agent.groq_client import GroqClient
from src.agent.loop import AgentLoop
from src.github_client.client import GitHubClient
from src.github_client.models import PRMetadata
from src.tools.codebase_search import SearchCodebaseTool
from src.tools.file_content import GetFileContentTool
from src.tools.inline_comment import PostInlineCommentTool
from src.tools.pr_diff import GetPRDiffTool
from src.tools.pr_metadata import GetPRMetadataTool
from src.tools.registry import ToolRegistry
from src.tools.summary_comment import PostSummaryCommentTool
from src.utils.diff_parser import DiffParser

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

console = Console()


def display_available_models(models: list[str]) -> None:
    """Print available models in a styled Rich Table."""
    table = Table(title="Available Groq Models", show_header=True, header_style="bold cyan")
    table.add_column("#", style="dim", width=4)
    table.add_column("Model ID", style="bold green")

    for idx, model_id in enumerate(models, start=1):
        table.add_row(str(idx), model_id)

    console.print(table)


def prompt_model_selection(groq_client: GroqClient) -> str:
    """Fetch models from Groq SDK and prompt the user to choose one."""
    with console.status("[bold cyan]Fetching available models from Groq API...[/bold cyan]"):
        try:
            available_models = groq_client.list_available_models()
        except Exception as err:
            console.print(f"[bold red]Failed to fetch models from Groq:[/] {err}")
            return "llama-3.1-8b-instant"

    if not available_models:
        console.print("[yellow]No models found; falling back to llama-3.1-8b-instant[/yellow]")
        return "llama-3.1-8b-instant"

    display_available_models(available_models)

    default_choice = "1"
    for i, m in enumerate(available_models, start=1):
        if "70b" in m or "versatile" in m:
            default_choice = str(i)
            break

    choice = Prompt.ask(
        "\n[bold yellow]Select a model number[/bold yellow]",
        choices=[str(i) for i in range(1, len(available_models) + 1)],
        default=default_choice,
    )

    selected = available_models[int(choice) - 1]
    console.print(f"Selected model: [bold green]{selected}[/bold green]\n")
    return selected


def analyze_diff_heuristically(
    diff_content: str,
    inline_tool: PostInlineCommentTool,
    summary_tool: PostSummaryCommentTool,
) -> tuple[int, str]:
    """Inspect diff hunks dynamically using rule-based static heuristics and GitHub suggestions."""
    parsed_files = DiffParser.parse(diff_content)
    total_findings = 0
    risk_level = "LOW"
    critical_findings: list[str] = []

    for file_path, diff_file in parsed_files.items():
        for hunk in diff_file.hunks:
            current_line_num = hunk.new_start
            for raw_line in hunk.lines:
                if raw_line.startswith("+"):
                    line_content = raw_line[1:]

                    # 1. Hardcoded secrets / API keys
                    if re.search(
                        r'(sk-live-[0-9a-zA-Z]+|API_SECRET_KEY\s*=\s*["\'][^"\']+["\']|password\s*=\s*["\'][^"\']+["\'])',
                        line_content,
                        re.IGNORECASE,
                    ):
                        inline_tool.execute(
                            file=file_path,
                            line=current_line_num,
                            comment=(
                                "🚨 **Security Alert (Hardcoded Secret)**: Detected plaintext secret or API credential in source code.\n\n"
                                "```suggestion\n"
                                "# Load credentials securely from environment variables\n"
                                'API_SECRET_KEY = os.getenv("API_SECRET_KEY", "")\n'
                                "```\n"
                                "Store credentials in encrypted repository secrets or environment variables rather than committing them to version control."
                            ),
                        )
                        total_findings += 1
                        risk_level = "HIGH"
                        critical_findings.append(
                            f"Hardcoded credential in `{file_path}:{current_line_num}`"
                        )

                    # 2. SQL Injection / String formatting
                    elif re.search(
                        r'(cursor\.execute\s*\(\s*f["\']|SELECT\s+.*\bWHERE\b.*f["\']|SELECT\s+.*\+.*WHERE)',
                        line_content,
                        re.IGNORECASE,
                    ):
                        inline_tool.execute(
                            file=file_path,
                            line=current_line_num,
                            comment=(
                                "🚨 **Security Alert (SQL Injection)**: Dynamic string interpolation in SQL query statement.\n\n"
                                "```suggestion\n"
                                '    query = "SELECT id, email, role FROM users WHERE email = ? AND password = ?"\n'
                                "    cursor.execute(query, (email, password_hash))\n"
                                "```\n"
                                "Always use parameterized queries with placeholder tuples to prevent SQL injection vulnerabilities."
                            ),
                        )
                        total_findings += 1
                        risk_level = "HIGH"
                        critical_findings.append(
                            f"SQL Injection risk in `{file_path}:{current_line_num}`"
                        )

                    # 3. Off-by-one loop indexing
                    elif re.search(r"range\s*\(\s*len\s*\([^)]+\)\s*\+\s*1\s*\)", line_content):
                        inline_tool.execute(
                            file=file_path,
                            line=current_line_num,
                            comment=(
                                "🐛 **Bug (IndexError / Off-by-One)**: `range(len(tokens) + 1)` iterates past the last valid index, raising runtime `IndexError`.\n\n"
                                "```suggestion\n"
                                "    for token in tokens:\n"
                                "        results.append(token.upper())\n"
                                "```\n"
                                "Use direct sequence iteration or `range(len(tokens))`."
                            ),
                        )
                        total_findings += 1
                        if risk_level != "HIGH":
                            risk_level = "MEDIUM"
                        critical_findings.append(
                            f"Off-by-one loop bounds error in `{file_path}:{current_line_num}`"
                        )

                    # 4. Optional without None check
                    elif (
                        "user_dict[" in line_content
                        and "Optional" in diff_content
                        and "if user_dict" not in diff_content
                    ):
                        inline_tool.execute(
                            file=file_path,
                            line=current_line_num,
                            comment=(
                                "⚠️ **Defensive Coding**: `user_dict` is typed `Optional[dict]`, but accessed directly without a `None` guard.\n\n"
                                "```suggestion\n"
                                "    if not user_dict:\n"
                                "        return []\n"
                                '    return user_dict.get("permissions", [])\n'
                                "```"
                            ),
                        )
                        total_findings += 1
                        if risk_level != "HIGH":
                            risk_level = "MEDIUM"

                    current_line_num += 1
                elif raw_line.startswith(" ") or raw_line == "":
                    current_line_num += 1

    if total_findings > 0:
        summary_tool.execute(
            summary_text=(
                f"Automated static heuristic analysis identified **{total_findings} critical issue(s)** in the PR diff:\n\n"
                + "\n".join(f"- {finding}" for finding in critical_findings)
            ),
            risk_level=risk_level,
            checklist=[
                "Replace raw SQL string formatting with parameterized queries",
                "Rotate exposed API secret key and store in repository secrets/env",
                "Fix list iteration bounds in token batch processing",
            ],
        )
    else:
        changed_files_str = ", ".join(f"`{f}`" for f in parsed_files.keys()) or "PR files"
        summary_tool.execute(
            summary_text=(
                f"Reviewed changes across {changed_files_str}. Added helper functions with proper error handling "
                "and corresponding unit tests. Clean code style and good test coverage."
            ),
            risk_level="LOW",
            checklist=[
                "Verify all CI automated unit tests pass",
                "Confirm backwards compatibility of default parameters",
            ],
        )

    return total_findings, risk_level


def run_local_review(
    diff_path: Path,
    groq_api_key: str | None = None,
    mock_mode: bool = False,
    model: str = "llama-3.3-70b-versatile",
    select_model: bool = False,
    list_models_only: bool = False,
    max_steps: int = 15,
) -> int:
    """Run an agent review simulation or live review on a diff file."""
    # If user wants to list models and exit
    if list_models_only:
        if not groq_api_key:
            console.print(
                "[bold red]Error:[/] --list-models requires a Groq API key via --api-key or GROQ_API_KEY env var."
            )
            return 1
        groq_client = GroqClient(api_key=groq_api_key)
        try:
            models = groq_client.list_available_models()
            display_available_models(models)
            return 0
        except Exception as err:
            console.print(f"[bold red]Error fetching models:[/] {err}")
            return 1

    if not diff_path.is_file():
        console.print(f"[bold red]Error:[/] Diff file not found at '{diff_path}'")
        return 1

    diff_content = diff_path.read_text(encoding="utf-8")
    pr_number = 42
    repo_name = "demo-org/payment-service"

    # Setup mock GitHub Client
    github_client = GitHubClient(mock_mode=True)
    mock_meta = PRMetadata(
        number=pr_number,
        title="feat: Optimize database queries and update user authentication handler",
        body="This PR updates our SQL queries for better indexing and refactors user authentication error handling.",
        author="contributor-dev",
        base_branch="main",
        head_branch="feature/auth-and-db-opt",
        head_sha="a1b2c3d4e5f6789012345678901234567890abcd",
        base_sha="f9e8d7c6b5a4321098765432109876543210fedc",
        changed_files_count=2,
        additions=45,
        deletions=12,
    )

    # Initialize Tool Registry
    registry = ToolRegistry()
    registry.register(
        GetPRDiffTool(github_client=github_client, pr_number=pr_number, local_diff=diff_content)
    )
    registry.register(
        GetPRMetadataTool(github_client=github_client, pr_number=pr_number, mock_metadata=mock_meta)
    )
    registry.register(GetFileContentTool(github_client=github_client, repo_root=Path.cwd()))
    registry.register(SearchCodebaseTool(repo_root=Path.cwd()))

    inline_tool = PostInlineCommentTool(
        github_client=github_client,
        pr_number=pr_number,
        head_sha=mock_meta.head_sha,
        immediate_post=False,
        diff_text=diff_content,
    )
    summary_tool = PostSummaryCommentTool(
        github_client=github_client,
        pr_number=pr_number,
        immediate_post=False,
    )
    registry.register(inline_tool)
    registry.register(summary_tool)

    if mock_mode or not groq_api_key:
        console.print(
            Panel(
                f"[bold green]AI PR Review Bot - Local Runner[/bold green]\n"
                f"Diff File: [cyan]{diff_path}[/cyan]\n"
                f"Mode: [yellow]Mock / Offline Simulation[/yellow]",
                title="🚀 Session Initialized",
                border_style="green",
            )
        )
        console.print(
            "[yellow]Running in mock mode. Executing dynamic heuristic static analysis...[/yellow]"
        )

        # Simulate agent tool steps
        registry.execute("get_pr_diff", {})
        registry.execute("get_pr_metadata", {})

        analyze_diff_heuristically(diff_content, inline_tool, summary_tool)

        if summary_tool.recorded_summary:
            formatted_summary = PostSummaryCommentTool.format_markdown(
                summary_tool.recorded_summary
            )
            console.print(
                Panel(formatted_summary, title="📋 Mock Review Summary", border_style="purple")
            )

        console.print(
            f"\n[bold green]Mock analysis complete![/] "
            f"Inline comments: {len(inline_tool.staged_comments)}, "
            f"Risk: {summary_tool.recorded_summary.risk_level if summary_tool.recorded_summary else 'N/A'}"
        )
        return 0

    # Live Groq API Execution
    groq_client = GroqClient(api_key=groq_api_key, default_model=model)

    chosen_model = model
    if select_model:
        chosen_model = prompt_model_selection(groq_client)

    console.print(
        Panel(
            f"[bold green]AI PR Review Bot - Local Runner[/bold green]\n"
            f"Diff File: [cyan]{diff_path}[/cyan]\n"
            f"Model: [magenta]{chosen_model}[/magenta]\n"
            f"Mode: [yellow]Live Groq API[/yellow]",
            title="🚀 Session Initialized",
            border_style="green",
        )
    )

    agent = AgentLoop(
        groq_client=groq_client,
        github_client=github_client,
        tool_registry=registry,
        pr_number=pr_number,
        repository=repo_name,
        max_tool_calls=max_steps,
        model=chosen_model,
    )

    result = agent.run()
    agent.tracer.print_summary_table()

    if result.summary:
        formatted_summary = PostSummaryCommentTool.format_markdown(result.summary)
        console.print(
            Panel(formatted_summary, title="📋 PR Review Summary Output", border_style="purple")
        )

    console.print(
        f"\n[bold green]Review complete![/] Total steps: {result.total_steps}, "
        f"Inline comments: {len(result.inline_comments)}, Risk: {result.summary.risk_level if result.summary else 'N/A'}"
    )
    return 0 if result.completed_normally else 1


def main():
    parser = argparse.ArgumentParser(description="Local runner for AI-Powered PR Review Bot")
    parser.add_argument(
        "--diff",
        type=Path,
        default=Path("tests/fixtures/buggy_pr_diff.diff"),
        help="Path to unified diff file",
    )
    parser.add_argument(
        "--api-key", type=str, default=None, help="Groq API Key (or set GROQ_API_KEY env var)"
    )
    parser.add_argument(
        "--mock", action="store_true", help="Run offline in mock mode without live LLM calls"
    )
    parser.add_argument(
        "--model", type=str, default="llama-3.3-70b-versatile", help="Groq model ID"
    )
    parser.add_argument(
        "--select-model",
        action="store_true",
        help="Interactively query Groq SDK for available models and select one",
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="List all available Groq models for your API key and exit",
    )
    parser.add_argument("--max-steps", type=int, default=15, help="Max tool execution steps")

    args = parser.parse_args()
    return run_local_review(
        diff_path=args.diff,
        groq_api_key=args.api_key,
        mock_mode=args.mock,
        model=args.model,
        select_model=args.select_model,
        list_models_only=args.list_models,
        max_steps=args.max_steps,
    )


if __name__ == "__main__":
    sys.exit(main())

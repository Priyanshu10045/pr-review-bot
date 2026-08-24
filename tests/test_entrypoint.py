"""Unit tests for entrypoint.py CLI execution, validation, and full execution pathways."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.config import BotConfig
from src.entrypoint import extract_event_context, main, parse_cli_arguments
from src.github_client.models import PRMetadata


class TestEntrypoint:
    """Test suite for entrypoint.py orchestration flow."""

    def test_parse_cli_arguments(self):
        with patch(
            "sys.argv",
            [
                "entrypoint.py",
                "--repo",
                "owner/repo",
                "--pr",
                "42",
                "--dry-run",
                "--model",
                "llama-3.1-8b-instant",
            ],
        ):
            args = parse_cli_arguments()
            assert args.repo == "owner/repo"
            assert args.pr == 42
            assert args.dry_run is True
            assert args.model == "llama-3.1-8b-instant"

    def test_extract_event_context_from_issue_comment(self, tmp_path: Path):
        event_file = tmp_path / "issue_comment_event.json"
        event_data = {
            "issue": {"number": 88, "pull_request": {}},
            "repository": {"full_name": "org/my-service"},
        }
        event_file.write_text(json.dumps(event_data), encoding="utf-8")

        config = BotConfig(github_event_path=str(event_file))
        pr_number, repo_name, head_sha = extract_event_context(config)

        assert pr_number == 88
        assert repo_name == "org/my-service"
        assert head_sha is None

    def test_main_missing_pr_number(self):
        with patch("sys.argv", ["entrypoint.py", "--repo", "owner/repo"]):
            exit_code = main()
            assert exit_code == 1

    def test_main_missing_repo_name(self):
        with patch("sys.argv", ["entrypoint.py", "--pr", "12"]):
            exit_code = main()
            assert exit_code == 1

    def test_main_missing_secrets_validation_error(self):
        with patch("sys.argv", ["entrypoint.py", "--repo", "owner/repo", "--pr", "12"]):
            with patch.dict("os.environ", {"GROQ_API_KEY": "", "GITHUB_TOKEN": ""}, clear=True):
                exit_code = main()
                assert exit_code == 1

    def test_main_successful_dry_run(self, tmp_path: Path):
        mock_meta = PRMetadata(
            number=42,
            title="feat: Clean refactor",
            body="PR description",
            author="dev",
            base_branch="main",
            head_branch="feature",
            head_sha="sha1234567890",
            base_sha="basesha123456",
        )

        with patch(
            "sys.argv",
            [
                "entrypoint.py",
                "--repo",
                "owner/repo",
                "--pr",
                "42",
                "--dry-run",
                "--api-key",
                "gsk_test1234",
                "--token",
                "ghp_test",
            ],
        ):
            with patch("src.entrypoint.GitHubClient") as mock_gh_cls:
                mock_gh_instance = MagicMock()
                mock_gh_instance.get_pr_metadata.return_value = mock_meta
                mock_gh_instance.get_pr_diff.return_value = "diff --git a/a.py b/a.py\n+x = 1\n"
                mock_gh_cls.return_value = mock_gh_instance

                with patch("src.entrypoint.GroqClient") as mock_groq_cls:
                    mock_groq_instance = MagicMock()
                    mock_groq_cls.return_value = mock_groq_instance

                    with patch("src.entrypoint.AgentLoop") as mock_loop_cls:
                        mock_loop_instance = MagicMock()
                        mock_result = MagicMock()
                        mock_result.completed_normally = True
                        mock_result.summary = None
                        mock_result.inline_comments = []
                        mock_loop_instance.run.return_value = mock_result
                        mock_loop_cls.return_value = mock_loop_instance

                        exit_code = main()
                        assert exit_code == 0
                        mock_gh_instance.get_pr_metadata.assert_called_once_with(42)
                        mock_gh_instance.submit_batch_review.assert_not_called()

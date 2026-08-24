"""Unit tests for GitHub Client wrapper, retry logic, and rate limiting."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from github import GithubException

from src.github_client.client import GitHubClient
from src.github_client.models import InlineComment


class TestGitHubClient:
    """Test suite for GitHubClient abstraction."""

    def test_mock_mode_operations(self):
        client = GitHubClient(mock_mode=True)
        meta = client.get_pr_metadata(42)
        assert meta.number == 42
        assert meta.author == "developer"

        # Post inline comment
        assert client.post_inline_comment(42, "sha123", "src/auth.py", 10, "Looks suspicious")
        assert len(client.mock_posted_comments) == 1
        assert client.mock_posted_comments[0]["path"] == "src/auth.py"

        # Post summary comment
        assert client.post_summary_comment(42, "Great PR")
        assert len(client.mock_summary_comments) == 1

    def test_retry_on_github_rate_limit_403(self):
        client = GitHubClient(mock_mode=False)

        mock_func = MagicMock()
        mock_func.side_effect = [
            GithubException(403, {"message": "API rate limit exceeded"}, None),
            "success_response",
        ]

        result = client._execute_with_retry(mock_func, max_retries=2, initial_delay=0.01)
        assert result == "success_response"
        assert mock_func.call_count == 2

    def test_retry_exhaustion_raises_exception(self):
        client = GitHubClient(mock_mode=False)

        mock_func = MagicMock()
        mock_func.side_effect = GithubException(500, {"message": "Internal Server Error"}, None)

        with pytest.raises(GithubException):
            client._execute_with_retry(mock_func, max_retries=2, initial_delay=0.01)
        assert mock_func.call_count == 2

    def test_check_rate_limit_with_mocked_gh(self):
        client = GitHubClient(mock_mode=False)
        mock_gh = MagicMock()
        mock_rate = MagicMock()
        mock_core = MagicMock()
        mock_core.remaining = 4500
        mock_core.limit = 5000
        mock_core.reset = "2026-08-24 22:00:00"
        mock_rate.core = mock_core
        mock_gh.get_rate_limit.return_value = mock_rate
        client._gh = mock_gh

        info = client.check_rate_limit()
        assert info["remaining"] == 4500
        assert info["limit"] == 5000

    def test_check_rate_limit_exception_handling(self):
        client = GitHubClient(mock_mode=False)
        mock_gh = MagicMock()
        mock_gh.get_rate_limit.side_effect = GithubException(401, {"message": "Bad credentials"}, None)
        client._gh = mock_gh

        info = client.check_rate_limit()
        assert info == {}

    def test_batch_review_submission_success(self):
        client = GitHubClient(mock_mode=False)
        mock_repo = MagicMock()
        mock_pr = MagicMock()
        mock_repo.get_pull.return_value = mock_pr
        mock_repo.get_commit.return_value = MagicMock()
        client._repo = mock_repo

        comments = [InlineComment(path="src/main.py", line=5, body="Refactor this", side="RIGHT")]

        success = client.submit_batch_review(
            pr_number=10,
            commit_sha="abc1234",
            summary_text="Batch Review Summary",
            comments=comments,
        )

        assert success is True
        mock_pr.create_review.assert_called_once()

    def test_batch_review_fallback_on_failure(self):
        client = GitHubClient(mock_mode=False)
        mock_repo = MagicMock()
        mock_pr = MagicMock()
        mock_pr.create_review.side_effect = GithubException(422, {"message": "Invalid line"}, None)
        mock_repo.get_pull.return_value = mock_pr
        mock_repo.get_commit.return_value = MagicMock()
        client._repo = mock_repo

        comments = [InlineComment(path="src/main.py", line=5, body="Refactor this", side="RIGHT")]

        success = client.submit_batch_review(
            pr_number=10,
            commit_sha="abc1234",
            summary_text="Batch Review Summary",
            comments=comments,
        )

        assert success is True
        mock_pr.create_issue_comment.assert_called_once()

    def test_get_pr_files_and_diff(self):
        client = GitHubClient(token="ghp_test", repository_name="owner/repo", mock_mode=False)
        mock_repo = MagicMock()
        mock_pr = MagicMock()

        file1 = MagicMock()
        file1.filename = "src/calculator.py"
        file1.status = "modified"
        file1.additions = 5
        file1.deletions = 1
        file1.changes = 6
        file1.patch = "@@ -1,3 +1,5 @@\n+print('hi')\n"
        file1.previous_filename = None

        mock_pr.get_files.return_value = [file1]
        mock_repo.get_pull.return_value = mock_pr
        client._repo = mock_repo

        diff_hunks = client.get_pr_files(10)
        assert len(diff_hunks) == 1
        assert diff_hunks[0].filename == "src/calculator.py"

        diff_text = client.get_pr_diff(10)
        assert "diff --git a/src/calculator.py b/src/calculator.py" in diff_text
        assert "+print('hi')" in diff_text

    def test_get_file_content_live(self):
        client = GitHubClient(token="ghp_test", repository_name="owner/repo", mock_mode=False)
        mock_repo = MagicMock()
        mock_content = MagicMock()
        mock_content.decoded_content = b"def test():\n    return True\n"
        mock_repo.get_contents.return_value = mock_content
        client._repo = mock_repo

        content = client.get_file_content("src/main.py", ref="main")
        assert "def test():" in content

    def test_get_file_content_404_not_found(self):
        client = GitHubClient(token="ghp_test", repository_name="owner/repo", mock_mode=False)
        mock_repo = MagicMock()
        mock_repo.get_contents.side_effect = GithubException(404, {"message": "Not Found"}, None)
        client._repo = mock_repo

        res = client.get_file_content("missing.py")
        assert "Error: File 'missing.py' not found" in res

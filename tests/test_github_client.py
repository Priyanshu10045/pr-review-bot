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

        # Mock function that fails once with 403 then succeeds
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

    def test_batch_review_submission_success(self, mocker):
        client = GitHubClient(mock_mode=False)
        mock_repo = MagicMock()
        mock_pr = MagicMock()
        mock_repo.get_pull.return_value = mock_pr
        mock_repo.get_commit.return_value = MagicMock()
        client._repo = mock_repo

        comments = [
            InlineComment(path="src/main.py", line=5, body="Refactor this", side="RIGHT")
        ]

        success = client.submit_batch_review(
            pr_number=10,
            commit_sha="abc1234",
            summary_text="Batch Review Summary",
            comments=comments,
        )

        assert success is True
        mock_pr.create_review.assert_called_once()

    def test_batch_review_fallback_on_failure(self, mocker):
        client = GitHubClient(mock_mode=False)
        mock_repo = MagicMock()
        mock_pr = MagicMock()
        # Make create_review fail to trigger individual fallback
        mock_pr.create_review.side_effect = GithubException(422, {"message": "Invalid line"}, None)
        mock_repo.get_pull.return_value = mock_pr
        mock_repo.get_commit.return_value = MagicMock()
        client._repo = mock_repo

        comments = [
            InlineComment(path="src/main.py", line=5, body="Refactor this", side="RIGHT")
        ]

        success = client.submit_batch_review(
            pr_number=10,
            commit_sha="abc1234",
            summary_text="Batch Review Summary",
            comments=comments,
        )

        assert success is True
        # Summary comment and inline comment should be posted via fallback
        mock_pr.create_issue_comment.assert_called_once()

"""Resilient GitHub API Client wrapper using PyGithub with rate-limit and error handling."""

from __future__ import annotations

import logging
import time

from github import Auth, Github, GithubException, PullRequest, Repository

from src.github_client.models import DiffHunk, InlineComment, PRMetadata

logger = logging.getLogger("PRReviewBot.GitHubClient")


class GitHubClient:
    """Encapsulates all GitHub REST API interactions with retry logic and rate limit tracking.

    Why this abstraction?
    - Single point of failure: isolates external API quirks from the core agent logic.
    - Testability: enables 100% mocked testing without leaking GitHub SDK types into tools.
    - Resilience: handles GitHub secondary rate limits (403) and transient network drops with backoff.
    """

    def __init__(
        self,
        token: str | None = None,
        repository_name: str | None = None,
        mock_mode: bool = False,
    ):
        self.token = token
        self.repository_name = repository_name
        self.mock_mode = mock_mode
        self._gh: Github | None = None
        self._repo: Repository.Repository | None = None

        # In-memory storage for dry-run/mock testing
        self.mock_posted_comments: list[dict] = []
        self.mock_summary_comments: list[str] = []

        if not self.mock_mode and token and repository_name:
            auth = Auth.Token(token)
            self._gh = Github(auth=auth, per_page=100)
            self._check_rate_limit()

    def _get_repo(self) -> Repository.Repository:
        """Lazily fetch and cache the GitHub repository instance."""
        if self.mock_mode:
            raise RuntimeError("Cannot fetch live repository in mock mode.")
        if self._repo is None:
            if not self._gh or not self.repository_name:
                raise ValueError("GitHub client is not properly initialized with token and repo.")
            self._repo = self._gh.get_repo(self.repository_name)
        return self._repo

    def _check_rate_limit(self) -> dict:
        """Inspect and log remaining GitHub API rate limits."""
        if not self._gh:
            return {}
        try:
            rate_limit = self._gh.get_rate_limit()
            core_limit = rate_limit.core
            logger.info(
                "GitHub API Rate Limit: %d/%d remaining (resets at %s)",
                core_limit.remaining,
                core_limit.limit,
                core_limit.reset,
            )
            if core_limit.remaining < 50:
                logger.warning(
                    "Low GitHub API rate limit remaining (%d calls left). Throttling may occur.",
                    core_limit.remaining,
                )
            return {
                "remaining": core_limit.remaining,
                "limit": core_limit.limit,
                "reset": str(core_limit.reset),
            }
        except Exception as err:
            logger.warning("Could not fetch GitHub rate limit info: %s", err)
            return {}

    def _execute_with_retry(self, func, max_retries: int = 3, initial_delay: float = 2.0):
        """Execute a GitHub API call with exponential backoff for 403/429/5xx errors."""
        delay = initial_delay
        for attempt in range(1, max_retries + 1):
            try:
                return func()
            except GithubException as err:
                status = err.status
                is_rate_limit = status in (403, 429)
                is_server_error = status >= 500

                if (is_rate_limit or is_server_error) and attempt < max_retries:
                    logger.warning(
                        "GitHub API returned status %d on attempt %d/%d. Backing off for %.1fs. Error: %s",
                        status,
                        attempt,
                        max_retries,
                        delay,
                        err.data if hasattr(err, "data") else err,
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    logger.error("GitHub API call failed after %d attempts: %s", attempt, err)
                    raise
            except Exception as err:
                if attempt < max_retries:
                    logger.warning(
                        "Transient exception on attempt %d/%d: %s. Retrying in %.1fs...",
                        attempt,
                        max_retries,
                        err,
                        delay,
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise

    def get_pr_metadata(self, pr_number: int) -> PRMetadata:
        """Fetch metadata for a pull request."""
        if self.mock_mode:
            return PRMetadata(
                number=pr_number,
                title="Mock PR Title",
                body="Mock PR Description",
                author="developer",
                base_branch="main",
                head_branch="feature/mock-branch",
                head_sha="mockheadsha1234567890",
                base_sha="mockbasesha1234567890",
                changed_files_count=1,
                additions=10,
                deletions=2,
            )

        repo = self._get_repo()
        pr: PullRequest.PullRequest = self._execute_with_retry(lambda: repo.get_pull(pr_number))

        return PRMetadata(
            number=pr.number,
            title=pr.title,
            body=pr.body or "",
            author=pr.user.login if pr.user else "unknown",
            base_branch=pr.base.ref,
            head_branch=pr.head.ref,
            head_sha=pr.head.sha,
            base_sha=pr.base.sha,
            changed_files_count=pr.changed_files,
            additions=pr.additions,
            deletions=pr.deletions,
            labels=[label.name for label in pr.labels],
            state=pr.state,
        )

    def get_pr_files(self, pr_number: int) -> list[DiffHunk]:
        """Fetch all changed files and patches for a PR."""
        if self.mock_mode:
            return []

        repo = self._get_repo()
        pr = self._execute_with_retry(lambda: repo.get_pull(pr_number))
        paginated_files = self._execute_with_retry(lambda: list(pr.get_files()))

        diff_hunks = []
        for file in paginated_files:
            diff_hunks.append(
                DiffHunk(
                    filename=file.filename,
                    status=file.status,
                    additions=file.additions,
                    deletions=file.deletions,
                    changes=file.changes,
                    patch=file.patch or "",
                    previous_filename=getattr(file, "previous_filename", None),
                )
            )
        return diff_hunks

    def get_pr_diff(self, pr_number: int) -> str:
        """Fetch the full unified diff for the PR as raw text."""
        if self.mock_mode:
            return ""

        diff_hunks = self.get_pr_files(pr_number)
        unified_diff_parts = []
        for hunk in diff_hunks:
            header = f"diff --git a/{hunk.filename} b/{hunk.filename}\n"
            header += f"--- a/{hunk.previous_filename or hunk.filename}\n"
            header += f"+++ b/{hunk.filename}\n"
            if hunk.patch:
                unified_diff_parts.append(header + hunk.patch)
            else:
                unified_diff_parts.append(header + "(binary or empty file change)")

        return "\n\n".join(unified_diff_parts)

    def get_file_content(self, path: str, ref: str | None = None) -> str:
        """Fetch full content of a file at a specific Git reference (branch or commit SHA)."""
        if self.mock_mode:
            return f"// Mock content for {path} at {ref}"

        repo = self._get_repo()
        kwargs = {"path": path}
        if ref:
            kwargs["ref"] = ref

        try:
            content_file = self._execute_with_retry(lambda: repo.get_contents(**kwargs))
            if isinstance(content_file, list):
                return f"Path '{path}' is a directory containing {len(content_file)} items."
            return content_file.decoded_content.decode("utf-8", errors="replace")
        except GithubException as err:
            if err.status == 404:
                return f"Error: File '{path}' not found at ref '{ref}'."
            raise

    def post_inline_comment(
        self,
        pr_number: int,
        commit_sha: str,
        path: str,
        line: int,
        body: str,
        side: str = "RIGHT",
    ) -> bool:
        """Post a single review comment on a specific line of code in the PR."""
        if self.mock_mode:
            logger.info("[MOCK] Posting inline comment on %s:%d -> %s", path, line, body)
            self.mock_posted_comments.append(
                {"path": path, "line": line, "body": body, "side": side, "commit_sha": commit_sha}
            )
            return True

        repo = self._get_repo()
        pr = self._execute_with_retry(lambda: repo.get_pull(pr_number))

        try:
            self._execute_with_retry(
                lambda: pr.create_review_comment(
                    body=body,
                    commit=repo.get_commit(commit_sha),
                    path=path,
                    line=line,
                    side=side,
                )
            )
            logger.info("Successfully posted inline comment on %s:%d", path, line)
            return True
        except GithubException as err:
            logger.warning(
                "Could not post inline review comment on %s:%d directly (%s). Falling back to PR issue comment.",
                path,
                line,
                err,
            )
            # Fallback: post as an issue comment with location tag
            fallback_body = f"**[Inline Comment for `{path}:{line}`]**\n\n{body}"
            return self.post_summary_comment(pr_number, fallback_body)

    def post_summary_comment(self, pr_number: int, body: str) -> bool:
        """Post an overall PR issue comment."""
        if self.mock_mode:
            logger.info("[MOCK] Posting PR summary comment -> %s", body[:100] + "...")
            self.mock_summary_comments.append(body)
            return True

        repo = self._get_repo()
        pr = self._execute_with_retry(lambda: repo.get_pull(pr_number))
        self._execute_with_retry(lambda: pr.create_issue_comment(body))
        logger.info("Successfully posted summary comment on PR #%d", pr_number)
        return True

    def submit_batch_review(
        self,
        pr_number: int,
        commit_sha: str,
        summary_text: str,
        comments: list[InlineComment],
        event: str = "COMMENT",
    ) -> bool:
        """Submit a structured GitHub Pull Request Review with batch inline comments."""
        if self.mock_mode:
            logger.info(
                "[MOCK] Batch review submitted with %d inline comments and summary.",
                len(comments),
            )
            self.mock_summary_comments.append(summary_text)
            for c in comments:
                self.mock_posted_comments.append(c.model_dump())
            return True

        repo = self._get_repo()
        pr = self._execute_with_retry(lambda: repo.get_pull(pr_number))

        review_comments_payload = []
        for c in comments:
            review_comments_payload.append(
                {
                    "path": c.path,
                    "line": c.line,
                    "body": c.body,
                    "side": c.side,
                }
            )

        try:
            if review_comments_payload:
                self._execute_with_retry(
                    lambda: pr.create_review(
                        commit=repo.get_commit(commit_sha),
                        body=summary_text,
                        event=event,
                        comments=review_comments_payload,
                    )
                )
            else:
                self._execute_with_retry(
                    lambda: pr.create_review(
                        commit=repo.get_commit(commit_sha),
                        body=summary_text,
                        event=event,
                    )
                )
            logger.info("Successfully submitted batch review with %d inline comments.", len(comments))
            return True
        except GithubException as err:
            logger.warning("Batch review submission failed (%s). Falling back to individual comments.", err)
            # Fallback: post individually
            self.post_summary_comment(pr_number, summary_text)
            for c in comments:
                self.post_inline_comment(
                    pr_number=pr_number,
                    commit_sha=commit_sha,
                    path=c.path,
                    line=c.line,
                    body=c.body,
                    side=c.side,
                )
            return True

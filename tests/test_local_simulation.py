"""Unit tests for offline local CLI simulation and entrypoint event parsing."""

from __future__ import annotations

from pathlib import Path

from sample_run import run_local_review
from src.config import BotConfig
from src.entrypoint import extract_event_context


class TestLocalSimulationAndEntrypoint:
    """Test suite for local runner and event extraction."""

    def test_run_local_review_mock_mode(self, fixtures_dir: Path):
        diff_path = fixtures_dir / "buggy_pr_diff.diff"
        exit_code = run_local_review(
            diff_path=diff_path,
            mock_mode=True,
            model="llama-3.3-70b-versatile",
        )
        assert exit_code == 0

    def test_run_local_review_missing_file(self, tmp_path: Path):
        non_existent = tmp_path / "does_not_exist.diff"
        exit_code = run_local_review(diff_path=non_existent, mock_mode=True)
        assert exit_code == 1

    def test_run_local_review_clean_diff(self, fixtures_dir: Path):
        diff_path = fixtures_dir / "clean_pr_diff.diff"
        exit_code = run_local_review(
            diff_path=diff_path,
            mock_mode=True,
            model="llama-3.3-70b-versatile",
        )
        assert exit_code == 0

    def test_run_local_review_dynamic_custom_diff(self, tmp_path: Path):
        custom_diff = tmp_path / "custom.diff"
        custom_diff.write_text(
            """diff --git a/custom_service.py b/custom_service.py
--- a/custom_service.py
+++ b/custom_service.py
@@ -1,3 +1,5 @@
+API_SECRET_KEY = "sk-live-abcdef123456"
+cursor.execute(f"SELECT * FROM items WHERE id = '{item_id}'")
""",
            encoding="utf-8",
        )
        exit_code = run_local_review(
            diff_path=custom_diff,
            mock_mode=True,
        )
        assert exit_code == 0

    def test_extract_event_context(self, fixtures_dir: Path):
        event_path = fixtures_dir / "sample_event.json"
        config = BotConfig(
            github_event_path=str(event_path),
            github_repository="octocat/Hello-World",
        )
        pr_number, repo_name, head_sha = extract_event_context(config)

        assert pr_number == 101
        assert repo_name == "octocat/Hello-World"
        assert head_sha == "6dcb09b5b57875f334f61aebed695e2e4193db5e"

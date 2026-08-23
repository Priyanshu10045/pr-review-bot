"""Unit tests for agent tools, parameter validation, and JSON schemas."""

from __future__ import annotations

from pathlib import Path

from src.github_client.client import GitHubClient
from src.tools.codebase_search import SearchCodebaseTool
from src.tools.file_content import GetFileContentTool
from src.tools.inline_comment import PostInlineCommentTool
from src.tools.pr_diff import GetPRDiffTool
from src.tools.pr_metadata import GetPRMetadataTool
from src.tools.registry import ToolRegistry
from src.tools.summary_comment import PostSummaryCommentTool


class TestTools:
    """Test suite for agent tools and registry."""

    def test_tool_registry_schema_generation(self):
        registry = ToolRegistry()
        mock_client = GitHubClient(mock_mode=True)
        registry.register(GetPRDiffTool(mock_client, 1))
        registry.register(PostSummaryCommentTool(mock_client, 1))

        schemas = registry.get_groq_tools_schema()
        assert len(schemas) == 2
        assert all(s["type"] == "function" for s in schemas)
        names = [s["function"]["name"] for s in schemas]
        assert "get_pr_diff" in names
        assert "post_summary_comment" in names

    def test_get_pr_diff_tool_local(self, clean_diff_text):
        mock_client = GitHubClient(mock_mode=True)
        tool = GetPRDiffTool(github_client=mock_client, pr_number=1, local_diff=clean_diff_text)
        res = tool.execute()
        assert res.success is True
        assert "safe_divide" in res.data

    def test_get_pr_metadata_tool(self, mock_pr_metadata):
        mock_client = GitHubClient(mock_mode=True)
        tool = GetPRMetadataTool(github_client=mock_client, pr_number=101, mock_metadata=mock_pr_metadata)
        res = tool.execute()
        assert res.success is True
        assert res.data["pr_number"] == 101
        assert res.data["author"] == "octocat"
        assert res.data["head_branch"] == "feature/auth-speedup"

    def test_get_file_content_tool_local(self, tmp_path: Path):
        test_file = tmp_path / "sample.py"
        test_file.write_text("def hello():\n    return 'world'\n", encoding="utf-8")

        mock_client = GitHubClient(mock_mode=True)
        tool = GetFileContentTool(github_client=mock_client, repo_root=tmp_path)
        res = tool.execute(path="sample.py")
        assert res.success is True
        assert "hello()" in res.data
        assert "   1 | def hello():" in res.data

    def test_search_codebase_tool(self, tmp_path: Path):
        code_dir = tmp_path / "src"
        code_dir.mkdir()
        (code_dir / "user_auth.py").write_text("def authenticate_jwt_token(): pass\n", encoding="utf-8")
        (code_dir / "billing.py").write_text("def process_payment(): pass\n", encoding="utf-8")

        tool = SearchCodebaseTool(repo_root=tmp_path)

        # Successful match
        res = tool.execute(query="authenticate_jwt")
        assert res.success is True
        assert "user_auth.py" in res.data

        # No match
        no_res = tool.execute(query="non_existent_symbol_12345")
        assert no_res.success is True
        assert "No occurrences" in no_res.data or "No matches" in no_res.data

    def test_post_inline_comment_validation(self):
        mock_client = GitHubClient(mock_mode=True)
        tool = PostInlineCommentTool(
            github_client=mock_client,
            pr_number=1,
            head_sha="sha123",
            immediate_post=False,
        )

        # Valid comment
        res = tool.execute(file="src/auth.py", line=15, comment="SQL injection vulnerability")
        assert res.success is True
        assert len(tool.staged_comments) == 1
        assert tool.staged_comments[0].line == 15

        # Invalid line number
        err_res = tool.execute(file="src/auth.py", line=-1, comment="Bad line")
        assert err_res.success is False

    def test_post_summary_comment_formatting(self):
        mock_client = GitHubClient(mock_mode=True)
        tool = PostSummaryCommentTool(github_client=mock_client, pr_number=1, immediate_post=False)

        res = tool.execute(
            summary_text="Overall clean refactor with improved tests.",
            risk_level="LOW",
            checklist=["Verify test coverage", "Check benchmark numbers"],
        )
        assert res.success is True
        assert tool.recorded_summary is not None
        assert tool.recorded_summary.risk_level == "LOW"

        md = PostSummaryCommentTool.format_markdown(tool.recorded_summary)
        assert "🟢 **Risk Level: LOW**" in md
        assert "Verify test coverage" in md

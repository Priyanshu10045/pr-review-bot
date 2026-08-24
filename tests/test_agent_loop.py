"""Unit and integration tests for the Agent Orchestration Loop and safeguards."""

from __future__ import annotations

from unittest.mock import MagicMock

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
from tests.conftest import make_mock_completion, make_mock_tool_call


class TestAgentLoop:
    """Test suite for agent loop execution, multi-step tool calls, and safeguards."""

    def _setup_test_registry(
        self,
        mock_client: GitHubClient,
        diff_text: str,
        pr_meta: PRMetadata,
    ) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(GetPRDiffTool(mock_client, pr_meta.number, local_diff=diff_text))
        registry.register(GetPRMetadataTool(mock_client, pr_meta.number, mock_metadata=pr_meta))
        registry.register(GetFileContentTool(mock_client))
        registry.register(SearchCodebaseTool())
        registry.register(
            PostInlineCommentTool(
                mock_client,
                pr_meta.number,
                head_sha=pr_meta.head_sha,
                immediate_post=False,
            )
        )
        registry.register(
            PostSummaryCommentTool(
                mock_client,
                pr_meta.number,
                immediate_post=False,
            )
        )
        return registry

    def test_clean_pr_review_flow(self, clean_diff_text, mock_pr_metadata):
        """Verify agent loop on a clean PR with no issues."""
        mock_github = GitHubClient(mock_mode=True)
        registry = self._setup_test_registry(mock_github, clean_diff_text, mock_pr_metadata)

        # Mock Groq LLM responses:
        # Step 1: LLM asks for diff and metadata
        step1_tc1 = make_mock_tool_call("get_pr_diff", {}, "call_1")
        step1_tc2 = make_mock_tool_call("get_pr_metadata", {}, "call_2")
        resp_step1 = make_mock_completion(
            content="Let's inspect the diff and metadata.", tool_calls=[step1_tc1, step1_tc2]
        )

        # Step 2: LLM assesses clean changes and posts summary
        step2_tc = make_mock_tool_call(
            "post_summary_comment",
            {
                "summary_text": "Code looks very clean, well-tested safe division helper added.",
                "risk_level": "LOW",
                "checklist": ["Verify CI unit tests pass"],
            },
            "call_3",
        )
        resp_step2 = make_mock_completion(content="Code looks solid.", tool_calls=[step2_tc])

        mock_groq = MagicMock(spec=GroqClient)
        mock_groq.generate_completion.side_effect = [resp_step1, resp_step2]

        loop = AgentLoop(
            groq_client=mock_groq,
            github_client=mock_github,
            tool_registry=registry,
            pr_number=mock_pr_metadata.number,
            repository="octocat/Hello-World",
            max_tool_calls=10,
        )

        result = loop.run()

        assert result.completed_normally is True
        assert result.total_steps == 2
        assert len(result.inline_comments) == 0
        assert result.summary is not None
        assert result.summary.risk_level == "LOW"
        assert "clean" in result.summary.summary_text.lower()

    def test_buggy_pr_review_flow(self, buggy_diff_text, mock_pr_metadata):
        """Verify agent loop catches security and logic bugs, posting inline comments."""
        mock_github = GitHubClient(mock_mode=True)
        registry = self._setup_test_registry(mock_github, buggy_diff_text, mock_pr_metadata)

        # Step 1: Agent inspects diff
        resp_step1 = make_mock_completion(
            content="Checking PR diff.",
            tool_calls=[make_mock_tool_call("get_pr_diff", {}, "call_1")],
        )

        # Step 2: Agent identifies SQL injection and off-by-one bug, posting inline comments
        resp_step2 = make_mock_completion(
            content="Found SQL injection and loop bounds issue.",
            tool_calls=[
                make_mock_tool_call(
                    "post_inline_comment",
                    {
                        "file": "src/auth_service.py",
                        "line": 10,
                        "comment": "🚨 SQL Injection: Unsanitized formatted query string.",
                    },
                    "call_2",
                ),
                make_mock_tool_call(
                    "post_inline_comment",
                    {
                        "file": "src/auth_service.py",
                        "line": 23,
                        "comment": "🚨 Off-by-one IndexError: `range(len(tokens) + 1)` will raise IndexError.",
                    },
                    "call_3",
                ),
            ],
        )

        # Step 3: Agent finishes with HIGH risk summary
        resp_step3 = make_mock_completion(
            content="Submitting final review.",
            tool_calls=[
                make_mock_tool_call(
                    "post_summary_comment",
                    {
                        "summary_text": "Critical security and index bounds bugs detected.",
                        "risk_level": "HIGH",
                        "checklist": ["Fix SQL injection", "Fix loop index upper bound"],
                    },
                    "call_4",
                )
            ],
        )

        mock_groq = MagicMock(spec=GroqClient)
        mock_groq.generate_completion.side_effect = [resp_step1, resp_step2, resp_step3]

        loop = AgentLoop(
            groq_client=mock_groq,
            github_client=mock_github,
            tool_registry=registry,
            pr_number=mock_pr_metadata.number,
            repository="octocat/Hello-World",
            max_tool_calls=10,
        )

        result = loop.run()

        assert result.completed_normally is True
        assert result.total_steps == 3
        assert len(result.inline_comments) == 2
        assert result.inline_comments[0].path == "src/auth_service.py"
        assert result.summary is not None
        assert result.summary.risk_level == "HIGH"

    def test_max_tool_calls_safeguard(self, buggy_diff_text, mock_pr_metadata):
        """Verify the loop enforces the max_tool_calls limit to prevent infinite loops."""
        mock_github = GitHubClient(mock_mode=True)
        registry = self._setup_test_registry(mock_github, buggy_diff_text, mock_pr_metadata)

        # Always return diff request without terminating
        repeating_resp = make_mock_completion(
            content="Still analyzing...",
            tool_calls=[make_mock_tool_call("get_pr_diff", {}, "call_loop")],
        )

        mock_groq = MagicMock(spec=GroqClient)
        mock_groq.generate_completion.return_value = repeating_resp

        max_limit = 3
        loop = AgentLoop(
            groq_client=mock_groq,
            github_client=mock_github,
            tool_registry=registry,
            pr_number=mock_pr_metadata.number,
            repository="octocat/Hello-World",
            max_tool_calls=max_limit,
        )

        result = loop.run()

        assert result.total_steps == max_limit
        assert result.summary is not None
        assert (
            "execution limit" in result.summary.summary_text.lower()
            or "partial" in result.summary.summary_text.lower()
        )

    def test_error_resilience_on_llm_failure(self, mock_pr_metadata):
        """Verify agent handles unexpected LLM failures without crashing."""
        mock_github = GitHubClient(mock_mode=True)
        registry = self._setup_test_registry(mock_github, "empty diff", mock_pr_metadata)

        mock_groq = MagicMock(spec=GroqClient)
        mock_groq.generate_completion.side_effect = ConnectionError("Groq Gateway Timeout 504")

        loop = AgentLoop(
            groq_client=mock_groq,
            github_client=mock_github,
            tool_registry=registry,
            pr_number=mock_pr_metadata.number,
            repository="octocat/Hello-World",
            max_tool_calls=5,
        )

        result = loop.run()
        assert result.completed_normally is False
        assert "Groq Gateway Timeout" in (result.error_message or "")
        assert result.summary is not None

    def test_duplicate_tool_call_cycle_detection(self, mock_pr_metadata):
        """Verify duplicate tool calls with identical arguments are detected and deduplicated."""
        mock_github = GitHubClient(mock_mode=True)
        registry = self._setup_test_registry(mock_github, "empty diff", mock_pr_metadata)

        # Agent calls get_pr_diff 3 times in a row with identical args
        call1 = make_mock_tool_call("get_pr_diff", {}, "c1")
        call2 = make_mock_tool_call("get_pr_diff", {}, "c2")
        call3 = make_mock_tool_call("get_pr_diff", {}, "c3")
        finish_call = make_mock_tool_call(
            "post_summary_comment", {"summary_text": "Done", "risk_level": "LOW"}, "c4"
        )

        resp1 = make_mock_completion(content="Checking diff", tool_calls=[call1, call2, call3])
        resp2 = make_mock_completion(content="Wrapping up", tool_calls=[finish_call])

        mock_groq = MagicMock(spec=GroqClient)
        mock_groq.generate_completion.side_effect = [resp1, resp2]

        loop = AgentLoop(
            groq_client=mock_groq,
            github_client=mock_github,
            tool_registry=registry,
            pr_number=mock_pr_metadata.number,
            repository="octocat/Hello-World",
            max_tool_calls=10,
        )

        result = loop.run()
        assert result.completed_normally is True
        assert result.summary is not None
        assert result.summary.risk_level == "LOW"

    def test_malformed_tool_call_arguments_handled_gracefully(self, mock_pr_metadata):
        """Verify malformed JSON strings in function arguments are safely caught."""
        mock_github = GitHubClient(mock_mode=True)
        registry = self._setup_test_registry(mock_github, "empty diff", mock_pr_metadata)

        # Create tool call with invalid JSON string
        malformed_tc = MagicMock()
        malformed_tc.id = "bad_call_1"
        malformed_tc.function.name = "post_inline_comment"
        malformed_tc.function.arguments = "{ invalid_json: "

        finish_call = make_mock_tool_call(
            "post_summary_comment", {"summary_text": "Done", "risk_level": "LOW"}, "c2"
        )

        resp1 = make_mock_completion(content="Testing bad args", tool_calls=[malformed_tc])
        resp2 = make_mock_completion(content="Recovered", tool_calls=[finish_call])

        mock_groq = MagicMock(spec=GroqClient)
        mock_groq.generate_completion.side_effect = [resp1, resp2]

        loop = AgentLoop(
            groq_client=mock_groq,
            github_client=mock_github,
            tool_registry=registry,
            pr_number=mock_pr_metadata.number,
            repository="octocat/Hello-World",
            max_tool_calls=10,
        )

        result = loop.run()
        assert result.completed_normally is True
        assert result.summary is not None

"""Agent Orchestration Loop with tool dispatching, safeguards, and structured tracing."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from src.agent.groq_client import GroqClient
from src.agent.logger import AgentExecutionTracer
from src.agent.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from src.github_client.client import GitHubClient
from src.github_client.models import InlineComment, ReviewSummary
from src.tools.registry import ToolRegistry
from src.tools.summary_comment import PostSummaryCommentTool

logger = logging.getLogger("PRReviewBot.AgentLoop")


class AgentReviewResult(BaseModel):
    """The structured outcome of an autonomous agent review session."""

    pr_number: int
    repository: str
    total_steps: int
    tool_calls_count: int
    inline_comments: list[InlineComment] = Field(default_factory=list)
    summary: ReviewSummary | None = None
    execution_trace_json: str = "[]"
    completed_normally: bool = True
    error_message: str | None = None


class AgentLoop:
    """Orchestrates the multi-step agent reasoning loop.

    Key Engineering Safeguards:
    1. Hard step limit (`max_tool_calls`): Prevents runaway agent loops and unexpected API bills.
    2. Graceful degradation: If LLM fails mid-loop, accumulated findings are still submitted.
    3. Proper OpenAI/Groq tool-call message protocol: Accurately tracks assistant and tool roles.
    """

    def __init__(
        self,
        groq_client: GroqClient,
        github_client: GitHubClient,
        tool_registry: ToolRegistry,
        pr_number: int,
        repository: str,
        max_tool_calls: int = 15,
        model: str = "llama-3.3-70b-versatile",
    ):
        self.groq_client = groq_client
        self.github_client = github_client
        self.tool_registry = tool_registry
        self.pr_number = pr_number
        self.repository = repository
        self.max_tool_calls = max_tool_calls
        self.model = model
        self.tracer = AgentExecutionTracer()

    def run(self) -> AgentReviewResult:
        """Execute the agent review loop until completion or safeguard limit."""
        logger.info(
            "Starting PR review agent loop for PR #%d on %s (max tool calls=%d, model=%s)",
            self.pr_number,
            self.repository,
            self.max_tool_calls,
            self.model,
        )

        user_prompt = USER_PROMPT_TEMPLATE.format(
            pr_number=self.pr_number,
            repository=self.repository,
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        step_count = 0
        total_tool_calls = 0
        summary_posted = False
        error_msg: str | None = None

        tools_schema = self.tool_registry.get_groq_tools_schema()

        recent_tool_invocations: list[tuple[str, str]] = []

        while step_count < self.max_tool_calls and total_tool_calls < self.max_tool_calls:
            step_count += 1
            step_trace = self.tracer.start_step(step_count)
            logger.debug("--- Starting Agent Step %d ---", step_count)

            try:
                response = self.groq_client.generate_completion(
                    messages=messages,
                    tools=tools_schema,
                    model=self.model,
                )
            except Exception as err:
                logger.error("LLM generation failed on step %d: %s", step_count, err)
                error_msg = f"LLM API Error during review step {step_count}: {err}"
                break

            choice = response.choices[0]
            message = choice.message
            thought_text = message.content or ""

            if thought_text:
                self.tracer.log_thought(step_trace, thought_text)

            # Check if model invoked tool calls
            tool_calls = getattr(message, "tool_calls", None)

            if not tool_calls:
                # No more tools requested, model finished its reasoning
                logger.info("Agent concluded tool calls at step %d.", step_count)
                messages.append({"role": "assistant", "content": thought_text})
                break

            # Append the assistant message with tool calls to conversation history
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": thought_text,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            }
            messages.append(assistant_msg)

            # Execute each requested tool call
            for tc in tool_calls:
                total_tool_calls += 1
                tool_name = tc.function.name
                raw_args = tc.function.arguments

                try:
                    args_dict = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args_dict = {}

                # Cycle detection: check for repeated identical tool calls
                call_sig = (tool_name, json.dumps(args_dict, sort_keys=True))
                if recent_tool_invocations.count(call_sig) >= 2:
                    logger.warning(
                        "Duplicate tool invocation detected for %s. Skipping redundant call.",
                        tool_name,
                    )
                    result_content = "Notice: This exact tool call was already executed previously with identical arguments."
                    result_success = True
                else:
                    recent_tool_invocations.append(call_sig)
                    self.tracer.log_tool_call(step_trace, tool_name, args_dict)

                    # Execute tool via registry
                    result = self.tool_registry.execute(tool_name, args_dict)
                    result_content = result.to_content()
                    result_success = result.success

                self.tracer.log_tool_result(
                    step_trace,
                    tool_name,
                    result_content,
                    result_success,
                )

                # Append tool response message
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tool_name,
                        "content": result_content,
                    }
                )

                if tool_name == "post_summary_comment" and result_success:
                    summary_posted = True

            # If summary was posted, we can terminate gracefully
            if summary_posted:
                logger.info("Summary comment posted. Review loop complete.")
                break

            if total_tool_calls >= self.max_tool_calls:
                logger.warning(
                    "Agent reached maximum total tool calls ceiling (%d calls). Triggering safeguard summary.",
                    self.max_tool_calls,
                )
                break

        # Check for max tool call safeguard trigger
        if (
            step_count >= self.max_tool_calls or total_tool_calls >= self.max_tool_calls
        ) and not summary_posted:
            logger.warning(
                "Agent reached maximum tool call ceiling (%d steps / %d calls). Triggering safeguard summary.",
                step_count,
                total_tool_calls,
            )
            self._handle_max_calls_fallback()

        # Retrieve staged comments and recorded summary
        inline_tool = self.tool_registry.get_tool("post_inline_comment")
        summary_tool = self.tool_registry.get_tool("post_summary_comment")

        staged_comments = getattr(inline_tool, "staged_comments", []) if inline_tool else []
        recorded_summary = getattr(summary_tool, "recorded_summary", None) if summary_tool else None

        if not recorded_summary:
            # Generate a minimal fallback summary if none was generated
            recorded_summary = ReviewSummary(
                risk_level="MEDIUM" if staged_comments else "LOW",
                summary_text=(
                    f"Review completed with {len(staged_comments)} findings. "
                    + (f"Note: {error_msg}" if error_msg else "")
                ),
                checklist=["Verify all inline comments before merging."],
            )

        return AgentReviewResult(
            pr_number=self.pr_number,
            repository=self.repository,
            total_steps=step_count,
            tool_calls_count=total_tool_calls,
            inline_comments=staged_comments,
            summary=recorded_summary,
            execution_trace_json=self.tracer.get_full_trace_json(),
            completed_normally=(error_msg is None),
            error_message=error_msg,
        )

    def _handle_max_calls_fallback(self) -> None:
        """Safeguard: Ensure a summary exists when max budget is reached."""
        summary_tool = self.tool_registry.get_tool("post_summary_comment")
        if isinstance(summary_tool, PostSummaryCommentTool) and not summary_tool.recorded_summary:
            summary_tool.execute(
                summary_text=(
                    "> ⚠️ **Notice**: The review agent reached its configured execution limit "
                    f"({self.max_tool_calls} steps). Partial findings have been captured below."
                ),
                risk_level="MEDIUM",
                checklist=[
                    "Review partial inline comments",
                    "Perform manual review for remaining files",
                ],
            )

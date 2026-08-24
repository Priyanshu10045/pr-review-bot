"""Structured execution tracer and console logger for the Agent Loop."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

console = Console()
logger = logging.getLogger("PRReviewBot.AgentTrace")


class AgentStepTrace:
    """Represents a single step in the agent's execution trace."""

    def __init__(self, step_number: int):
        self.step_number = step_number
        self.thought: str | None = None
        self.tool_calls: list[dict[str, Any]] = []
        self.tool_results: list[dict[str, Any]] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step_number,
            "thought": self.thought,
            "tool_calls": self.tool_calls,
            "tool_results": self.tool_results,
        }


class AgentExecutionTracer:
    """Collects and displays structured reasoning traces of agent tool executions."""

    def __init__(self):
        self.steps: list[AgentStepTrace] = []

    def start_step(self, step_number: int) -> AgentStepTrace:
        step = AgentStepTrace(step_number)
        self.steps.append(step)
        return step

    def log_thought(self, step: AgentStepTrace, thought: str) -> None:
        step.thought = thought
        if thought.strip():
            console.print(
                Panel(thought, title=f"🧠 Step {step.step_number} Reasoning", border_style="blue")
            )

    def log_tool_call(
        self, step: AgentStepTrace, tool_name: str, arguments: dict[str, Any]
    ) -> None:
        step.tool_calls.append({"name": tool_name, "arguments": arguments})
        args_json = json.dumps(arguments, indent=2)
        console.print(
            Panel(
                Syntax(args_json, "json", theme="monokai"),
                title=f"🛠️ [bold cyan]Tool Call: {tool_name}[/bold cyan] (Step {step.step_number})",
                border_style="cyan",
            )
        )

    def log_tool_result(
        self, step: AgentStepTrace, tool_name: str, result_preview: str, success: bool
    ) -> None:
        step.tool_results.append(
            {"name": tool_name, "preview": result_preview[:300], "success": success}
        )
        status_color = "green" if success else "red"
        status_symbol = "✅" if success else "❌"
        console.print(
            f"[{status_color}]{status_symbol} Tool '{tool_name}' result:[/ {status_color}] {result_preview[:200]}..."
        )

    def print_summary_table(self) -> None:
        """Render an execution trace summary table in the terminal."""
        table = Table(
            title="Agent Execution Trace Summary", show_header=True, header_style="bold magenta"
        )
        table.add_column("Step", style="dim", width=6)
        table.add_column("Tools Called", style="cyan")
        table.add_column("Result Status", style="green")

        for step in self.steps:
            tools = ", ".join([tc["name"] for tc in step.tool_calls]) or "None (Final Response)"
            statuses = (
                ", ".join(["OK" if tr["success"] else "FAIL" for tr in step.tool_results]) or "Done"
            )
            table.add_row(str(step.step_number), tools, statuses)

        console.print(table)

    def get_full_trace_json(self) -> str:
        """Export the full reasoning trace as a JSON string."""
        return json.dumps([step.to_dict() for step in self.steps], indent=2)

"""Registry for managing, inspecting, and executing agent tools."""

from __future__ import annotations

import logging
from typing import Any

from src.tools.base import BaseTool, ToolResult

logger = logging.getLogger("PRReviewBot.ToolRegistry")


class ToolRegistry:
    """Central registry storing available agent tools and formatting them for Groq API."""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool instance."""
        if tool.name in self._tools:
            logger.warning("Overwriting existing tool '%s' in registry.", tool.name)
        self._tools[tool.name] = tool
        logger.debug("Registered tool: %s", tool.name)

    def get_tool(self, name: str) -> BaseTool | None:
        """Retrieve a tool by name."""
        return self._tools.get(name)

    def get_groq_tools_schema(self) -> list[dict[str, Any]]:
        """Generate the list of JSON tool schemas formatted for Groq / OpenAI function calling."""
        return [tool.to_groq_schema() for tool in self._tools.values()]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute a tool by name with the given argument dictionary."""
        tool = self.get_tool(tool_name)
        if not tool:
            err_msg = f"Tool '{tool_name}' is not registered. Available tools: {list(self._tools.keys())}"
            logger.error(err_msg)
            return ToolResult(success=False, error=err_msg)

        try:
            logger.info("Executing tool '%s' with arguments: %s", tool_name, arguments)
            result = tool.execute(**arguments)
            logger.debug("Tool '%s' completed with success=%s", tool_name, result.success)
            return result
        except TypeError as err:
            err_msg = f"Invalid arguments for tool '{tool_name}': {err}"
            logger.error(err_msg)
            return ToolResult(success=False, error=err_msg)
        except Exception as err:
            err_msg = f"Execution failure in tool '{tool_name}': {err}"
            logger.exception(err_msg)
            return ToolResult(success=False, error=err_msg)

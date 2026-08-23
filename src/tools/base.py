"""Base definitions for agent tools and execution results."""

from __future__ import annotations

import abc
import json
from typing import Any


class ToolResult:
    """Encapsulates the execution result of an agent tool."""

    def __init__(self, success: bool, data: Any = None, error: str | None = None):
        self.success = success
        self.data = data
        self.error = error

    def to_content(self) -> str:
        """Serialize result to a string suitable for LLM tool response messages."""
        if not self.success:
            return f"Error: {self.error or 'Unknown tool execution error'}"
        if isinstance(self.data, str):
            return self.data
        if isinstance(self.data, dict | list):
            return json.dumps(self.data, indent=2)
        return str(self.data)

    def __repr__(self) -> str:
        return f"<ToolResult success={self.success} data_preview={str(self.data)[:50]}>"


class BaseTool(abc.ABC):
    """Abstract Base Class for all AI agent tools.

    Why this design?
    - Strict JSON Schema definitions ensure compatibility with Groq/OpenAI tool-calling specifications.
    - Uniform interface decouples tool business logic from the agent orchestration loop.
    - Enables easy mock injection during unit tests.
    """

    name: str
    description: str
    parameters_schema: dict[str, Any]

    @abc.abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        """Execute the tool with the provided arguments and return a ToolResult."""
        pass

    def to_groq_schema(self) -> dict[str, Any]:
        """Convert the tool definition into OpenAI/Groq function calling JSON schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }

"""Tool: search_codebase - searches repository for keywords, function definitions, or callers."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from src.tools.base import BaseTool, ToolResult


class SearchCodebaseTool(BaseTool):
    """Tool that searches across the repository using ripgrep or Python regex.

    Why this tool?
    - Enables agent to verify if a modified function is called elsewhere (cross-file impact).
    - Checks whether renamed parameters or changed signatures break callers.
    """

    name = "search_codebase"
    description = (
        "Searches the repository codebase for a query string or regex pattern. "
        "Returns matching files and line numbers with matching code snippets. "
        "Use this to check if a modified/deleted function or variable is used elsewhere in the project."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The exact string or regex pattern to search for across the repository.",
            },
            "file_pattern": {
                "type": "string",
                "description": "Optional glob filter for file paths (e.g., '*.py', 'src/*').",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of match occurrences to return. Default is 25.",
                "default": 25,
            },
        },
        "required": ["query"],
    }

    # Directories to always skip during search
    IGNORED_DIRS = {
        ".git",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        ".pytest_cache",
        ".ruff_cache",
    }

    def __init__(self, repo_root: Path | None = None):
        self.repo_root = Path(repo_root or Path.cwd()).resolve()

    def execute(
        self,
        query: str,
        file_pattern: str | None = None,
        max_results: int = 25,
        **kwargs,
    ) -> ToolResult:
        """Execute search via ripgrep if available, or fallback to Python traversal."""
        if not query or not query.strip():
            return ToolResult(success=False, error="Search query cannot be empty.")

        # Try ripgrep first if available on PATH
        rg_path = shutil.which("rg")
        if rg_path and self.repo_root.is_dir():
            try:
                cmd = [
                    rg_path,
                    "--line-number",
                    "--no-heading",
                    "--color=never",
                    "-m",
                    str(max_results),
                ]
                if file_pattern:
                    cmd.extend(["-g", file_pattern])
                cmd.extend(["-e", query, str(self.repo_root)])

                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout:
                    results = []
                    for line in proc.stdout.splitlines()[:max_results]:
                        # Make path relative to repo_root for clean display
                        clean_line = line.replace(str(self.repo_root) + os.sep, "")
                        results.append(clean_line)
                    return ToolResult(success=True, data="\n".join(results))
                elif proc.returncode == 1:
                    return ToolResult(
                        success=True,
                        data=f"No matches found for query '{query}' in the codebase.",
                    )
            except Exception:
                # Fallback to Python search
                pass

        # Pure Python fallback
        return self._python_search(query, file_pattern, max_results)

    def _python_search(
        self,
        query: str,
        file_pattern: str | None,
        max_results: int,
    ) -> ToolResult:
        """Walk repo_root and perform regex/substring matching."""
        if not self.repo_root.is_dir():
            return ToolResult(
                success=False,
                error=f"Repository root '{self.repo_root}' is not a valid directory.",
            )

        matches: list[str] = []
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error:
            # If invalid regex, fall back to literal substring match
            pattern = None

        count = 0
        for path in self.repo_root.rglob("*"):
            if count >= max_results:
                break
            if any(part in self.IGNORED_DIRS for part in path.parts):
                continue
            if not path.is_file():
                continue
            if file_pattern and not path.match(file_pattern):
                continue

            try:
                # Skip binary files
                content = path.read_text(encoding="utf-8", errors="ignore")
                rel_path = path.relative_to(self.repo_root)
                for line_idx, line in enumerate(content.splitlines(), start=1):
                    is_match = False
                    if pattern and pattern.search(line):
                        is_match = True
                    elif query.lower() in line.lower():
                        is_match = True

                    if is_match:
                        matches.append(f"{rel_path}:{line_idx}: {line.strip()}")
                        count += 1
                        if count >= max_results:
                            break
            except Exception:
                continue

        if not matches:
            return ToolResult(
                success=True,
                data=f"No occurrences of '{query}' found in repository.",
            )

        return ToolResult(success=True, data="\n".join(matches))
